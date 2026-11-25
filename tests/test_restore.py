import os
import tempfile
import unittest
from unittest.mock import Mock,patch
from agent_utils.agterm import restore
from agent_utils.agterm.sessions import save
from agent_utils.agterm.startup import restart_pinned

CONV='11111111-1111-4111-8111-111111111111'


def record(**extra):
    base={'id':'rec1','project':'p','project_name':'Demo','roots':['/tmp'],'agent':'claude','title':'Demo',
          'conversation_id':CONV,'terminal_id':'T1','cwd':'/tmp','hidden':False,'archived':False}
    return {**base,**extra}


def agterm(rows):
    ag=Mock(dry=False)
    def call(*args,json_result=False):
        if args[:2]==('window','list'):return [{'id':'W','name':'Team'}]
        if args[0]=='tree':return {'workspaces':[{'name':'Demo','sessions':rows}]}
    ag.call.side_effect=call
    return ag


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup)
        patcher=patch.dict(os.environ,{'XDG_STATE_HOME':self.dir.name,'XDG_CONFIG_HOME':self.dir.name});patcher.start();self.addCleanup(patcher.stop)
        which=patch('shutil.which',return_value='/bin/claude');which.start();self.addCleanup(which.stop)

    def test_pin_resumes_exact_conversation_in_saved_directory(self):
        line=restore.pin_line(record())
        self.assertTrue(line.startswith('cd /tmp && env AGTERM_UTILS_PLAIN_SHELL=1 env AGTERM_UTILS_RECORD=rec1 '))
        self.assertIn('--resume '+CONV,line)

    def test_no_pin_without_conversation_or_when_archived(self):
        self.assertIsNone(restore.pin_line(record(conversation_id=None)))
        self.assertIsNone(restore.pin_line(record(archived=True)))
        self.assertIsNone(restore.pin_line(record(terminal_id=None)))

    def test_sync_pins_clears_and_keeps_foreign_pins(self):
        save(record());save(record(id='rec2',terminal_id='T2',archived=True))
        save(record(id='rec3',terminal_id='T3'))
        rows=[{'id':'T1','name':'a'},
              {'id':'T2','name':'b','restoreCommand':'x AGTERM_UTILS_RECORD=rec2 y'},
              {'id':'T3','name':'c','restoreCommand':'my own pin'}]
        ag=agterm(rows)
        with patch.object(restore,'_alive',return_value=True):
            result=dict((rid,what) for what,rid in restore.sync(ag))
        self.assertEqual(result,{'rec1':'pinned','rec2':'cleared','rec3':'kept-foreign-pin'})
        restored=[c.args for c in ag.call.call_args_list if c.args[:2]==('session','restore')]
        self.assertEqual(len(restored),2)
        self.assertIn('--clear',restored[1])

    def test_sync_is_idempotent(self):
        save(record())
        line=restore.pin_line(record())
        ag=agterm([{'id':'T1','name':'a','restoreCommand':line}])
        self.assertEqual(restore.sync(ag),[])

    def test_snapshot_plan_reports_rename_missing_and_unpinned(self):
        save(record())
        restore.write_snapshot(agterm([{'id':'T1','name':'old','restoreCommand':'AGTERM_UTILS_RECORD=rec1'},{'id':'T9','name':'gone'}]))
        report=restore.plan(agterm([{'id':'T1','name':'new'}]))
        kinds=sorted(k for k,_ in report)
        self.assertEqual(kinds,['missing','renamed','unpinned'])
        restore.write_snapshot(agterm([{'id':'T1','name':'new'}]))
        restore.write_snapshot(agterm([{'id':'T1','name':'new'}]))
        self.assertEqual(len(list((restore.state_dir()).glob('restore-snapshot.json.*.bak'))),2)

    def test_plan_requires_snapshot(self):
        with self.assertRaises(ValueError):restore.plan(agterm([]))

    def test_startup_menu_skipped_for_pinned_restored_pane(self):
        tree={'workspaces':[{'sessions':[{'id':'T1','restoreCommand':'x AGTERM_UTILS_RECORD=r'},{'id':'T2'}]}]}
        self.assertTrue(restart_pinned(tree,'T1'))
        self.assertFalse(restart_pinned(tree,'T2'))
        self.assertFalse(restart_pinned({},'T1'))

    def test_kiro_conversation_discovered_from_lease_of_descendant_process(self):
        from pathlib import Path
        home=Path(self.dir.name);leases=home/'.kiro/session-leases';leases.mkdir(parents=True)
        sid='sess_'+CONV
        (leases/(sid+'.lease')).write_text('{"pid": 30, "parentPid": 20}')
        (leases/'sess_22222222-2222-4222-8222-222222222222.lease').write_text('{"pid": 99, "parentPid": 98}')
        rec=record(agent='kiro-cli',conversation_id=None,provider_pid=10,provider_birth='b')
        table={30:20,20:10,99:98,98:1}
        with patch('agent_utils.agterm.workers.process_birth',return_value='b'):
            self.assertEqual(restore.discover(rec,home,table),sid)
            (leases/'sess_33333333-3333-4333-8333-333333333333.lease').write_text('{"pid": 31, "parentPid": 20}')
            table[31]=20
            self.assertIsNone(restore.discover(rec,home,table))
        with patch('agent_utils.agterm.workers.process_birth',return_value='other'):
            self.assertIsNone(restore.discover(rec,home,{30:10}))

    def test_bind_discovered_never_reassigns(self):
        save(record(agent='kiro-cli',conversation_id=None));save(record(id='rec2',terminal_id='T2',conversation_id=CONV))
        with patch.object(restore,'discover',return_value='sess_'+CONV),patch.object(restore,'_alive',return_value=True):
            self.assertEqual(restore.bind_discovered({'T1':{},'T2':{}}),['rec1'])
        from agent_utils.agterm.sessions import records
        self.assertEqual(records()['rec2']['conversation_id'],CONV)

    def test_conversation_held_by_dead_record_can_be_rebound_to_live_pane(self):
        save(record(agent='kiro-cli',conversation_id=None));save(record(id='old',terminal_id='T0',conversation_id='sess_'+CONV))
        with patch.object(restore,'discover',return_value='sess_'+CONV),patch.object(restore,'_alive',side_effect=lambda r:r['id']=='rec1'):
            self.assertEqual(restore.bind_discovered({'T1':{}}),['rec1'])


if __name__=='__main__':unittest.main()


class PinCommandTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup)
        p=patch.dict(os.environ,{'XDG_STATE_HOME':self.dir.name,'XDG_CONFIG_HOME':self.dir.name});p.start();self.addCleanup(p.stop)
    def test_pin_adopts_unregistered_session_with_exact_conversation(self):
        from argparse import Namespace
        from agent_utils.agterm.sessions import records
        ag=agterm([{'id':'T7','name':'◑ my chat','cwd':'/tmp'}])
        args=Namespace(target='T7',agent='kiro-cli',conversation='sess_'+CONV)
        with patch('shutil.which',return_value='/bin/kiro-cli'):restore.pin_session(ag,args,None)
        record=next(iter(records().values()))
        self.assertEqual((record['terminal_id'],record['conversation_id'],record['title']),('T7','sess_'+CONV,'my chat'))
        pinned=[c.args for c in ag.call.call_args_list if c.args[:2]==('session','restore')][0]
        self.assertIn('--resume-id sess_'+CONV,pinned[2])
    def test_pin_rejects_bad_ids_and_unknown_sessions(self):
        from argparse import Namespace
        ag=agterm([{'id':'T7','name':'x','cwd':'/tmp'}])
        with self.assertRaises(ValueError):restore.pin_session(ag,Namespace(target='nope',agent='codex',conversation=CONV),None)
        with self.assertRaises(ValueError):restore.pin_session(ag,Namespace(target='T7',agent='codex',conversation='not-a-uuid'),None)
    def test_kiro_candidates_match_directory(self):
        from pathlib import Path
        d=Path(self.dir.name)/'.kiro/sessions/h/sess_a';d.mkdir(parents=True)
        (d/'session.json').write_text('{"id":"sess_a","title":"T","workspacePaths":["/w"],"lastModifiedAt":"2026-01-01"}')
        self.assertEqual(restore.kiro_candidates('/w',self.dir.name)[0][1],'sess_a')
        self.assertEqual(restore.kiro_candidates('/other',self.dir.name),[])


class AdoptTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup)
        p=patch.dict(os.environ,{'XDG_STATE_HOME':self.dir.name,'XDG_CONFIG_HOME':self.dir.name});p.start();self.addCleanup(p.stop)

    def test_hand_started_agent_is_adopted_from_its_process(self):
        from agent_utils.agterm.sessions import records
        sid='AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA'
        ps=f"10 1 /opt/homebrew/bin/kiro-cli chat AGTERM_SESSION_ID={sid} X=1\n11 10 /x/bun run AGTERM_SESSION_ID={sid}\n"
        with patch('subprocess.run') as run,patch.object(restore,'discover',return_value='sess_'+CONV),\
             patch('agent_utils.agterm.workers.process_birth',return_value='b'),patch.object(restore,'_process_table',return_value={}):
            run.return_value.stdout=ps
            keys=restore.adopt_unregistered({sid:{'id':sid,'name':'o hand','cwd':'/tmp','workspace_name':'W','window_name':'Team'}})
        self.assertEqual(len(keys),1)
        rec=records()[keys[0]]
        self.assertEqual((rec['agent'],rec['conversation_id'],rec['terminal_id']),('kiro-cli','sess_'+CONV,sid))


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup)
        p=patch.dict(os.environ,{'XDG_STATE_HOME':self.dir.name,'XDG_CONFIG_HOME':self.dir.name});p.start();self.addCleanup(p.stop)
        w=patch('shutil.which',return_value='/bin/claude');w.start();self.addCleanup(w.stop)
    def test_replayed_launch_resumes_recorded_conversation_but_first_start_does_not(self):
        self.addCleanup(os.chdir,os.getcwd())
        from agent_utils.agterm.runner import replay_command
        save(record(status='launching'))
        first=['/bin/claude','--session-id',CONV]
        self.assertEqual(replay_command('rec1',first),first)
        save(record(status='running'))
        again=replay_command('rec1',first)
        self.assertIn('--resume',again);self.assertIn(CONV,again);self.assertNotIn('--session-id',again)
        self.assertEqual(os.getcwd(),os.path.realpath('/tmp'))
        other=['python3','-c','pass']
        self.assertEqual(replay_command('rec1',other),other)
        self.assertEqual(replay_command('missing',first),first)


class EndedTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup)
        p=patch.dict(os.environ,{'XDG_STATE_HOME':self.dir.name,'XDG_CONFIG_HOME':self.dir.name});p.start();self.addCleanup(p.stop)
    def test_ended_needs_two_sightings_and_resets_when_agent_runs(self):
        save(record(exited_at='x'))
        shell={'id':'T1','foreground':[],'realized':True}
        self.assertFalse(restore.ended(record(exited_at='x'),shell,set(),clock=lambda:100))
        self.assertFalse(restore.ended(record(exited_at='x'),shell,set(),clock=lambda:130))
        self.assertTrue(restore.ended(record(exited_at='x'),shell,set(),clock=lambda:161))
        running={'id':'T1','foreground':['/x/python','/y/runner.py','rec1'],'realized':True}
        self.assertFalse(restore.ended(record(exited_at='x'),running,set(),clock=lambda:200))
        self.assertFalse(restore.ended(record(exited_at='x'),shell,set(),clock=lambda:210))
        with patch.object(restore,'_alive',return_value=True):
            self.assertFalse(restore.ended(record(exited_at=None),shell,set(),clock=lambda:400))
        self.assertFalse(restore.ended(record(exited_at='x'),shell,{'T1'},clock=lambda:999))


class NoRepinTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup)
        p=patch.dict(os.environ,{'XDG_STATE_HOME':self.dir.name,'XDG_CONFIG_HOME':self.dir.name});p.start();self.addCleanup(p.stop)
        w=patch('shutil.which',return_value='/bin/claude');w.start();self.addCleanup(w.stop)
    def test_pane_whose_agent_is_gone_is_not_pinned_again(self):
        save(record())
        ag=agterm([{'id':'T1','name':'zsh','foreground':[]}])
        with patch.object(restore,'_alive',return_value=False):
            self.assertEqual(restore.sync(ag),[])
        self.assertFalse([c for c in ag.call.call_args_list if c.args[:2]==('session','restore')])
