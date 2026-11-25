import json
import tempfile
import unittest
from pathlib import Path

from agent_utils.core import snippets as sn


class SnippetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir/'a.md').write_text('---\nid: a\ntitle: A\nsummary: first\ndefault: on\nconflicts: c\n---\nRule A {{host}}\n')
        (self.dir/'b.md').write_text('---\nid: b\n---\nRule B\n')
        (self.dir/'c.md').write_text('---\nid: c\n---\nRule C\n')
        self.lib = sn.library([self.dir]); self.vals = {'host': 'h.example'}

    def test_library_parses_frontmatter(self):
        self.assertTrue(self.lib['a']['default']); self.assertEqual(self.lib['a']['conflicts'], ['c'])
        self.assertEqual(self.lib['b']['applies_to'], ['all'])

    def test_bundled_library_renders_with_its_variables(self):
        lib = sn.library([sn.LIBRARY])
        self.assertTrue({'inbox-policy', 'session-naming', 'shared-rules-source', 'privacy-in-repos', 'commit-conventions', 'untrusted-content', 'confirmation-questions'} <= set(lib))
        for snip in lib.values(): sn.render(snip, {})
        self.assertEqual([s for s in lib if lib[s]['default']], ['confirmation-questions', 'inbox-policy', 'session-naming', 'shared-rules-source'])

    def test_inbox_policy_sends_signoffs_to_the_inbox(self):
        # Plan 14: plans and code reviews go to the Inbox; a blocking review only on request.
        text = sn.render(sn.library([sn.LIBRARY])['inbox-policy'], {})
        for phrase in ('needs the owner\'s read or sign-off', '`attachments`', '`get_guide_brief`',
                       '`submit_guide`', 'Do not open a blocking Plannotator review',
                       "overrides Plannotator's own guide", 'when the Inbox cannot be reached',
                       'do not wait or poll', '(Kiro) reads the thread on its next turn',
                       'In Kiro, list unread threads'):
            self.assertIn(phrase, ' '.join(text.split()))

    def test_add_keeps_outside_text_and_is_ok(self):
        text, actions = sn.change('# Mine\n\nHand text.\n', self.lib, self.vals, add=['a', 'b'])
        self.assertTrue(text.startswith('# Mine\n\nHand text.\n\n<!-- agent-utils-snippet:start id=a'))
        self.assertIn('Rule A h.example', text)
        self.assertEqual([a['action'] for a in actions], ['add', 'add'])
        self.assertEqual({r['id']: r['state'] for r in sn.inspect(text, self.lib, self.vals)}, {'a': 'ok', 'b': 'ok', 'c': 'missing'})
        self.assertEqual(sn.change(text, self.lib, self.vals, add=['a'])[0], text)

    def test_stale_then_update(self):
        text, _ = sn.change('', self.lib, self.vals, add=['b'])
        (self.dir/'b.md').write_text('---\nid: b\n---\nRule B2\n'); lib = sn.library([self.dir])
        self.assertEqual(sn.inspect(text, lib, self.vals)[1]['state'], 'stale')
        new, actions = sn.change(text, lib, self.vals, update=True)
        self.assertIn('Rule B2', new); self.assertEqual(actions[0]['action'], 'update')

    def test_hand_edit_is_kept_until_force(self):
        text, _ = sn.change('', self.lib, self.vals, add=['b'])
        edited = text.replace('Rule B', 'Rule B plus mine')
        self.assertEqual(sn.inspect(edited, self.lib, self.vals)[1]['state'], 'edited')
        same, actions = sn.change(edited, self.lib, self.vals, update=True)
        self.assertEqual(same, edited); self.assertEqual(actions[0]['action'], 'skip')
        forced, _ = sn.change(edited, self.lib, self.vals, update=True, force=True)
        self.assertNotIn('plus mine', forced)
        removed, actions = sn.change(edited, self.lib, self.vals, remove=['b'])
        self.assertEqual(removed, edited); self.assertEqual(actions[0]['action'], 'skip')

    def test_remove_and_conflict_and_unset_variable(self):
        text, _ = sn.change('Top\n', self.lib, self.vals, add=['a', 'b'])
        new, _ = sn.change(text, self.lib, self.vals, remove=['a'])
        self.assertNotIn('Rule A', new); self.assertIn('Rule B', new); self.assertTrue(new.startswith('Top\n'))
        with self.assertRaises(ValueError): sn.change(text, self.lib, self.vals, add=['c'])
        with self.assertRaises(ValueError): sn.change('', self.lib, self.vals, add=['nope'])
        _, actions = sn.change('', self.lib, {}, add=['a'])
        self.assertEqual(actions[0]['action'], 'skip'); self.assertIn('host', actions[0]['reason'])

    def test_target_paths(self):
        self.assertEqual(sn.target_path(project='/p'), Path('/p/AGENTS.md'))
        self.assertEqual(sn.target_path(src='/s'), Path('/s/common.md'))
        self.assertEqual(sn.target_path(part='codex', src='/s'), Path('/s/codex.md'))
        with self.assertRaises(ValueError): sn.target_path(part='nope')

    def test_replace_rebuilds_in_order(self):
        text, actions = sn.change('# Old hand text\n', self.lib, self.vals, add=['b', 'a'], replace=True)
        self.assertNotIn('Old hand text', text); self.assertEqual(actions[0]['action'], 'replace')
        self.assertLess(text.index('Rule B'), text.index('Rule A'))
        edited = sn.change('', self.lib, self.vals, add=['c'])[0].replace('Rule C', 'Mine')
        with self.assertRaises(ValueError): sn.change(edited, self.lib, self.vals, add=['b'], replace=True)

    def test_composed_source_syncs_into_copy_targets(self):
        from unittest.mock import patch
        from agent_utils.core import instructions as ins
        home = self.dir/'home'; src = self.dir/'src'; src.mkdir()
        (src/'common.md').write_text(sn.change('', self.lib, self.vals, add=['b'])[0])
        with patch.object(ins, 'agent_executable', lambda a: '/bin/x' if a == 'codex' else None):
            ins.apply(home, src)
            self.assertEqual({t['state'] for t in ins.targets(home, src)}, {'ok'})
        codex = (home/'.codex/AGENTS.md').read_text()
        self.assertIn('<!-- agent-utils-snippet:start id=b', codex); self.assertIn('<!-- agent-utils:start', codex)


if __name__ == '__main__':
    unittest.main()
