import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_utils.cli import main, validate
from agent_utils.agterm.startup import visible_agents, startup_choices, pick_provider


class ProviderSettingsTests(unittest.TestCase):
    def config(self):
        return {'version':1,'windows':[]}

    def test_auto_detect_is_default_and_hide_wins_over_default_provider(self):
        with patch('agent_utils.cli.agent_executable',side_effect=lambda name:'/bin/'+name if name in ('claude','cursor-agent','pi') else None):
            self.assertEqual(visible_agents(self.config()),['claude','cursor-agent','pi'])
            data={**self.config(),'providers':{'hidden':['cursor-agent']}}
            choices=startup_choices('cursor-agent',data)
            self.assertNotIn('cursor-agent',[value for _,value in choices])
            self.assertEqual(choices[0],('Claude','claude'))

    def test_manual_list_controls_order_and_ignores_detection_and_hidden(self):
        data={'providers':{'auto_detect':False,'hidden':['pi'],'enabled':['pi','codex']}}
        with patch('agent_utils.cli.agent_executable',side_effect=AssertionError('must not detect')):
            self.assertEqual(visible_agents(data),['pi','codex'])
            choices=startup_choices('claude',data)
            self.assertEqual(choices[:2],[('Pi','pi'),('Codex','codex')])
            self.assertNotIn('coordinator',[value for _,value in choices])
            self.assertNotIn('settings',[value for _,value in choices])
            self.assertEqual(visible_agents({'providers':{'auto_detect':False}}),[])

    def test_agent_order_with_rest_wildcard(self):
        data={'providers':{'auto_detect':False,'enabled':['claude','codex','kiro-cli','pi']}}
        values=lambda order:[v for _,v in startup_choices('claude',data,order) if v in ('claude','codex','kiro-cli','pi')]
        self.assertEqual(values(['kiro-cli','claude','codex','*']),['kiro-cli','claude','codex','pi'])
        self.assertEqual(values(['kiro-cli','*','codex']),['kiro-cli','claude','pi','codex'])
        self.assertEqual(values(['kiro-cli','claude']),['kiro-cli','claude'])
        self.assertEqual(values(None),['claude','codex','kiro-cli','pi'])

    def test_agent_order_setting_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'c.json'
            path.write_text(json.dumps({'version':1,'windows':[{'name':'Work','default_agent':'claude','projects':[]}]}))
            main(['--config',str(path),'settings','--window','Work','--agent-order','kiro-cli,claude,codex,*'])
            self.assertEqual(json.loads(path.read_text())['windows'][0]['agent_order'],['kiro-cli','claude','codex','*'])
            with self.assertRaises(ValueError):
                main(['--config',str(path),'settings','--window','Work','--agent-order','nope'])
            main(['--config',str(path),'settings','--window','Work','--agent-order',''])
            self.assertNotIn('agent_order',json.loads(path.read_text())['windows'][0])

    def test_invalid_settings_rejected(self):
        for settings in (None,[],{'auto_detect':'false'},{'enabled':'pi'},{'hidden':['unknown']},{'enabled':['pi','pi']},{'hidden':[{}]}):
            with self.subTest(settings=settings),self.assertRaises(ValueError):
                validate({**self.config(),'providers':settings})

    def test_settings_edits_are_backed_up_and_dry_run_preserves_file(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config.json';original=self.config();original['other']={'preserve':True}
            path.write_text(json.dumps(original))
            main(['--config',str(path),'--dry-run','settings','--hide-agent','cursor-agent'])
            self.assertEqual(json.loads(path.read_text()),original)
            self.assertEqual(list(path.parent.glob('*.backup-*')),[])
            main(['--config',str(path),'settings','--hide-agent','cursor-agent'])
            data=json.loads(path.read_text())
            self.assertEqual(data['providers']['hidden'],['cursor-agent'])
            self.assertTrue(data['other']['preserve'])
            self.assertEqual(len(list(path.parent.glob('*.backup-*'))),1)
            main(['--config',str(path),'settings','--auto-detect','off','--enabled-agent','pi','--enabled-agent','codex'])
            self.assertEqual(visible_agents(json.loads(path.read_text())),['pi','codex'])
            main(['--config',str(path),'settings','--show-agent','cursor-agent'])
            self.assertEqual(json.loads(path.read_text())['providers']['hidden'],[])

    def test_settings_menu_can_toggle_detection_and_manual_provider(self):
        from agent_utils.agterm.configuration import provider_settings_menu
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config.json';path.write_text(json.dumps(self.config()))
            with patch('agent_utils.cli.pick',side_effect=['auto','pi',None]):
                provider_settings_menu(path)
            data=json.loads(path.read_text())
            self.assertFalse(data['providers']['auto_detect'])
            self.assertEqual(data['providers']['enabled'],['pi'])

    def test_launch_args_validation(self):
        for args in ([],{'unknown':[]},{'kiro-cli':'--v3'},{'kiro-cli':[None]},{'kiro-cli':['bad\x00arg']}):
            with self.subTest(args=args),self.assertRaises(ValueError):
                validate({**self.config(),'providers':{'launch_args':args}})

    def test_launch_args_cli_preserves_argument_boundaries_and_can_clear(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config.json';path.write_text(json.dumps(self.config()))
            command=['--config',str(path),'settings','--agent','kiro-cli']
            main(command+['--launch-args=--v3 --trust-all-tools --model "model with spaces"'])
            settings=json.loads(path.read_text())['providers']
            self.assertEqual(settings['launch_args']['kiro-cli'],['--v3','--trust-all-tools','--model','model with spaces'])
            main(command+['--launch-args='])
            self.assertEqual(json.loads(path.read_text())['providers']['launch_args']['kiro-cli'],[])
            with self.assertRaisesRegex(ValueError,'requires --agent'):
                main(['--config',str(path),'settings','--launch-args=--v3'])
            before=path.read_bytes()
            with self.assertRaises(ValueError):main(command+['--launch-args="unterminated'])
            self.assertEqual(path.read_bytes(),before)

    def test_launch_args_menu_edits_without_shell_execution(self):
        from agent_utils.agterm.configuration import provider_settings_menu
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config.json';path.write_text(json.dumps(self.config()))
            with patch('agent_utils.cli.pick',side_effect=['launch-args','kiro-cli',None,None]),patch('builtins.input',return_value='--v3 --trust-all-tools'):
                provider_settings_menu(path)
            self.assertEqual(json.loads(path.read_text())['providers']['launch_args']['kiro-cli'],['--v3','--trust-all-tools'])
