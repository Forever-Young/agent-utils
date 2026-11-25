"""Provider command contracts; never start a model or inspect user history."""
from argparse import Namespace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent_utils.cli import AGENTS, AGENT_NAMES, agent_argv, validate
from agent_utils.agterm.sessions import launch, records, save
from agent_utils.agterm.startup import startup_choices


class ProviderTests(unittest.TestCase):
    def project(self, root='/tmp/demo'):
        return {'id':'demo','name':'Demo','roots':[root]}

    @patch('shutil.which', side_effect=lambda name: '/bin/'+name)
    def test_new_resume_and_prompt_commands(self, which):
        cases=[
            ('kiro-cli',None,False,['chat','hello']),
            ('kiro-cli',None,True,['chat','--resume-picker','hello']),
            ('kiro-cli','session-123',False,['chat','--resume-id','session-123','hello']),
            ('opencode',None,False,['--prompt','hello']),
            ('opencode',None,True,['--continue','--prompt','hello']),
            ('opencode','ses_123',False,['--session','ses_123','--prompt','hello']),
            ('cursor-agent',None,False,['--','hello']),
            ('cursor-agent',None,True,['--resume','--','hello']),
            ('cursor-agent','chat-123',False,['--resume','chat-123','--','hello']),
            ('pi',None,False,['hello']),
            ('pi',None,True,['--resume','hello']),
            ('pi','session-123',False,['--session','session-123','hello']),
            ('qwen',None,False,['--prompt-interactive','hello']),
            ('qwen',None,True,['--resume','--prompt-interactive','hello']),
            ('qwen','session-123',False,['--resume','session-123','--prompt-interactive','hello']),
            ('copilot',None,False,['-i','hello']),
            ('copilot',None,True,['--resume','-i','hello']),
            ('copilot','session-123',False,['--resume=session-123','-i','hello']),
            ('vibe',None,False,['--','hello']),
            ('vibe',None,True,['--resume','--','hello']),
            ('vibe','session-123',False,['--resume','session-123','--','hello']),
            ('agy',None,False,['--prompt-interactive','hello']),
            ('agy',None,True,['--continue','--prompt-interactive','hello']),
            ('agy','conversation-123',False,['--conversation','conversation-123','--prompt-interactive','hello']),
        ]
        for agent,ident,picker,expected in cases:
            with self.subTest(agent=agent,ident=ident,picker=picker):
                argv,cwd=agent_argv(self.project(),agent,ident,'hello',resume_picker=picker)
                self.assertEqual(argv,['/bin/'+agent,*expected])
                self.assertEqual(cwd,'/tmp/demo')

    def test_configuration_and_startup_offer_every_provider(self):
        for agent in AGENTS:
            validate({'version':1,'windows':[{'name':'Demo','default_agent':agent,'projects':[self.project()]}]})
            choices=startup_choices(agent)
            self.assertEqual(choices[0][1],agent)
            for provider in AGENTS:
                self.assertIn((AGENT_NAMES[provider],provider),choices)

    def test_multi_root_and_coordinator_are_not_silently_misconfigured(self):
        for agent in ('kiro-cli','opencode','pi'):
            project=self.project();project['roots'].append('/tmp/extra')
            with self.assertRaisesRegex(ValueError,'one root'):agent_argv(project,agent)
            if agent!='kiro-cli':
                with self.assertRaisesRegex(ValueError,'Coordinator'):agent_argv(self.project(),agent,coordinator=True)

    def test_single_root_agents_choose_one_root_at_startup(self):
        from agent_utils.agterm.startup import choose_single_root
        project=self.project();project['roots']=['/tmp/a','/tmp/b']
        seen=[]
        def pick(prompt,entries):seen.append(prompt);return entries[1][1]
        chosen=choose_single_root(project,'kiro-cli',pick)
        self.assertEqual(chosen['roots'],['/tmp/b'])
        self.assertEqual(project['roots'],['/tmp/a','/tmp/b'])
        agent_argv(chosen,'kiro-cli')
        self.assertIs(choose_single_root(project,'claude',pick),project)
        self.assertIsNone(choose_single_root(project,'pi',lambda *a:None))

    def test_managed_resume_uses_provider_flags_and_binds_native_id(self):
        from agent_utils.cli import main
        for agent,ident,flags in [('kiro-cli','session-123',['chat','--resume-id','session-123']),
                                  ('opencode','ses_123',['--session','ses_123']),
                                  ('cursor-agent','chat-123',['--resume','chat-123']),
                                  ('pi','session-123',['--session','session-123']),
                                  ('agy','conversation-123',['--conversation','conversation-123']),
                                  ('kimi','session-123',['--session','session-123']),
                                  ('qwen','session-123',['--resume','session-123']),
                                  ('copilot','session-123',['--resume=session-123']),
                                  ('vibe','session-123',['--resume','session-123'])]:
            with self.subTest(agent=agent),tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'XDG_STATE_HOME':d,'XDG_CONFIG_HOME':d}),patch('shutil.which',side_effect=lambda x:'/bin/'+x):
                ag=Mock(dry=False);ag.launch.return_value='row'
                record=launch(ag,{'name':'Demo'},self.project(d),agent,
                              Namespace(session=None,prompt=None,command='new',task=None))
                self.assertIsNone(record['conversation_id'])
                main(['session','bind','--record',record['id'],'--conversation',ident])
                self.assertEqual(records()[record['id']]['conversation_id'],ident)
                with patch('agent_utils.agterm.sessions.live_rows',return_value=[]),patch('agent_utils.cli.Agterm',return_value=ag):
                    main(['session','resume','--record',record['id'],'--backend-stopped'])
                self.assertEqual(ag.launch.call_args.args[2][ag.launch.call_args.args[2].index('--')+2:],flags)

    def test_history_discovery_does_not_fall_through_to_codex(self):
        from agent_utils.agterm.history import discover
        for agent in ('kiro-cli','opencode','cursor-agent','pi','agy','kimi','qwen','copilot','vibe'):
            with self.assertRaisesRegex(ValueError,'not supported'):
                discover(self.project(),Namespace(agent=agent))

    def test_opencode_picker_filters_metadata_and_handles_cancel(self):
        import json
        from agent_utils.agterm.history import pick_opencode_session
        rows=[{'id':'ses_own','title':'Demo','directory':'/tmp/demo'},
              {'id':'ses_other','title':'Other','directory':'/tmp/elsewhere'}]
        with patch('agent_utils.agterm.history.subprocess.run',return_value=Mock(stdout=json.dumps(rows))) as run,patch('agent_utils.cli.pick',return_value='ses_own') as pick:
            self.assertEqual(pick_opencode_session(self.project()),'ses_own')
            self.assertEqual(pick.call_args.args[1],[('Demo · ses_own','ses_own')])
            self.assertEqual(run.call_args.kwargs['cwd'],str(Path('/tmp/demo').resolve()))
            pick.return_value=None
            self.assertIsNone(pick_opencode_session(self.project()))
        for output in ('[]','{}','[{"id":"--bad"}]'):
            with patch('agent_utils.agterm.history.subprocess.run',return_value=Mock(stdout=output)),patch('agent_utils.cli.pick') as pick:
                with self.assertRaises(ValueError):pick_opencode_session(self.project())
                pick.assert_not_called()

    def test_opencode_resume_binds_picked_id_and_cancel_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'XDG_STATE_HOME':d,'XDG_CONFIG_HOME':d}),patch('shutil.which',side_effect=lambda x:'/bin/'+x):
            ag=Mock(dry=False);ag.launch.return_value='row'
            args=Namespace(session=None,prompt=None,command='resume',task=None)
            with patch('agent_utils.agterm.history.pick_opencode_session',return_value=None):
                self.assertIsNone(launch(ag,{'name':'Demo'},self.project(d),'opencode',args))
                ag.launch.assert_not_called()
                self.assertEqual(records(),{})
            with patch('agent_utils.agterm.history.pick_opencode_session',return_value='ses_selected'):
                record=launch(ag,{'name':'Demo'},self.project(d),'opencode',args)
                self.assertEqual(record['conversation_id'],'ses_selected')
                self.assertEqual(ag.launch.call_args.args[2][ag.launch.call_args.args[2].index('--')+2:],['--session','ses_selected'])
            ag.dry=True;ag.launch.reset_mock()
            with patch('agent_utils.agterm.history.pick_opencode_session') as picker:
                launch(ag,{'name':'Demo'},self.project(d),'opencode',args)
                picker.assert_not_called();ag.launch.assert_not_called()


    def test_cursor_and_antigravity_support_extra_roots(self):
        project=self.project();project['roots'].append('/tmp/extra')
        for agent in ('cursor-agent','agy'):
            with self.subTest(agent=agent):
                argv,_=agent_argv(project,agent)
                self.assertEqual(argv[-2:],['--add-dir','/tmp/extra'])

    def test_new_provider_resume_menu_labels_match_behavior(self):
        from agent_utils.agterm.startup import choose_session
        for agent in ('cursor-agent','pi','agy','kimi','qwen','copilot','vibe'):
            with self.subTest(agent=agent),patch('agent_utils.cli.pick',return_value=('resume',None)) as pick:
                self.assertEqual(choose_session(self.project(),agent),('resume',None))
                title,entries=pick.call_args.args
                self.assertEqual(title,'Demo / '+AGENT_NAMES[agent])
                self.assertEqual(entries[0],('New session',('new',None)))
                self.assertEqual(entries[1],('Continue most recent session' if agent=='agy' else 'Resume using provider picker',('resume',None)))


    @patch('shutil.which',side_effect=lambda name:'/bin/'+name)
    def test_kimi_new_and_native_resume_without_headless_prompt(self,which):
        for ident,picker,expected in [(None,False,[]),(None,True,['--session']),('session-123',False,['--session','session-123'])]:
            argv,_=agent_argv(self.project(),'kimi',ident,resume_picker=picker)
            self.assertEqual(argv,['/bin/kimi',*expected])
        with self.assertRaisesRegex(ValueError,'initial prompt'):
            agent_argv(self.project(),'kimi',prompt='Do work')

    def test_official_cli_additional_directory_flags(self):
        project=self.project();project['roots'].append('/tmp/extra')
        for agent,flag in [('qwen','--add-dir'),('copilot','--add-dir'),('kimi','--add-dir'),('vibe','--add-dir')]:
            argv,_=agent_argv(project,agent)
            self.assertEqual(argv[-2:],[flag,'/tmp/extra'])


    def test_gemini_is_not_a_supported_provider(self):
        self.assertNotIn('gemini',AGENTS)
        with self.assertRaisesRegex(ValueError,'Unsupported agent'):
            agent_argv(self.project(),'gemini')


    def test_kimi_native_installer_resolution(self):
        from agent_utils.cli import agent_executable
        with tempfile.TemporaryDirectory() as d,patch('agent_utils.cli.Path.home',return_value=Path(d)),patch('shutil.which',return_value=None):
            path=Path(d)/'.kimi-code/bin/kimi';path.parent.mkdir(parents=True)
            self.assertIsNone(agent_executable('kimi'))
            path.write_text('#!/bin/sh\n');path.chmod(0o644)
            self.assertIsNone(agent_executable('kimi'))
            path.chmod(0o755)
            self.assertEqual(agent_executable('kimi'),str(path))
            with patch('shutil.which',return_value='/preferred/kimi'):
                self.assertEqual(agent_executable('kimi'),'/preferred/kimi')

    def test_global_launch_args_apply_to_new_picker_and_exact_resume(self):
        from agent_utils.cli import with_provider_launch_args
        defaults={'providers':{'launch_args':{'kiro-cli':['--v3','--trust-all-tools']}}}
        project=with_provider_launch_args(self.project(),defaults)
        for ident,picker in [(None,False),(None,True),('session-123',False)]:
            argv,_=agent_argv(project,'kiro-cli',ident,resume_picker=picker)
            self.assertEqual(argv[-2:],['--v3','--trust-all-tools'])
        project['agent_args']={'kiro-cli':['--v2']}
        self.assertEqual(agent_argv(project,'kiro-cli')[0][-1],'--v2')
        self.assertNotIn('--trust-all-tools',agent_argv(project,'kiro-cli')[0])
        project['agent_args']['kiro-cli']=[]
        self.assertNotIn('--v3',agent_argv(project,'kiro-cli')[0])
        self.assertNotIn('--v3',agent_argv(self.project(),'codex')[0])

    def test_managed_resume_reads_current_global_launch_args(self):
        import json
        from agent_utils.cli import main
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'XDG_STATE_HOME':d,'XDG_CONFIG_HOME':d}),patch('shutil.which',side_effect=lambda name:'/bin/'+name):
            config=Path(d)/'config.json'
            config.write_text(json.dumps({'version':1,'windows':[],'providers':{'launch_args':{'kiro-cli':['--v3','--trust-all-tools']}}}))
            ag=Mock(dry=False);ag.launch.return_value='row'
            record=launch(ag,{'name':'Demo'},self.project(d),'kiro-cli',Namespace(session='session-123',prompt=None,command='resume',task=None))
            with patch('agent_utils.agterm.sessions.live_rows',return_value=[]),patch('agent_utils.cli.Agterm',return_value=ag):
                main(['--config',str(config),'session','resume','--record',record['id'],'--backend-stopped'])
            argv=ag.launch.call_args.args[2]
            self.assertIn('--v3',argv)
            self.assertIn('--trust-all-tools',argv)
            self.assertEqual(records()[record['id']]['agent_args'],{})


class BackOptionTests(unittest.TestCase):
    def test_plain_menu_back_entry_is_last_and_returns_sentinel(self):
        from unittest import mock
        from agent_utils import cli
        with mock.patch.dict(os.environ,{'AGTERM_UTILS_MENU':'plain'}),mock.patch('builtins.input',return_value='3'),mock.patch('builtins.print') as out:
            self.assertIs(cli.pick('X',[('a',1),('b',2)],back=True),cli.BACK)
        self.assertIn('3. ← Back',[c.args[0] for c in out.call_args_list])
        with mock.patch.dict(os.environ,{'AGTERM_UTILS_MENU':'plain'}),mock.patch('builtins.input',return_value='3'),mock.patch('builtins.print'):
            with self.assertRaises(ValueError):cli.pick('X',[('a',1),('b',2)])
