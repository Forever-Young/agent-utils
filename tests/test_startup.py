import unittest
from agent_utils.agterm.startup import resolve

class StartupTests(unittest.TestCase):
    def setUp(self):
        self.project={'id':'p','name':'Project','roots':['/tmp/main','/tmp/extra']}
        self.data={'windows':[{'name':'Team','default_agent':'claude','projects':[self.project]}]}
    def test_session_membership_wins(self):
        tree={'workspaces':[{'id':'moved','name':'Project','sessions':[{'id':'s'}]}]}
        self.assertEqual(resolve(self.data,tree,'stale','s')[1]['id'],'p')
    def test_cwd_secondary_root(self):
        self.assertEqual(resolve(self.data,{},cwd='/tmp/extra/sub')[1]['id'],'p')
    def test_stale_context_no_cwd_guess(self):
        self.assertIsNone(resolve(self.data,{},'stale',cwd='/tmp/main'))
    def test_ambiguous_workspace(self):
        self.data['windows'].append({'name':'Other','projects':[dict(self.project,id='q')]})
        self.assertIsNone(resolve(self.data,{'workspaces':[{'id':'w','name':'Project'}]},'w'))


class CoordinatorMenuTests(unittest.TestCase):
    def test_coordinator_mode_follows_new_session_for_three_agents_only(self):
        from unittest.mock import patch
        from agent_utils.agterm.startup import choose_session
        for agent,expected in (('claude',True),('codex',True),('kiro-cli',True),('opencode',False),('pi',False)):
            with patch('agent_utils.cli.pick') as pick,patch('agent_utils.agterm.history.claude_history',return_value=[]),patch('agent_utils.agterm.history.codex_history',return_value=[]):
                choose_session({'name':'Demo','roots':['/tmp']},agent)
                labels=[label for label,_ in pick.call_args[0][1]]
                self.assertEqual(labels[0],'New session')
                self.assertEqual('Coordinator mode' in labels,expected)
                if expected:self.assertEqual(labels[1],'Coordinator mode')
    def test_coordinator_selection_dispatches_without_launching_regular_agent(self):
        import tempfile,json,os
        from pathlib import Path
        from unittest.mock import patch
        from agent_utils.agterm.startup import startup
        data={'version':1,'windows':[{'name':'Team','default_agent':'claude','projects':[{'id':'p','name':'Project','roots':['/tmp']}]}]}
        with tempfile.TemporaryDirectory() as d:
            config=Path(d)/'config.json';config.write_text(json.dumps(data))
            pair=(data['windows'][0],data['windows'][0]['projects'][0])
            with patch.dict(os.environ,{'AGTERM_ENABLED':'0','AGTERM_UTILS_STARTUP_HANDOFF':''}),patch('agent_utils.agterm.startup.resolve',return_value=pair),patch('agent_utils.cli.pick',return_value='claude'),patch('agent_utils.agterm.startup.choose_session',return_value=('coordinator',None)),patch('agent_utils.agterm.startup.open_coordinator') as launch,patch('os.execv') as execute:
                self.assertIsNone(startup(config))
                launch.assert_called_once_with(*pair,agent='claude')
                with patch.dict(os.environ,{'AGTERM_UTILS_STARTUP_HANDOFF':'1'}):
                    self.assertEqual(startup(config),10)
                    launch.side_effect=OSError('launch failed')
                    with patch('agent_utils.agterm.startup.plain_shell',return_value=None) as fallback:
                        self.assertIsNone(startup(config))
                        fallback.assert_called_once_with()
                execute.assert_not_called()


class SessionMenuTests(unittest.TestCase):
    def test_new_first_then_exact_sessions_for_each_listable_provider(self):
        from unittest.mock import patch
        from agent_utils.agterm.startup import choose_session
        project={'name':'Demo','roots':['/tmp/demo']}
        for agent,source in [('claude','claude_history'),('codex','codex_history'),('opencode','opencode_session_entries')]:
            data=[('Saved session','ses_1')] if agent=='opencode' else [{'title':'Saved session','conversation_id':'session-id'}]
            expected='ses_1' if agent=='opencode' else 'session-id'
            with self.subTest(agent=agent),patch('agent_utils.agterm.history.'+source,return_value=data),patch('agent_utils.cli.pick',return_value=('resume',expected)) as pick:
                self.assertEqual(choose_session(project,agent),('resume',expected))
                entries=pick.call_args.args[1]
                self.assertEqual(entries[0],('New session',('new',None)))
                off=2 if agent=='opencode' else 3
                if agent!='opencode':self.assertEqual(entries[1][1],('coordinator',None))
                self.assertEqual(entries[off][1],('resume',expected))
                from agent_utils.cli import SEP
                self.assertIs(entries[off-1][1],SEP);self.assertIs(entries[off+1][1],SEP)

    def test_separator_selection_is_a_noop_in_text_menus(self):
        from unittest.mock import patch
        from agent_utils import cli
        entries=[('A',1),cli.SEPARATOR,('B',2)]
        with patch.dict('os.environ',{'AGTERM_UTILS_MENU':'plain'}),patch('builtins.input',side_effect=['2','3']),patch('builtins.print'):
            self.assertEqual(cli.pick('t',entries),2)

    def test_kiro_native_picker_and_cancel(self):
        from unittest.mock import patch
        from agent_utils.agterm.startup import choose_session
        with patch('agent_utils.cli.pick',return_value=None) as pick:
            self.assertIsNone(choose_session({'name':'Demo'},'kiro-cli'))
            self.assertEqual(pick.call_args.args[1],[('New session',('new',None)),('Coordinator mode',('coordinator',None)),('Resume using provider picker',('resume',None))])

    def test_listing_failure_still_allows_new_session(self):
        from unittest.mock import patch
        from agent_utils.agterm.startup import choose_session
        with patch('agent_utils.agterm.history.opencode_session_entries',side_effect=ValueError('unavailable')),patch('agent_utils.cli.pick',return_value=('new',None)) as pick:
            self.assertEqual(choose_session({'name':'Demo','roots':['/tmp/demo']},'opencode'),('new',None))
            self.assertEqual(pick.call_args.args[1],[('New session',('new',None))])

    def test_startup_passes_selected_id_to_in_place_launch(self):
        import json,os,tempfile
        from pathlib import Path
        from unittest.mock import patch
        from agent_utils.agterm.startup import startup
        project={'id':'p','name':'Demo','roots':['/tmp/demo']}
        window={'name':'Demo','default_agent':'codex','projects':[project]}
        with tempfile.TemporaryDirectory() as d:
            config=Path(d)/'config.json';config.write_text(json.dumps({'version':1,'windows':[window]}))
            with patch.dict(os.environ,{'AGTERM_ENABLED':'1','AGTERM_WINDOW_ID':'','AGTERM_SESSION_ID':'row'}),patch('agent_utils.agterm.startup.resolve',return_value=(window,project)),patch('agent_utils.cli.pick',return_value='opencode'),patch('agent_utils.agterm.startup.choose_session',return_value=('resume','ses_picked')),patch('agent_utils.agterm.sessions.launch_in_place') as launch:
                startup(config)
                launch.assert_called_once_with(window,project,'opencode',True,'ses_picked')

    def test_explicit_menu_passes_selected_session_to_launch(self):
        import json,tempfile
        from pathlib import Path
        from unittest.mock import patch
        from agent_utils.cli import main
        data={'version':1,'windows':[{'name':'Demo','default_agent':'claude','projects':[{'id':'p','name':'Demo','roots':['/tmp/demo']}]}]}
        with tempfile.TemporaryDirectory() as d:
            config=Path(d)/'config.json';config.write_text(json.dumps(data))
            with patch('agent_utils.cli.pick',return_value='opencode'),patch('agent_utils.agterm.startup.choose_session',return_value=('resume','ses_picked')),patch('agent_utils.agterm.sessions.launch') as launch:
                main(['--config',str(config),'menu','--project','p'])
                args=launch.call_args.args
                self.assertEqual(args[3],'opencode')
                self.assertEqual(args[4].session,'ses_picked')
                self.assertEqual(args[4].command,'resume')
