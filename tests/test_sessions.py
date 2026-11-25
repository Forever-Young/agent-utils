from argparse import Namespace
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch
from agent_utils.cli import main,atomic_write
from agent_utils.agterm.sessions import launch,records,task_project,command


class SessionTests(unittest.TestCase):
    def test_launch_failure_has_no_record_and_launch_binds_claude(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'XDG_STATE_HOME':d,'XDG_CONFIG_HOME':d}),patch('shutil.which',return_value='/bin/claude'):
            ag=Mock(dry=False);ag.launch.side_effect=OSError('failed')
            args=Namespace(session=None,prompt=None,command='new',task=None)
            project={'id':'p','name':'Demo','roots':[d]}
            with self.assertRaises(OSError):launch(ag,{'name':'Team'},project,'claude',args)
            self.assertEqual(records(),{})
            ag.launch.side_effect=None;ag.launch.return_value='row'
            record=launch(ag,{'name':'Team'},project,'claude',args)
            self.assertEqual(records()[record['id']]['terminal_id'],'row')
            argv=ag.launch.call_args.args[2]
            self.assertEqual(argv[argv.index('--session-id')+1],record['conversation_id'])
    def test_lifecycle_keeps_provider_history(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'XDG_STATE_HOME':d,'XDG_CONFIG_HOME':d}):
            from agent_utils.agterm.sessions import save
            save({'id':'r','project':'p','window':'Team','conversation_id':None,'updated_at':'today'})
            main(['session','archive','--record','r'])
            self.assertTrue(records()['r']['archived'])
            main(['session','unarchive','--record','r'])
            self.assertFalse(records()['r']['archived'])
            main(['session','forget','--record','r','--yes'])
            self.assertEqual(records(),{})
    def test_worktree_task_does_not_edit_primary_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d)/'repo';repo.mkdir()
            subprocess.run(['git','init','-q',str(repo)],check=True)
            subprocess.run(['git','-C',str(repo),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-q','--allow-empty','-m','Initial'],check=True)
            project={'id':'p','name':'Demo','roots':[str(repo)]}
            args=Namespace(task='issue-1',in_place=False,repo=[],destination=str(Path(d)/'tasks'),base='HEAD')
            result=task_project(project,args,False)
            target=Path(result['roots'][0])
            (target/'new.txt').write_text('isolated')
            self.assertFalse((repo/'new.txt').exists())
            self.assertEqual(project['roots'],[str(repo)])
            with self.assertRaises(ValueError):task_project(project,args,False)
    def test_codex_unknown_id_cannot_resume_arbitrary_history(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'XDG_STATE_HOME':d,'XDG_CONFIG_HOME':d}):
            from agent_utils.agterm.sessions import save
            save({'id':'r','project':'p','window':'Team','terminal_id':'gone','conversation_id':None})
            with patch('agent_utils.agterm.sessions.live_rows',return_value=[]):
                with self.assertRaisesRegex(ValueError,'not bound'):
                    main(['session','resume','--record','r','--backend-stopped'])

    def test_decoration_resolves_window_id(self):
        from agent_utils.agterm.sessions import decorate
        ag=Mock();ag.window.return_value='actual-window-id'
        decorate(ag,{'terminal_id':'row','window':'Team','agent':'claude','title':'Task','id':'record'})
        self.assertEqual(ag.call.call_args_list[0].args,('session','tint','orange','--target','row','--window','actual-window-id'))

    def test_result_survives_notification_failure(self):
        from agent_utils.agterm.sessions import save
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'XDG_STATE_HOME':d,'XDG_CONFIG_HOME':d}):
            save({'id':'r','project':'p','window':'Team','terminal_id':'row','title':'Task','updated_at':'today'})
            with patch('agent_utils.agterm.sessions.live_rows',return_value=[{'id':'row','window_id':'wid'}]),patch('agent_utils.cli.Agterm') as adapter:
                adapter.return_value.dry=False
                adapter.return_value.call.side_effect=OSError('socket failed')
                with self.assertRaises(OSError):main(['session','finish','--record','r','--result','Done with checks'])
            self.assertEqual(records()['r']['result'],'Done with checks')

    def test_provider_delete_requires_stopped_and_exact_uuid(self):
        from agent_utils.agterm.sessions import save
        import uuid
        conversation=str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'XDG_STATE_HOME':d,'XDG_CONFIG_HOME':d}):
            save({'id':'r','project':'p','window':'Team','terminal_id':None,'agent':'codex','conversation_id':conversation,'roots':[d]})
            with patch('agent_utils.agterm.sessions.live_rows',return_value=[]),patch('subprocess.run') as run:
                with self.assertRaises(ValueError):main(['session','provider-delete','--record','r','--yes'])
                run.assert_not_called()
                main(['session','provider-delete','--record','r','--yes','--backend-stopped'])
                self.assertEqual(run.call_args.args[0],['codex','delete',conversation,'--force'])
            self.assertTrue(records()['r']['provider_deleted'])
