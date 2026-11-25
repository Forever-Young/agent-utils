import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from agent_utils.core import audit, skills

AGENTS = """# Demo

## Style

- Keep functions small and readable for reviewers.
- Write tests for every behaviour change you make.
- Never commit secrets or credentials to the repository.

## Investigating failed deploys

When investigating a failed deploy, follow these steps:

1. Open the pipeline page at https://ci.example.com/demo and find the failed job.
2. Fetch the job log with `democtl logs --job ID --tail 200`.
3. Check the release tag with `git describe --tags --abbrev=0`.
4. Compare the config with `democtl config diff --env staging`.

```sh
democtl rollback --env staging --to previous
```

Report the job ID and the root cause in the ticket.
"""
STEERING = """---
inclusion: always
---
# Style

- Keep functions small and readable for reviewers.
- Write tests for every behaviour change you make.
- Never commit secrets or credentials to the repository.
"""
CLAUDE_RULES = """# Style

- Keep functions small and readable for reviewers.
- Write tests for every behaviour change you make, always.
- Prefer composition over inheritance in the domain layer.
"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text); return path


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name).resolve()
        self.home = self.root/'home'; self.home.mkdir()
        self.p = self.root/'ws'
        write(self.p/'AGENTS.md', AGENTS); write(self.p/'.kiro/steering/style.md', STEERING)
        write(self.p/'.claude/rules/style.md', CLAUDE_RULES); write(self.p/'CLAUDE.md', '@AGENTS.md\n')
        self.no_git = lambda path: None

    def tearDown(self):
        self.tmp.cleanup()

    def run_audit(self):
        return audit.audit(self.p, home=self.home, roots=self.no_git)

    def test_rule_section_is_not_a_candidate_and_procedure_is(self):
        secs = {s['heading']: s for s in audit.sections(AGENTS)}
        self.assertFalse(audit.is_candidate(secs['Style']))
        self.assertTrue(audit.is_candidate(secs['Investigating failed deploys']))
        cands = self.run_audit()['candidates']
        self.assertEqual([(c['file'], c['heading'], c['start']) for c in cands], [('AGENTS.md', 'Investigating failed deploys', 9)])
        self.assertEqual(cands[0]['name'], 'investigating-failed-deploys')
        self.assertIn('Use when investigating a failed deploy', cands[0]['description'])

    def test_short_procedure_stays_a_rule(self):
        sec = {'heading': 'Release', 'body': 'Run `make release --tag X` then `git push --tags`.'}
        self.assertFalse(audit.is_candidate(sec))

    def test_headings_inside_fences_are_ignored(self):
        text = '## A\n\n```\n## not a heading\n```\n\n## B\n\nx\n'
        self.assertEqual([s['heading'] for s in audit.sections(text)], ['A', 'B'])

    def test_compare_verdicts_for_every_agent_rule_file(self):
        rules = {r['file']: r for r in self.run_audit()['rules']}
        self.assertEqual(rules['.kiro/steering/style.md']['verdict'], 'duplicate')
        claude = rules['.claude/rules/style.md']
        self.assertEqual(claude['verdict'], 'overlaps')
        self.assertEqual(claude['differs'][0]['source'], 'write tests for every behaviour change you make')
        self.assertEqual(rules['CLAUDE.md']['verdict'], 'imports')

    def test_codex_override_and_cursor_rules_are_found(self):
        write(self.p/'AGENTS.override.md', 'x\n'); write(self.p/'.cursor/rules/a.mdc', 'y\n')
        found = {str(k.relative_to(self.p)): v for k, v in audit.rule_files(self.p).items()}
        self.assertEqual(found['AGENTS.override.md'], 'codex'); self.assertEqual(found['.cursor/rules/a.mdc'], 'cursor-agent')

    def test_claude_without_import_is_reported(self):
        write(self.p/'CLAUDE.md', '# Claude\n\nSome Claude-only text that is long enough.\n')
        self.assertIn('claude', self.run_audit()['no_source'])

    def test_pairs_shared_by_agent_files_but_not_agents_md(self):
        extra = '\n'.join('- A rule shared by two agents only, number %d.' % i for i in range(3))
        write(self.p/'.kiro/steering/extra.md', extra+'\n'); write(self.p/'.claude/rules/extra.md', extra+'\n')
        pairs = self.run_audit()['pairs']
        self.assertTrue(any(set(p['files']) == {'.kiro/steering/extra.md', '.claude/rules/extra.md'} for p in pairs))

    def test_prune_preview_then_apply_with_backup(self):
        backups = self.root/'backups'
        actions, diff = audit.prune(self.p, base=backups)
        self.assertEqual({a['path']: a['action'] for a in actions},
                         {'.kiro/steering/style.md': 'delete', '.claude/rules/style.md': 'prune'})
        self.assertTrue((self.p/'.kiro/steering/style.md').exists())
        self.assertIn('-- Keep functions small', diff)
        audit.prune(self.p, dry=False, base=backups)
        self.assertFalse((self.p/'.kiro/steering/style.md').exists())
        kept = (self.p/'.claude/rules/style.md').read_text()
        self.assertNotIn('Keep functions small', kept); self.assertIn('always', kept); self.assertIn('# Style', kept)
        self.assertEqual(next(backups.glob('*/rules/.kiro/steering/style.md')).read_text(), STEERING)
        self.assertEqual((self.p/'CLAUDE.md').read_text(), '@AGENTS.md\n')
        self.assertEqual((self.p/'AGENTS.md').read_text(), AGENTS)

    def test_prune_keeps_front_matter_and_partial_fences(self):
        source = 'Run the whole test suite before you push changes.\n'
        raw = '---\ninclusion: always\n---\n# T\n\n- Run the whole test suite before you push changes.\n- Kiro only rule that stays here.\n\n```\nRun the whole test suite before you push changes.\nanother command line here\n```\n'
        out = audit.pruned(raw, source)
        self.assertTrue(out.startswith('---\ninclusion: always\n---\n'))
        self.assertNotIn('- Run the whole', out); self.assertIn('another command line here', out); self.assertIn('Kiro only', out)

    def test_extract_preview_then_apply_links_and_writes_patch(self):
        subprocess.run(['git', 'init', '-q', str(self.p)], check=True)
        actions, skill, patch, path = audit.extract(self.p, 'AGENTS.md', 'L9', 'failed-deploys', home=self.home, base=self.root/'audits')
        self.assertIsNone(path); self.assertFalse((self.p/'.agents').exists())
        self.assertIn('name: failed-deploys', skill); self.assertIn('# Investigating failed deploys', skill)
        actions, skill, patch, path = audit.extract(self.p, 'AGENTS.md', 'Investigating failed deploys', 'failed-deploys',
                                                     dry=False, home=self.home, base=self.root/'audits')
        self.assertTrue((self.p/'.agents/skills/failed-deploys/SKILL.md').is_file())
        env = skills.Env(home=self.home, project=self.p)
        self.assertEqual({skills.entry_state(env, 'failed-deploys', a) for a in env.dirs}, {'ok'})
        self.assertEqual((self.p/'AGENTS.md').read_text(), AGENTS)
        subprocess.run(['git', '-C', str(self.p), 'apply', str(path)], check=True)
        text = (self.p/'AGENTS.md').read_text()
        self.assertIn('## Investigating failed deploys\n\nFor investigating failed deploys, use the `failed-deploys` skill.\n', text)
        self.assertNotIn('democtl', text); self.assertIn('## Style', text)
        with self.assertRaises(ValueError): audit.extract(self.p, 'AGENTS.md', 'L9', 'failed-deploys', home=self.home)

    def test_extract_refuses_ambiguous_heading(self):
        write(self.p/'AGENTS.md', '## A\n\nx\n\n## A\n\ny\n')
        with self.assertRaises(ValueError) as err: audit.extract(self.p, 'AGENTS.md', 'A', 'a', home=self.home)
        self.assertIn('L1', str(err.exception))

    def test_visibility_child_repo_misses_root_skills(self):
        write(self.p/'.agents/skills/root-skill/SKILL.md', '---\nname: root-skill\n---\n')
        write(self.p/'.kiro/skills/kiro-skill/SKILL.md', '---\nname: kiro-skill\n---\n')
        (self.p/'svc/.git').mkdir(parents=True); (self.p/'lib').mkdir()
        vis = audit.visibility(self.p, roots=lambda child: child)
        self.assertEqual([c['path'] for c in vis['children']], ['svc'])
        self.assertEqual(vis['children'][0]['missing'], {'codex': ['root-skill'], 'kiro-cli': ['kiro-skill']})

    def test_searched_walks_up_to_git_root_only_for_repo_mode(self):
        top = self.p; start = self.p/'a/b'; start.mkdir(parents=True)
        self.assertEqual(audit.searched(start, 'repo', top), [start, self.p/'a', self.p])
        self.assertEqual(audit.searched(start, 'cwd', top), [start])

    def test_project_skill_copies_are_listed(self):
        write(self.p/'.claude/skills/notes/SKILL.md', '---\nname: notes\n---\nA\n')
        rows = {s['name']: s for s in self.run_audit()['skills']}
        self.assertEqual(rows['notes']['agents']['claude'], 'only-here')

    def test_inbox_body_and_send(self):
        report = self.run_audit()
        body = audit.inbox_body(report)
        self.assertEqual(body.count(':::question'), 3)
        self.assertIn('Extract as `investigating-failed-deploys`', body)
        calls = []
        def runner(argv, **kw):
            calls.append((argv, json.loads(kw['input']), kw['cwd']))
            return subprocess.CompletedProcess(argv, 0, '{"url": "http://x/#thread=1"}', '')
        sent = audit.send_inbox(report, [self.root/'report.md'], runner)
        self.assertEqual(sent['url'], 'http://x/#thread=1')
        argv, payload, cwd = calls[0]
        self.assertEqual(argv[:4], ['plannotator', 'inbox', 'call', 'send_message']); self.assertEqual(cwd, str(self.p))
        self.assertTrue(payload['idempotency_key'].startswith('agents-audit-'))
        self.assertEqual(payload['attachments'], [str(self.root/'report.md')])

    def test_command_writes_report_outside_project(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root/'state'), 'HOME': str(self.home)}), \
                mock.patch.object(audit, 'git_root', self.no_git), contextlib.redirect_stdout(out):
            self.assertEqual(skills.command(['audit', str(self.p)]), 0)
        self.assertIn('# Instructions audit: ws', out.getvalue())
        reports = list((self.root/'state').rglob('report.json'))
        self.assertEqual(len(reports), 1); self.assertNotIn(str(self.p), str(reports[0].parent))
        self.assertEqual(json.loads(reports[0].read_text())['project'], str(self.p))

    def test_stage_b_uses_read_only_ask(self):
        report = self.run_audit()
        with mock.patch('agent_utils.core.ask.ask', return_value={'results': []}) as asked:
            audit.stage_b(report, 'codex', 30)
        spec, prompt = asked.call_args[0][:2]
        self.assertEqual(spec, 'codex'); self.assertIn('lines 9-', prompt); self.assertEqual(asked.call_args[1]['cwd'], str(self.p))


if __name__ == '__main__':
    unittest.main()
