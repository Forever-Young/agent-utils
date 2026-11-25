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
