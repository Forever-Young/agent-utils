import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from agent_utils.core import settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base/'home'; self.home.mkdir()
        patcher = mock.patch.dict(os.environ, {'HOME': str(self.home), 'XDG_CONFIG_HOME': str(base/'config'), 'XDG_STATE_HOME': str(base/'state')})
        patcher.start(); self.addCleanup(patcher.stop)
        self.cfg = base/'config/agent-utils/config.json'
        self.cfg.parent.mkdir(parents=True)
        self.cfg.write_text(json.dumps({'version': 1, 'windows': [{'name': 'Main', 'default_agent': 'codex', 'projects': [
            {'id': 'demo', 'name': 'Demo', 'roots': ['/tmp/demo']}]}]}))
        self.lines = []

    def out(self, text):
        self.lines.append(str(text))

    def cmd(self, *argv, runner=None):
        self.lines = []
        return settings.command(list(argv), runner=runner or self.fail_runner, out=self.out)

    def fail_runner(self, *a, **k):
        raise AssertionError('no command expected')

    def test_list_shows_defaults_values_and_managed(self):
        self.cmd('list', '--json')
        data = json.loads(self.lines[0])
        rows = {r['key']: r for r in data['settings']}
        self.assertEqual(rows['providers.auto_detect']['value'], True)
        self.assertFalse(rows['providers.auto_detect']['set'])
        self.assertEqual(rows['notify.enabled']['value'], False)
        self.assertIn('snippets.variables.*', rows)
        names = {m['name']: m for m in data['managed']}
        self.assertEqual(names['Windows and projects']['state'], '1 windows, 1 projects')
        self.assertNotIn('Claude Code', names)  # provider-owned paths are not listed
        self.assertNotIn('Installed copy', names)

    def test_set_previews_then_writes_with_backup_and_undo(self):
        before = self.cfg.read_text()
        self.cmd('set', 'new_session_position', 'top')
        self.assertIn('+  "new_session_position": "top"', '\n'.join(self.lines))
        self.assertEqual(self.cfg.read_text(), before)
        self.cmd('set', 'new_session_position', 'top', '--yes')
        self.assertEqual(json.loads(self.cfg.read_text())['new_session_position'], 'top')
        self.assertEqual(len(settings.backups('config')), 1)
        self.cmd('get', 'new_session_position'); self.assertEqual(self.lines, ['top'])
        self.cmd('undo', 'config')
        self.assertEqual(json.loads(self.cfg.read_text())['new_session_position'], 'top')
        self.cmd('undo', 'config', '--yes')
        self.assertNotIn('new_session_position', json.loads(self.cfg.read_text()))
        self.assertEqual(len(settings.backups('config')), 1)  # the pre-undo state, so undo can be undone

    def test_family_and_types(self):
        self.cmd('set', 'providers.launch_args.kiro-cli', '--v3 --flag "a b"', '--yes')
        self.assertEqual(json.loads(self.cfg.read_text())['providers']['launch_args']['kiro-cli'], ['--v3', '--flag', 'a b'])
        self.cmd('get', 'providers.launch_args.kiro-cli'); self.assertEqual(json.loads(self.lines[0]), ['--v3', '--flag', 'a b'])
        self.cmd('set', 'providers.hidden', 'pi, vibe,pi', '--yes')
        self.assertEqual(json.loads(self.cfg.read_text())['providers']['hidden'], ['pi', 'vibe'])
        self.cmd('set', 'providers.auto_detect', 'off', '--yes')
        self.assertIs(json.loads(self.cfg.read_text())['providers']['auto_detect'], False)
        self.cmd('unset', 'providers.auto_detect', '--yes')
        self.assertNotIn('auto_detect', json.loads(self.cfg.read_text())['providers'])
        self.cmd('set', 'snippets.variables.inbox_host', 'https://example.test', '--yes')
        snippets = json.loads((self.cfg.parent/'snippets.json').read_text())
        self.assertEqual(snippets, {'variables': {'inbox_host': 'https://example.test'}})

    def test_rejects_bad_values_without_writing(self):
        before = self.cfg.read_text()
        for key, value in (('providers.auto_detect', 'maybe'), ('providers.hidden', 'nosuch'),
                           ('new_session_position', 'left'), ('providers.launch_args.nosuch', 'x'),
                           ('unknown.key', 'x'), ('providers.launch_args.*', 'x'), ('notify.url', 'ftp://x')):
            with self.assertRaises(ValueError, msg=key):
                self.cmd('set', key, value, '--yes')
        self.assertEqual(self.cfg.read_text(), before)
        with self.assertRaises(ValueError):
            self.cmd('set', 'notify.enabled', 'on', '--yes')  # needs a URL first
        self.assertFalse((self.cfg.parent/'notify.json').exists())

    def test_whole_file_is_validated(self):
        self.cfg.write_text(json.dumps({'version': 2, 'windows': []}))
        with self.assertRaises(ValueError):
            self.cmd('set', 'new_session_position', 'top', '--yes')
        self.assertEqual(settings.backups('config'), [])

    def test_notify_settings_are_read_by_notify(self):
        self.cmd('set', 'notify.url', 'https://ntfy.example/topic', '--yes')
        self.cmd('set', 'notify.enabled', 'on', '--yes')
        from agent_utils.core import notify
        loaded = notify.load()
        self.assertTrue(loaded['enabled']); self.assertEqual(loaded['url'], 'https://ntfy.example/topic')

    def test_actions_preview_unless_yes(self):
        calls = []
        def runner(argv, env=None):
            calls.append((argv, env)); return mock.Mock(returncode=0)
        self.cmd('run', 'instructions-apply', runner=runner)
        self.cmd('run', 'instructions-apply', '--yes', runner=runner)
        self.assertEqual(calls[0][0], [sys.executable, '-m', 'agent_utils.entry', 'admin', 'instructions', 'apply'])
        self.assertEqual(calls[1][0][-1], '--yes')
        root = str(Path(settings.__file__).resolve().parents[2])
        self.assertTrue(calls[0][1]['PYTHONPATH'].startswith(root))
        self.cmd('run', 'notify-test', runner=runner)
        self.assertEqual(len(calls), 2)  # acts at once, so it needs --yes
        self.cmd('run', 'optional', 'kiro-hooks', '--yes', runner=runner)
        self.assertEqual(calls[-1][0][-2:], ['kiro-hooks', '--apply'])
        self.assertEqual(calls[-1][0][1:3], ['-m', 'agent_utils.core.setup.optional'])
        with self.assertRaises(ValueError):
            self.cmd('run', 'optional', runner=runner)
        with self.assertRaises(ValueError):
            self.cmd('run', 'skills-status', 'extra', runner=runner)
        with self.assertRaises(ValueError):
            self.cmd('run', 'nosuch', runner=runner)

    def test_menu_toggle_and_action(self):
        from agent_utils.core import configure_flows as flows
        from agent_utils.cli import BACK
        by_label = lambda section, text: next(e for l, e in flows.entries(section) if text in l)
        picks = iter(['providers', by_label('providers', 'Offer every installed agent'), BACK,
                      'skills', by_label('skills', 'Adopt copies'), None])
        answers = iter(['y', '', 'n', ''])  # write it; continue; do not apply; continue
        calls = []
        def runner(argv, env=None):
            calls.append(argv); return mock.Mock(returncode=0)
        settings.menu(pick=lambda prompt, entries, back=False: next(picks), ask=lambda prompt: next(answers),
                      runner=runner, out=self.out)
        self.assertIs(json.loads(self.cfg.read_text())['providers']['auto_detect'], False)
        self.assertEqual([c[-1] for c in calls], ['sync'])  # preview ran, apply declined

    def test_project_flow_disables_and_adds(self):
        from agent_utils.core import configure_flows as flows
        entry = next(e for l, e in flows.entries('projects') if 'Demo (demo)' in l)
        choices = iter(['toggle'])
        flows.drive_sync(entry, lambda t, c: next(choices), lambda p: 'y', None, self.out)
        data = json.loads(self.cfg.read_text())
        self.assertIs(data['windows'][0]['projects'][0]['enabled'], False)
        add = next(e for l, e in flows.entries('projects') if l == 'Add a project')
        pick = iter(['', 'claude'])  # window: a new one
        text = iter(['hobby', 'Play', '/tmp/hobby'])
        def ask(prompt):
            return 'y' if prompt.endswith('[y/N] ') else '' if prompt.startswith('Enter') else next(text)
        flows.drive_sync(add, lambda t, c: next(pick), ask, None, self.out)
        data = json.loads(self.cfg.read_text())
        self.assertEqual([w['name'] for w in data['windows']], ['Main', 'Play'])
        self.assertEqual(data['windows'][1]['projects'][0]['id'], 'hobby')

    def test_rules_entries_turn_snippets_on_and_off(self):
        from agent_utils.core import configure_flows as flows
        rules = dict(flows.entries('instructions'))
        label = next(l for l in rules if 'Untrusted content' in l)
        self.assertTrue(label.startswith('[off] '))
        calls = []
        def runner(argv, env=None):
            calls.append(argv); return mock.Mock(returncode=0)
        picks = iter([['--add', 'untrusted-content']])
        flows.drive_sync(rules[label], lambda t, c: next(picks), lambda p: 'y', runner, self.out)
        self.assertEqual([c[-1] for c in calls], ['untrusted-content', '--yes'])  # preview, then apply
        self.assertEqual(calls[0][4:7], ['instructions', 'compose', '--add'])

    def test_custom_rule_is_marked_and_can_be_created(self):
        from agent_utils.core import configure_flows as flows
        from agent_utils.core import snippets
        text = iter(['mine', 'My rule', 'Short description', 'Always say hello.'])
        flows.drive_sync(('flow', flows.add_rule_flow), None, lambda p: 'y' if p.endswith('[y/N] ') else '' if p.startswith('Enter') else next(text), None, self.out)
        self.assertTrue((snippets.user_dir()/'mine.md').is_file())
        labels = [l for l, e in flows.entries('instructions')]
        self.assertTrue(any(l.startswith('[off] (custom) My rule: Short description') for l in labels))
        self.assertTrue(any(l.startswith('[off] Untrusted') or l.startswith('[off] Commit') for l in labels))
        shown = []
        rule = dict(flows.entries('instructions'))[next(l for l in labels if 'My rule' in l)]
        flows.drive_sync(rule, lambda t, c: None, lambda p: '', None, shown.append)
        self.assertIn('Custom rule', shown[0])
        self.assertIn('Always say hello.', shown[0])

    def test_personalization_lists_rule_variables_with_meaning(self):
        from agent_utils.core import configure_flows as flows
        from agent_utils.core import snippets
        d = snippets.user_dir(); d.mkdir(parents=True)
        (d/'hello.md').write_text('---\nid: hello\ntitle: Hello\nsummary: s\nvariables: owner=who owns the repos\n---\nHi {{owner}} and {{team}}.\n')
        entries = dict(flows.entries('snippets'))
        owner = next(l for l in entries if l.startswith('{{owner}}, who owns the repos'))
        self.assertIn('[rules: Hello]', owner)
        self.assertTrue(any(l.startswith('{{team}}, used in the text of these rules') for l in entries))
        self.assertTrue(any(l.startswith('Add your own variable') for l in entries))
        text = iter(['Ana'])
        flows.drive_sync(entries[owner], None, lambda p: 'y' if p.endswith('[y/N] ') else '' if p.startswith('Enter') else next(text), None, self.out)
        self.assertEqual(snippets.variables()['owner'], 'Ana')

    def test_agent_checks_are_in_the_skills_section(self):
        from agent_utils.core import configure_flows as flows
        labels = [l for l, e in flows.entries('skills')]
        self.assertTrue(any('agent review' in l for l in labels))
        calls = []
        entry = dict(flows.entries('skills'))[next(l for l in labels if 'agent review' in l)]
        flows.drive_sync(entry, lambda t, c: 'codex', lambda p: '', lambda argv, env=None: calls.append(argv) or mock.Mock(returncode=0), self.out)
        self.assertEqual(calls[0][-3:], ['check', '--agent', 'codex'])

    def test_skills_entries_offer_add(self):
        from agent_utils.core import configure_flows as flows
        self.assertIn('Add a skill to the store', [l for l, e in flows.entries('skills')])

    def test_hooks_and_schedule_entries_are_actionable(self):
        from agent_utils.core import configure_flows as flows
        labels = [l for l, e in flows.entries('hooks')]
        self.assertTrue(any('Kiro' in l for l in labels))
        self.assertFalse(any('Installed copy' in l for l in labels))
        self.assertNotIn('install', settings.SECTIONS)
        self.assertNotIn('provider-owned', settings.SECTIONS)
        self.assertTrue(all(e[0] == 'flow' for l, e in flows.entries('schedules')))

    def test_launch_args_lists_every_agent_with_its_name(self):
        from agent_utils.core.agents import AGENTS
        rows = {r['key']: r for r in settings.values('providers')}
        for agent in AGENTS:
            self.assertIn('providers.launch_args.'+agent, rows)
            self.assertIn(agent, rows['providers.launch_args.'+agent]['help'])
        self.assertFalse(rows['providers.launch_args.claude']['set'])

    def test_bare_config_prints_help_and_tui_opens_menu(self):
        self.assertEqual(self.cmd(), 0)  # help goes to stdout; no menu, no runner
        with mock.patch.object(settings, 'menu', return_value=7) as menu:
            self.assertEqual(self.cmd('tui'), 7)
        menu.assert_called_once()
        with self.assertRaises(SystemExit):
            self.cmd('menu')  # old spelling is gone, no alias


if __name__ == '__main__':
    unittest.main()
