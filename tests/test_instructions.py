import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from agent_utils.core import instructions as ins

PLANNOTATOR = '<!-- plannotator-inbox:start v9 -->\nTheir block.\n<!-- plannotator-inbox:end -->\n'


class InstructionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.home = root/'home'; self.src = root/'config/agent-utils/instructions'
        (self.home/'.codex').mkdir(parents=True); (self.home/'.codex/AGENTS.md').write_text(PLANNOTATOR)
        self.installed = {'claude', 'kiro-cli', 'codex'}
        patcher = patch.object(ins, 'agent_executable', lambda agent: '/bin/'+agent if agent in self.installed else None)
        patcher.start(); self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def states(self):
        return {(t['agent'], Path(t['path']).name): t['state'] for t in ins.targets(self.home, self.src)}

    def test_preview_writes_nothing(self):
        actions = ins.apply(self.home, self.src, dry=True)
        self.assertEqual(actions[0]['action'], 'create')
        self.assertEqual({a['action'] for a in actions[1:]}, {'link', 'write block'})
        self.assertFalse(self.src.exists()); self.assertFalse((self.home/'.claude').exists())
        self.assertEqual((self.home/'.codex/AGENTS.md').read_text(), PLANNOTATOR)

    def test_apply_links_and_copies(self):
        ins.apply(self.home, self.src)
        common = self.src/'common.md'
        self.assertEqual(common.read_text(), ins.starter())
        rule = self.home/'.claude/rules/agent-utils.md'
        self.assertTrue(rule.is_symlink()); self.assertEqual(Path(os.readlink(rule)), common)
        self.assertEqual(Path(os.readlink(self.home/'.kiro/steering/agent-utils.md')), common)
        codex = (self.home/'.codex/AGENTS.md').read_text()
        self.assertTrue(codex.startswith(PLANNOTATOR)); self.assertIn(common.read_text().strip(), codex)
        self.assertEqual(set(self.states().values()), {'ok'})
        self.assertEqual(ins.apply(self.home, self.src), [])

    def test_source_edit_needs_no_resync_except_codex(self):
        ins.apply(self.home, self.src)
        (self.src/'common.md').write_text('New rule.\n')
        self.assertEqual(self.states(), {('claude', 'agent-utils.md'): 'ok', ('kiro-cli', 'agent-utils.md'): 'ok', ('codex', 'AGENTS.md'): 'stale'})
        ins.apply(self.home, self.src)
        self.assertIn('New rule.', (self.home/'.codex/AGENTS.md').read_text())
        self.assertEqual(set(self.states().values()), {'ok'})

    def test_agent_parts(self):
        ins.apply(self.home, self.src)
        for part in ('claude', 'kiro', 'codex'): (self.src/(part+'.md')).write_text('Only %s.\n' % part)
        states = self.states()
        self.assertEqual(states[('claude', 'agent-utils-claude.md')], 'missing')
        self.assertEqual(states[('kiro-cli', 'agent-utils-kiro.md')], 'missing')
        ins.apply(self.home, self.src)
        self.assertEqual(set(self.states().values()), {'ok'})
        self.assertEqual(Path(os.readlink(self.home/'.kiro/steering/agent-utils-kiro.md')), self.src/'kiro.md')
        codex = (self.home/'.codex/AGENTS.md').read_text()
        self.assertIn('Only codex.', codex); self.assertNotIn('Only claude.', codex)
        (self.src/'claude.md').unlink()
        self.assertEqual(self.states()[('claude', 'agent-utils-claude.md')], 'orphan')
        ins.apply(self.home, self.src)
        self.assertFalse((self.home/'.claude/rules/agent-utils-claude.md').is_symlink())

    def test_hand_edits_and_foreign_files_are_kept(self):
        rules = self.home/'.claude/rules'; rules.mkdir(parents=True)
        (rules/'agent-utils.md').write_text('mine')
        ins.apply(self.home, self.src)
        codex = self.home/'.codex/AGENTS.md'
        codex.write_text(codex.read_text().replace('Shared rule', 'Edited rule'))
        self.assertEqual(self.states()[('claude', 'agent-utils.md')], 'foreign')
        self.assertEqual(self.states()[('codex', 'AGENTS.md')], 'edited')
        skipped = [a for a in ins.apply(self.home, self.src) if a['action'] == 'skip']
        self.assertEqual(len(skipped), 2); self.assertEqual((rules/'agent-utils.md').read_text(), 'mine')
        ins.apply(self.home, self.src, force=True)
        self.assertEqual(set(self.states().values()), {'ok'})
        self.assertEqual([p.read_text() for p in rules.glob('agent-utils.md.backup-*')], ['mine'])

    def test_remove_restores_codex_and_keeps_sources(self):
        ins.apply(self.home, self.src); ins.apply(self.home, self.src, remove=True)
        self.assertEqual((self.home/'.codex/AGENTS.md').read_text(), PLANNOTATOR)
        self.assertFalse((self.home/'.claude/rules/agent-utils.md').is_symlink())
        self.assertFalse((self.home/'.kiro/steering/agent-utils.md').is_symlink())
        self.assertTrue((self.src/'common.md').exists())

    def test_other_agents(self):
        self.installed |= {'pi', 'opencode', 'cursor-agent', 'qwen'}
        (self.home/'.pi/agent').mkdir(parents=True); (self.home/'.pi/agent/AGENTS.md').write_text(PLANNOTATOR)
        (self.home/'.config/opencode').mkdir(parents=True)
        oc = self.home/'.config/opencode/opencode.json'; oc.write_text('{"plugin": ["x"], "instructions": ["mine.md"]}')
        ins.apply(self.home, self.src)
        common = self.src/'common.md'
        self.assertEqual(set(self.states().values()), {'ok'})
        self.assertNotIn(('vibe', 'AGENTS.md'), self.states())
        self.assertEqual(Path(os.readlink(self.home/'.qwen/AGENTS.md')), common)
        self.assertTrue((self.home/'.pi/agent/AGENTS.md').read_text().startswith(PLANNOTATOR))
        self.assertEqual(json.loads(oc.read_text()), {'plugin': ['x'], 'instructions': ['mine.md', str(common)]})
        mdc = (self.home/'.cursor/rules/agent-utils.mdc').read_text()
        self.assertTrue(mdc.startswith('---\n') and 'alwaysApply: true' in mdc and common.read_text().strip() in mdc)
        (self.src/'opencode.md').write_text('OpenCode only.\n'); (self.src/'cursor.md').write_text('Cursor only.\n')
        common.write_text('Changed.\n')
        states = self.states()
        self.assertEqual(states[('opencode', 'opencode.json')], 'stale'); self.assertEqual(states[('cursor-agent', 'agent-utils.mdc')], 'stale')
        self.assertEqual(states[('qwen', 'AGENTS.md')], 'ok')
        ins.apply(self.home, self.src)
        self.assertEqual(set(self.states().values()), {'ok'})
        self.assertIn('Cursor only.', (self.home/'.cursor/rules/agent-utils.mdc').read_text())
        self.assertEqual(json.loads(oc.read_text())['instructions'], ['mine.md', str(common), str(self.src/'opencode.md')])
        ins.apply(self.home, self.src, remove=True)
        self.assertEqual(json.loads(oc.read_text()), {'plugin': ['x'], 'instructions': ['mine.md']})
        self.assertEqual((self.home/'.pi/agent/AGENTS.md').read_text(), PLANNOTATOR)
        self.assertFalse((self.home/'.cursor/rules/agent-utils.mdc').exists())
        self.assertFalse((self.home/'.qwen/AGENTS.md').is_symlink())

    def test_command_status_exit_code(self):
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.src.parent.parent)}), patch('pathlib.Path.home', return_value=self.home):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(ins.command(['status']), 1)
                self.assertEqual(ins.command(['apply']), 0); self.assertFalse(self.src.exists())
                self.assertEqual(ins.command(['apply', '--yes']), 0)
                self.assertEqual(ins.command(['status']), 0)


if __name__ == '__main__':
    unittest.main()
