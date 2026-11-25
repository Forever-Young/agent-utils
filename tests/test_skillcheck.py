import json
import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_utils.core import skillcheck, skills
from tests.test_skills import make


class Runner:
    """Fake `plannotator inbox call`: records payloads, serves a canned thread."""
    def __init__(self, thread=None):
        self.calls = []; self.thread = thread or {'thread': {'messages': []}}

    def __call__(self, argv, input=None, **kw):
        payload = json.loads(input); self.calls.append((argv[3], payload))
        out = {'message_id': 'msg_1', 'thread_id': 'thr_1'} if argv[3] == 'send_message' else self.thread
        return subprocess.CompletedProcess(argv, 0, json.dumps(out), '')


class SkillCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.home = self.root/'home'; self.home.mkdir(); self.base = self.root/'state'
        self.env = skills.Env(home=self.home, policy=self.root/'config/skills.json', backups=self.root/'backups',
                              imports=self.root/'data/skills', installed=lambda a: True)
        self.cwd = self.root/'cwd'; self.cwd.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, **kw):
        return skillcheck.run([self.env], base=self.base, inbox_cwd=self.cwd, **kw)

    def kinds(self, result):
        return sorted((f['kind'], f['skill'], f['agent']) for f in result['findings'])

    def test_stage_a_finds_missing_broken_and_drift(self):
        make(self.env.store/'alpha')
        make(self.env.store/'beta', 'store text'); make(self.env.dirs['claude']/'beta', 'other text')
        gone = self.env.dirs['kiro-cli']/'gamma'; gone.parent.mkdir(parents=True)
        gone.symlink_to(os.path.relpath(self.env.store/'gamma', gone.parent))
        found = self.kinds(self.run_check())
        self.assertIn(('missing', 'alpha', 'claude'), found)
        self.assertIn(('drift', 'beta', 'claude'), found)
        self.assertIn(('broken-link', 'gamma', 'kiro-cli'), found)

    def test_no_findings_means_no_inbox_message(self):
        runner = Runner()
        result = self.run_check(inbox=True, runner=runner)
        self.assertEqual(result['findings'], []); self.assertEqual(result['inbox'], 'nothing'); self.assertEqual(runner.calls, [])

    def test_auto_applies_only_safe_actions(self):
        make(self.env.store/'alpha')
        make(self.env.store/'beta', 'store text'); make(self.env.dirs['claude']/'beta', 'other text')
        result = self.run_check(auto=True)
        self.assertTrue((self.env.dirs['claude']/'alpha').is_symlink())
        self.assertEqual({a[1] for a in result['applied']}, {'link'})
        self.assertTrue((self.env.dirs['kiro-cli']/'alpha').is_symlink()); self.assertTrue((self.env.dirs['kiro-cli']/'beta').is_symlink())
        self.assertFalse((self.env.dirs['claude']/'beta').is_symlink())              # drift left alone
        self.assertIn(('drift', 'beta', 'claude'), self.kinds(result))

    def test_auto_removes_only_our_dangling_link(self):
        link = self.env.dirs['kiro-cli']/'gamma'; link.parent.mkdir(parents=True)
        link.symlink_to(os.path.relpath(self.env.store/'gamma', link.parent))
        elsewhere = self.env.dirs['claude']/'delta'; elsewhere.parent.mkdir(parents=True)
        elsewhere.symlink_to(self.root/'nowhere')
        self.run_check(auto=True)
        self.assertFalse(link.is_symlink()); self.assertTrue(elsewhere.is_symlink())

    def test_parse_judgement_keeps_only_valid_rows(self):
        items = [{'id': 'a', 'kind': 'missing'}, {'id': 'b', 'kind': 'drift'}]
        text = 'Here:\n```json\n[{"id":"a","class":"shared","action":"link","reason":"r"},' \
               '{"id":"b","class":"shared","action":"link","reason":"x"},' \
               '{"id":"b","class":"bogus","action":"adopt"},{"id":"zzz","class":"shared","action":"link"},' \
               '{"id":"b","class":"duplicate","action":"ask-owner"}]\n```'
        out = skillcheck.parse_judgement(text, items)
        self.assertEqual(out['a']['action'], 'link'); self.assertEqual(out['b']['action'], 'ask-owner')
        self.assertEqual(skillcheck.parse_judgement('not json', items), {})

    def test_stage_b_runs_through_ask_and_never_applies_unsafe_proposals(self):
        make(self.env.store/'beta', 'store text'); make(self.env.dirs['claude']/'beta', 'other text')
        answer = self.root/'answer.md'; seen = {}
        def fake_ask(spec, prompt, **kw):
            seen['spec'] = spec; seen['prompt'] = prompt
            fid = next(f['id'] for f in skillcheck.stage_a(self.env) if f['kind'] == 'drift')
            answer.write_text(json.dumps([{'id': fid, 'class': 'duplicate', 'action': 'adopt', 'reason': 'older copy'}]))
            return {'results': [{'status': 'ok', 'answer': str(answer)}]}
        result = self.run_check(agent='codex', auto=True, ask_fn=fake_ask)
        self.assertEqual(seen['spec'], 'codex'); self.assertIn('Read-only', seen['prompt'])
        self.assertEqual(result['proposals'][result['findings'][0]['id']]['action'], 'adopt')
        self.assertFalse((self.env.dirs['claude']/'beta').is_symlink())

    def test_stage_b_failure_is_a_tool_error(self):
        make(self.env.store/'alpha')
        with self.assertRaises(ValueError):
            self.run_check(agent='codex', ask_fn=lambda *a, **k: {'results': [{'status': 'timeout'}]})

    def test_inbox_posts_once_per_change(self):
        make(self.env.store/'beta', 'store text'); make(self.env.dirs['claude']/'beta', 'other text')
        runner = Runner()
        first = self.run_check(inbox=True, runner=runner)
        self.assertEqual(first['inbox'], 'sent')
        sent = runner.calls[0][1]
        self.assertEqual(sent['thread'], 'Skills drift'); self.assertIn(':::question', sent['body'])
        self.assertIn('`skill-check:drift:user:beta:claude`', sent['body'])
        self.assertTrue(sent['idempotency_key'].startswith('skills-check-'))
        again = self.run_check(inbox=True, runner=runner)
        self.assertEqual(again['inbox'], 'unchanged')
        self.assertEqual([c[0] for c in runner.calls].count('send_message'), 1)

    def test_owner_answers_are_applied_on_next_run(self):
        make(self.env.store/'alpha'); make(self.env.store/'beta', 'store text'); make(self.env.dirs['claude']/'beta', 'other text')
        runner = Runner(); self.run_check(inbox=True, runner=runner)
        ids = {f['kind']+f['skill']+f['agent']: f['id'] for f in skillcheck.stage_a(self.env)}
        runner.thread = {'thread': {'messages': [{'questions': [
            {'key': 'q1', 'prompt': '`%s` missing for alpha. What?' % ids['missingalphaclaude'], 'answer': {'selected': ['Exclude it for claude']}},
            {'key': 'q2', 'prompt': '`%s` drift for beta. What?' % ids['driftbetaclaude'], 'answer': {'selected': ['Keep as is']}}]}]}}
        result = self.run_check(inbox=True, runner=runner)
        self.assertEqual(self.env.policy()['skills']['alpha']['exclude'], ['claude'])
        self.assertNotIn(('drift', 'beta', 'claude'), self.kinds(result))   # kept: suppressed
        self.assertNotIn(('missing', 'alpha', 'claude'), self.kinds(result))
        third = self.run_check(inbox=True, runner=runner)                   # answers are not applied twice
        self.assertEqual(self.env.policy()['skills']['alpha']['exclude'], ['claude'])
        self.assertNotIn(('drift', 'beta', 'claude'), self.kinds(third))
        make(self.env.dirs['claude']/'beta', 'changed again')               # new content: asked again
        self.assertIn(('drift', 'beta', 'claude'), self.kinds(self.run_check()))

    def test_plannotator_version_check(self):
        result = self.run_check(plannotator_version=lambda: '0.0.1')
        self.assertIn('plannotator-recipes', [f['kind'] for f in result['findings']])
        from agent_utils.plannotator.inbox import CHECKED_VERSIONS
        self.assertNotIn('plannotator-recipes', [f['kind'] for f in self.run_check(plannotator_version=lambda: CHECKED_VERSIONS[0])['findings']])

    def test_project_scope(self):
        project = self.root/'proj'; make(project/'.agents/skills/local')
        penv = skills.Env(home=self.home, project=project, installed=lambda a: True)
        result = skillcheck.run([self.env, penv], base=self.base, inbox_cwd=self.cwd)
        self.assertTrue(any(f['scope'] == str(project) and f['kind'] == 'missing' for f in result['findings']))

    def test_second_run_while_locked_is_skipped(self):
        import fcntl
        self.base.mkdir(parents=True)
        with open(self.base/'skills-check.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(self.run_check()['status'], 'busy')

    def test_parse_every(self):
        self.assertEqual(skillcheck.parse_every('1d'), 86400); self.assertEqual(skillcheck.parse_every('30m'), 1800)
        for bad in ('0d', '5m', 'daily', '1y'):
            with self.assertRaises(ValueError): skillcheck.parse_every(bad)

    def test_schedule_preview_then_apply(self):
        calls = []
        runner = lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0, '', '')
        out = []
        with mock.patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root/'st')}):
            skillcheck.schedule_command(['install', '--every', '1d', '--agent', 'codex', '--auto', '--inbox'], runner, self.home, out.append)
            self.assertFalse(skillcheck.plist_path(self.home).exists()); self.assertEqual(calls, [])
            skillcheck.schedule_command(['install', '--agent', 'codex', '--auto', '--inbox', '--apply'], runner, self.home, out.append)
            data = plistlib.loads(skillcheck.plist_path(self.home).read_bytes())
            self.assertEqual(data['StartInterval'], 86400)
            args = data['ProgramArguments']; self.assertEqual(args[args.index('skills'):], ['skills', 'check', '--agent', 'codex', '--auto', '--inbox'])
            self.assertTrue(any('bootstrap' in c for c in calls))
            with self.assertRaises(ValueError):
                skillcheck.schedule_command(['install', '--agent', 'ask'], runner, self.home, out.append)
            skillcheck.schedule_command(['remove', '--apply'], runner, self.home, out.append)
            self.assertFalse(skillcheck.plist_path(self.home).exists())


if __name__ == '__main__':
    unittest.main()
