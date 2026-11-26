import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_utils import entry
from agent_utils.core import zellij
from agent_utils.core.setup import zellij_mobile


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root/'projects.json'
        self.ui = self.root/'phone config.kdl'
        self.ui.write_text('on_force_close "detach"\n')
        self.config.write_text(json.dumps({'version': 1, 'windows': [], 'zellij': {
            'ssh_config_path': str(self.ui), 'session': 'mobile'}}))
        self.env = {'SSH_TTY': '/dev/synthetic', 'AGTERM_UTILS_ZELLIJ': 'zellij',
                    'AGTERM_SESSION_ID': 'stale'}

    def test_bare_group_is_not_forced_into_project_menu(self):
        with patch('agent_utils.core.zellij.command') as command:
            entry.main(['zellij'])
        command.assert_called_once_with([])

    def test_bare_outside_runs_inside_opens_menu(self):
        with patch.dict('os.environ', {}, clear=True), patch.object(zellij, 'run_session') as run:
            zellij.command(['--config', str(self.config)])
            run.assert_called_once()
        with patch.dict('os.environ', {'ZELLIJ':'1','ZELLIJ_PANE_ID':'0'}, clear=True), patch.object(zellij, 'menu') as menu:
            zellij.command(['--config', str(self.config)])
            menu.assert_called_once()

    def test_existing_session_only_attaches(self):
        z = zellij.Zellij(env=self.env)
        with patch.object(zellij, 'list_sessions', return_value={'mobile':'running'}), patch('subprocess.call', return_value=0) as call:
            zellij.run_session(str(self.config), z)
        self.assertEqual(call.call_args.args[0], ['zellij', '--config', str(self.ui), 'attach', 'mobile'])
        self.assertNotIn('AGTERM_SESSION_ID', call.call_args.kwargs['env'])

    def test_new_named_session_contains_launcher_template(self):
        z = zellij.Zellij('work-phone', env=self.env)
        with patch.object(zellij, 'list_sessions', return_value={}), patch('subprocess.call', return_value=0) as call:
            zellij.run_session(str(self.config), z)
        argv = call.call_args.args[0]
        self.assertEqual(argv[-3:], ['attach', 'work-phone', '--create'])
        layout = argv[argv.index('--layout-string')+1]
        self.assertIn('default_tab_template', layout)
        self.assertIn('zellij_launcher.py', layout)
        self.assertIn(str(self.config), layout)
        self.assertIn('compact-bar', layout)

    def test_exited_session_does_not_restart_processes(self):
        with patch.object(zellij, 'list_sessions', return_value={'mobile':'exited'}), patch('subprocess.call') as call:
            with self.assertRaisesRegex(ValueError, 'resurrect'):
                zellij.run_session(str(self.config), zellij.Zellij(env=self.env))
        call.assert_not_called()

    def test_resurrect_attaches_exited_session_and_runs_commands(self):
        with patch.object(zellij, 'list_sessions', return_value={'mobile':'exited'}), patch('subprocess.call', return_value=0) as call:
            zellij.run_session(str(self.config), zellij.Zellij(env=self.env), resurrect=True)
        self.assertEqual(call.call_args.args[0], ['zellij', '--config', str(self.ui), 'attach', '--force-run-commands', 'mobile'])

    def test_fresh_deletes_session_then_creates_it(self):
        z = zellij.Zellij(env=self.env)
        with patch.object(zellij, 'list_sessions', return_value={'mobile':'exited'}), \
             patch('subprocess.run') as run, patch('subprocess.call', return_value=0) as call:
            run.return_value.returncode = 0
            zellij.run_session(str(self.config), z, fresh=True)
        self.assertEqual(run.call_args.args[0], ['zellij', 'delete-session', '--force', 'mobile'])
        self.assertEqual(call.call_args.args[0][-3:], ['attach', 'mobile', '--create'])

    def test_custom_config_is_separate_from_project_config(self):
        argv = zellij.attach_argv('example', self.env, str(self.config))
        self.assertEqual(argv[2], str(self.ui))
        self.ui.unlink()
        with self.assertRaisesRegex(ValueError, 'config not found'):
            zellij.attach_argv('example', self.env, str(self.config))

    def test_shell_choice_does_not_launch_agent(self):
        with patch('agent_utils.cli.pick', return_value='shell'), patch.object(zellij, 'menu') as menu, patch('os.execv') as execute:
            zellij.launcher(str(self.config), zellij.Zellij(env={'SHELL':'/bin/sh'}))
        menu.assert_not_called()
        execute.assert_called_once_with('/bin/sh', ['/bin/sh', '-l'])

    def test_managed_tab_replaces_launcher_template_with_explicit_command(self):
        z = zellij.Zellij('mobile', env=self.env)
        dump = '''layout {
    new_tab_template {
        pane command="python3" {
            args "zellij_launcher.py"
        }
    }
}'''
        with patch.object(z, 'run', side_effect=[dump, '7']) as run, \
                patch.object(z, 'tabs', return_value=[{'tab_id': 7}]), \
                patch.object(z, 'agent_pane', return_value=12):
            self.assertEqual(z.new_tab('Example', '/tmp', ['env', 'EXAMPLE=1', '/bin/sh'], False), (7, 12))
        args = run.call_args.args
        self.assertEqual(args[0], 'new-tab')
        self.assertIn('--no-focus', args)
        layout = args[args.index('--layout-string') + 1]
        self.assertIn('pane name="agent" focus=true command="env"', layout)
        self.assertIn('args "EXAMPLE=1" "/bin/sh"', layout)
        self.assertNotIn('zellij_launcher.py', layout)


class ContribTests(unittest.TestCase):
    def test_optional_item_is_explicit_and_configuration_can_install_it(self):
        from agent_utils.core import settings
        from agent_utils.core.setup import optional
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            runner = Mock(return_value=Mock(returncode=0))
            optional.main(['zellij-mobile', '--apply'], runner=runner, home=Path(folder))
            self.assertIn('agent_utils.core.setup.zellij_mobile', runner.call_args.args[0])
            self.assertIn('--apply', runner.call_args.args[0])
        action = settings.action('zellij-mobile-install')
        self.assertIn('--apply', action.command(apply=True))
        self.assertEqual(settings.find('zellij.ssh_config_path').default, '~/.config/zellij/config-ssh.kdl')

    def test_preview_install_custom_protection_and_remove(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            argv = ['--home', folder]
            dest = zellij_mobile.destination(home)
            zellij_mobile.main(argv)
            self.assertFalse(dest.exists())
            zellij_mobile.main(argv+['--apply'])
            self.assertTrue(zellij_mobile.status(home))
            dest.write_text('custom\n')
            for flags in (['--apply'], ['--remove','--apply']):
                with self.assertRaisesRegex(ValueError, 'Preserving customized'):
                    zellij_mobile.main(argv+flags)
            self.assertEqual(dest.read_text(), 'custom\n')
            dest.write_text(zellij_mobile.SOURCE.read_text())
            zellij_mobile.main(argv+['--remove','--apply'])
            self.assertFalse(dest.exists())

    def test_installer_uses_configured_path(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            conf = home/'.config/agent-utils/config.json'
            conf.parent.mkdir(parents=True)
            conf.write_text(json.dumps({'zellij':{'ssh_config_path':'~/phone.kdl'}}))
            self.assertEqual(zellij_mobile.destination(home),home/'phone.kdl')
