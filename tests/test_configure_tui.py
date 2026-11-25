import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from agent_utils.core import settings


class UiChoiceTests(unittest.TestCase):
    def test_menu_forced(self):
        with mock.patch.object(settings, 'menu', return_value=3) as menu:
            self.assertEqual(settings.open_ui('menu'), 3)
        menu.assert_called_once()

    def test_auto_without_terminal_uses_menu(self):
        with mock.patch('sys.stdin.isatty', return_value=False), mock.patch.object(settings, 'menu', return_value=0) as menu:
            self.assertEqual(settings.open_ui('auto'), 0)
        menu.assert_called_once()

    def test_textual_without_terminal_is_an_error(self):
        with mock.patch('sys.stdin.isatty', return_value=False):
            with self.assertRaises(ValueError):
                settings.open_ui('textual')

    def test_auto_prefers_textual_on_a_terminal(self):
        fake = mock.Mock(return_value=0)
        with mock.patch('sys.stdin.isatty', return_value=True), mock.patch('sys.stdout.isatty', return_value=True), \
                mock.patch.dict('sys.modules', {'agent_utils.core.configure_tui': mock.Mock(run_app=fake)}), \
                mock.patch.object(settings, 'menu') as menu:
            self.assertEqual(settings.open_ui('auto'), 0)
        fake.assert_called_once()
        menu.assert_not_called()

    def test_tui_ui_option(self):
        with mock.patch.object(settings, 'open_ui', return_value=0) as ui:
            settings.command(['tui', '--ui', 'menu'])
        self.assertEqual(ui.call_args[0][0], 'menu')


@unittest.skipUnless(importlib.util.find_spec('textual'), 'optional Textual not installed')
class ConfigureAppTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        home = base/'home'; home.mkdir()
        patcher = mock.patch.dict(os.environ, {'HOME': str(home), 'XDG_CONFIG_HOME': str(base/'config'), 'XDG_STATE_HOME': str(base/'state')})
        patcher.start(); self.addCleanup(patcher.stop)
        self.cfg = base/'config/agent-utils/config.json'
        self.cfg.parent.mkdir(parents=True)
        self.cfg.write_text(json.dumps({'version': 1, 'windows': [{'name': 'Main', 'default_agent': 'codex', 'projects': [
            {'id': 'demo', 'name': 'Demo', 'roots': ['/tmp/demo']}]}]}))

    async def test_toggle_setting_asks_then_writes(self):
        from agent_utils.core.configure_tui import ConfigureApp
        from textual.widgets import OptionList
        app = ConfigureApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            entries = app.query_one('#entries', OptionList)
            self.assertEqual(app.section, 'providers')
            self.assertTrue(entries.option_count > 0)
            await pilot.press('tab', 'enter')  # first entry: providers.auto_detect (bool)
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, 'Ask')
            await pilot.press('y')
            await pilot.pause()
        data = json.loads(self.cfg.read_text())
        self.assertIs(data['providers']['auto_detect'], False)

    async def test_declining_writes_nothing(self):
        from agent_utils.core.configure_tui import ConfigureApp
        app = ConfigureApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press('tab', 'enter')
            await pilot.pause()
            await pilot.press('n')
            await pilot.pause()
        self.assertNotIn('providers', json.loads(self.cfg.read_text()))

    async def test_project_can_be_disabled_from_the_screen(self):
        from agent_utils.core.configure_tui import ConfigureApp
        app = ConfigureApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press('down', 'down', 'down')  # providers, agterm settings, notifications, projects
            await pilot.pause()
            self.assertEqual(app.section, 'projects')
            await pilot.press('tab', 'down', 'enter')  # the Demo project
            await pilot.pause()
            await pilot.press('enter')  # first operation: Disable
            await pilot.pause()
            await pilot.press('y')
            await pilot.pause()
        project = json.loads(self.cfg.read_text())['windows'][0]['projects'][0]
        self.assertIs(project['enabled'], False)

    async def test_rule_preview_diff_is_shown_in_the_confirmation(self):
        from agent_utils.core.configure_tui import ConfigureApp
        app = ConfigureApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press('down', 'down', 'down', 'down', 'down', 'down')  # ... shared rules
            await pilot.pause()
            self.assertEqual(app.section, 'instructions')
            await pilot.press('tab', 'enter')  # first rule, off: choice screen
            await pilot.pause()
            await pilot.press('enter')  # Turn on: runs the preview
            for _ in range(60):
                await pilot.pause(0.25)
                if type(app.screen).__name__ == 'Ask' and app.screen.kind == 'confirm':
                    break
            self.assertEqual(app.screen.kind, 'confirm')
            self.assertIn('agent-utils-snippet:start', app.screen.body_text)
            await pilot.press('n')
            await pilot.pause()

    async def test_moving_in_sections_changes_entries(self):
        from agent_utils.core.configure_tui import ConfigureApp
        app = ConfigureApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press('down')
            await pilot.pause()
            self.assertEqual(app.section, 'placement')
            await pilot.press('q')


if __name__ == '__main__':
    unittest.main()
