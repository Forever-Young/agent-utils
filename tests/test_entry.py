import io
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch

from agent_utils import entry


class DetectTests(unittest.TestCase):
    def test_contexts(self):
        self.assertEqual(entry.detect_context({}), 'plain')
        self.assertEqual(entry.detect_context({'SSH_CONNECTION': 'a b c d'}), 'remote')
        self.assertEqual(entry.detect_context({'AGTERM_ENABLED': '1'}), 'agterm')
        self.assertEqual(entry.detect_context({'AGTERM_SESSION_ID': 'x', 'SSH_TTY': '/dev/ttys1'}), 'agterm')
        self.assertEqual(entry.detect_context({'ZELLIJ': '0'}), 'plain')
        pane = {'ZELLIJ': '0', 'ZELLIJ_PANE_ID': '3'}
        self.assertEqual(entry.detect_context(pane), 'zellij')
        stale = {**pane, 'AGTERM_SESSION_ID': 'x'}
        self.assertEqual(entry.detect_context(stale, alive=lambda env: False), 'zellij')
        self.assertEqual(entry.detect_context(stale, alive=lambda env: True), 'agterm')
        self.assertEqual(entry.detect_context({**pane, 'SSH_TTY': '/dev/ttys1'}), 'zellij')


class RoutingTests(unittest.TestCase):
    def test_agterm_group_passes_through_to_cli_main(self):
        with patch('agent_utils.cli.main', return_value=0) as m:
            entry.main(['--context', 'plain', 'agterm', '--dry-run', 'restore', 'sync'])
        m.assert_called_once_with(['--dry-run', 'restore', 'sync'])

    def test_admin_project_commands_reach_cli_main(self):
        with patch('agent_utils.cli.main', return_value=0) as m:
            entry.main(['admin', '--config', 'c.json', 'settings', 'list'])
        m.assert_called_once_with(['--config', 'c.json', 'settings', 'list'])

    def test_groups_reject_foreign_commands(self):
        for argv in (['agterm', 'skills'], ['admin', 'new'], ['agent', 'list'], ['nope']):
            with self.assertRaises(ValueError, msg=argv):
                entry.main(argv)

    def test_skills_instructions_agent_and_remote(self):
        with patch('agent_utils.core.skills.command', return_value=0) as m:
            entry.main(['admin', 'skills', 'status'])
        m.assert_called_once_with(['status'])
        with patch('agent_utils.core.instructions.command', return_value=0) as m:
            entry.main(['admin', 'instructions', 'status'])
        m.assert_called_once_with(['status'])
        with patch('agent_utils.agterm.names.command', return_value=0) as m:
            entry.main(['agent', 'rename', 'Topic, action'])
        m.assert_called_once_with(['rename', 'Topic, action'])
        with patch('agent_utils.core.ask.command', return_value=0) as m:
            entry.main(['agent', 'ask', 'codex', 'q'])
        m.assert_called_once_with(['codex', 'q'])
        with patch('agent_utils.agterm.remote.main', return_value=0) as m:
            entry.main(['remote', 'terminal', '--session', 'S'])
        m.assert_called_once_with(['terminal', '--session', 'S'])

    def test_bare_run_follows_context(self):
        with patch('agent_utils.cli.main', return_value=0) as m:
            entry.main(['--context=agterm'])
        m.assert_called_once_with(['startup'])
        with patch('agent_utils.agterm.remote.main', return_value=0) as m:
            entry.main(['--context', 'remote'])
        m.assert_called_once_with([])

    def test_bare_plain_menu_choice(self):
        with patch('agent_utils.cli.pick', return_value='remote'), \
                patch('agent_utils.agterm.remote.main', return_value=0) as m:
            entry.main(['--context', 'plain'])
        m.assert_called_once_with([])

    def test_configure_is_admin_config_tui(self):
        with patch('agent_utils.core.settings.command', return_value=0) as m:
            entry.main(['configure'])
        m.assert_called_once_with(['tui'])

    def test_plain_menu_settings_opens_configure(self):
        with patch('agent_utils.cli.pick', return_value='config'), \
                patch('agent_utils.core.settings.command', return_value=0) as m:
            entry.main(['--context', 'plain'])
        m.assert_called_once_with(['tui'])

    def test_bad_context(self):
        with self.assertRaises(ValueError):
            entry.main(['--context', 'tmux'])

    def test_help_lists_relevant_group_first(self):
        out = io.StringIO()
        with redirect_stdout(out):
            entry.main(['--context', 'remote', '--help'])
        text = out.getvalue()
        self.assertIn('Detected context: remote', text)
        self.assertLess(text.index('  remote '), text.index('  agterm '))


if __name__ == '__main__':
    unittest.main()
