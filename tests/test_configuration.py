import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent_utils.cli import main,load


class ConfigurationTests(unittest.TestCase):
    def test_add_backup_and_targeted_send(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config.json'
            common=['--config',str(path),'project']
            main(common+['add','--project','a','--window','Team','--agent','claude','--root',d])
            with patch('agent_utils.cli.Agterm') as adapter:
                adapter.return_value.call.return_value={'workspaces':[]}
                main(common+['add','--project','b','--window','Team','--root',d,'--send'])
                calls=adapter.return_value.call.call_args_list
                self.assertTrue(any(c.args[:3]==('workspace','new','b') for c in calls))
                self.assertFalse(any(c.args[:3]==('workspace','new','a') for c in calls))
            self.assertEqual(len(list(Path(d).glob('config.json.backup-*'))),1)
            main(common+['order','--project','b','--position','1'])
            self.assertEqual(load(path)['windows'][0]['projects'][0]['id'],'b')
    def test_invalid_edit_leaves_original(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config.json'
            cmd=['--config',str(path),'project']
            main(cmd+['add','--project','a','--window','Team','--agent','codex','--root',d])
            before=path.read_bytes()
            with self.assertRaises(ValueError):main(cmd+['add','--project','b','--name','a','--window','Team','--root',d])
            self.assertEqual(path.read_bytes(),before)

    def test_bootstrap_keeps_empty_configured_windows(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config.json'
            path.write_text(json.dumps({'version':1,'windows':[{'name':'Personal','default_agent':'codex','projects':[]}]}))
            with patch('agent_utils.cli.Agterm') as adapter:
                adapter.return_value.call.return_value={'workspaces':[]}
                main(['--config',str(path),'bootstrap'])
                adapter.return_value.window.assert_called_once_with('Personal')


class RootEditTests(unittest.TestCase):
    def test_add_remove_and_replace_roots(self):
        with tempfile.TemporaryDirectory() as d:
            a,b,c=[str(Path(d,n).resolve()) for n in 'abc']
            path=Path(d)/'config.json'
            cmd=['--config',str(path),'project']
            main(cmd+['add','--project','p','--window','W','--agent','claude','--root',a])
            roots=lambda:load(path)['windows'][0]['projects'][0]['roots']
            main(cmd+['edit','--project','p','--add-root',b,'--add-root',c])
            self.assertEqual(roots(),[a,b,c])
            main(cmd+['edit','--project','p','--remove-root',b])
            self.assertEqual(roots(),[a,c])
            with self.assertRaises(ValueError):main(cmd+['edit','--project','p','--add-root',a])
            with self.assertRaises(ValueError):main(cmd+['edit','--project','p','--remove-root',b])
            with self.assertRaises(ValueError):main(cmd+['edit','--project','p','--remove-root',a,'--remove-root',c])
            with self.assertRaises(ValueError):main(cmd+['edit','--project','p','--root',a,'--add-root',b])
            self.assertEqual(roots(),[a,c])
            main(cmd+['edit','--project','p','--root',b])
            self.assertEqual(roots(),[b])


class FakeAgterm:
    """One window 'Team' whose sidebar order is the class attribute `rows` (name list; ids are 'id-'+name)."""
    rows=[]
    def __init__(self,dry=False,*a):self.dry=dry
    def window(self,name):return 'W1'
    def call(self,*args,json_result=False):
        if args[:2]==('window','list'):return [{'id':'W1','name':'Team'}]
        if args[0]=='tree':return {'tree':{'workspaces':[{'name':n,'id':'id-'+n} for n in FakeAgterm.rows]}}
        if args[:2]==('workspace','move') and not self.dry:
            assert args[args.index('--to')+1]=='up'
            n=args[args.index('--target')+1][3:];r=FakeAgterm.rows;i=r.index(n);r[i-1],r[i]=r[i],r[i-1]


class OrderSyncTests(unittest.TestCase):
    def setUp(self):
        self.d=tempfile.TemporaryDirectory();self.addCleanup(self.d.cleanup)
        self.path=Path(self.d.name)/'config.json'
        self.cmd=['--config',str(self.path),'project']
        for n in 'abc':
            main(self.cmd+['add','--project',n,'--window','Team','--agent','claude','--root',self.d.name])

    def names(self):return [p['id'] for p in load(self.path)['windows'][0]['projects']]

    def test_slot_order_keeps_unmanaged_in_place(self):
        from agent_utils.agterm.configuration import slot_order
        self.assertEqual(slot_order(['x','c','y','a'],['a','b','c']),['x','a','y','c'])

    def test_push_reorders_managed_and_leaves_others(self):
        FakeAgterm.rows=['c','scratch','b','a']
        with patch('agent_utils.cli.Agterm',FakeAgterm):
            main(self.cmd+['push-order'])
        self.assertEqual(FakeAgterm.rows,['a','scratch','b','c'])

    def test_push_dry_run_changes_nothing(self):
        FakeAgterm.rows=['c','b','a']
        with patch('agent_utils.cli.Agterm',FakeAgterm):
            main(['--dry-run']+self.cmd+['push-order'])
        self.assertEqual(FakeAgterm.rows,['c','b','a'])

    def test_pull_reorders_config(self):
        FakeAgterm.rows=['c','scratch','a','b']
        with patch('agent_utils.cli.Agterm',FakeAgterm):
            main(self.cmd+['pull-order'])
        self.assertEqual(self.names(),['c','a','b'])

    def test_order_send_pushes_order(self):
        FakeAgterm.rows=['a','b','c']
        with patch('agent_utils.cli.Agterm',FakeAgterm):
            main(self.cmd+['order','--project','c','--position','1','--send'])
        self.assertEqual(FakeAgterm.rows,['c','a','b'])
