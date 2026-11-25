import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from agent_utils.core import ask

# A fake agent CLI: prints help listing the adapter's flags, otherwise records its argv
# and environment, then answers. FAKE_MODE controls failure cases.
FAKE = r'''#!%s
import json, os, sys, time
flags = json.loads(%r)
name = os.path.basename(sys.argv[0])
if any(a in ('--help', 'help') for a in sys.argv[1:]):
    if os.environ.get('FAKE_OLD') == name: print('usage: old version')
    else: print('usage:', ' '.join(flags[name]))
    sys.exit(0)
mode = os.environ.get('FAKE_MODE', '')
if mode == 'sleep': time.sleep(30)
if mode == 'big': sys.stdout.write('x' * 5000); sys.stdout.flush(); time.sleep(30)
with open(os.environ['FAKE_LOG'] + '.' + name, 'w') as f:
    json.dump({'argv': sys.argv[1:], 'env': dict(os.environ), 'cwd': os.getcwd()}, f)
if mode == 'fail': sys.stderr.write('boom\n'); sys.exit(3)
if '--output-last-message' in sys.argv:
    open(sys.argv[sys.argv.index('--output-last-message') + 1], 'w').write('codex answer')
    print('event log')
else:
    print('\x1b[1m' + name + ' answer\x1b[0m')
'''


class AskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.bin = root/'bin'; self.bin.mkdir(); self.work = root/'work'; self.work.mkdir()
        self.runs = root/'runs'; self.log = str(root/'log'); self.home = root/'home'; self.home.mkdir()
        flags = json.dumps({a: v[1] for a, v in ask.ADAPTERS.items()})
        for agent in ('claude', 'codex', 'kiro-cli'):
            path = self.bin/agent; path.write_text(FAKE % (sys.executable, flags)); path.chmod(0o755)
        self.env = {'PATH': str(self.bin)+os.pathsep+'/usr/bin:/bin', 'HOME': str(self.home), 'FAKE_LOG': self.log}

    def tearDown(self):
        self.tmp.cleanup()

    def which(self, agent):
        path = self.bin/agent
        return str(path) if path.exists() else None

    def run_ask(self, spec, env=None, **kw):
        merged = dict(self.env, **(env or {}))
        old = os.environ.get('FAKE_OLD')
        if 'FAKE_OLD' in merged:os.environ['FAKE_OLD'] = merged['FAKE_OLD']  # check() uses os.environ
        try:return ask.ask(spec, 'Why does it fail?', self.work, env=merged, which=self.which, base=self.runs, **kw)
        finally:
            if old is None:os.environ.pop('FAKE_OLD', None)
            else:os.environ['FAKE_OLD'] = old

    def recorded(self, agent):
        return json.loads(Path(self.log+'.'+agent).read_text())

    def test_parallel_run_writes_answers_and_read_only_flags(self):
        report = self.run_ask('claude,codex,kiro')
        self.assertEqual([r['status'] for r in report['results']], ['ok', 'ok', 'ok'])
        answers = {r['target']: Path(r['answer']).read_text() for r in report['results']}
        self.assertEqual(answers, {'claude': 'claude answer\n', 'codex': 'codex answer\n', 'kiro-cli': 'kiro-cli answer\n'})
        claude = self.recorded('claude')['argv']
        self.assertIn('--permission-prompts', claude); self.assertIn('Read,Grep,Glob', claude)
        self.assertEqual(claude[-2], '--')
        codex = self.recorded('codex')['argv']
        self.assertEqual(codex[codex.index('--sandbox')+1], 'read-only')
        self.assertIn('--trust-tools=fs_read,grep,glob,code', self.recorded('kiro-cli')['argv'])
        self.assertTrue((Path(report['run'])/'prompt.md').is_file())

    def test_unknown_target_is_an_error_and_missing_is_reported(self):
        with self.assertRaises(ValueError):self.run_ask('nobody')
        report = self.run_ask('claude,pi')
        self.assertEqual([r['status'] for r in report['results']], ['ok', 'missing'])

    def test_all_skips_the_caller_and_uninstalled_agents(self):
        report = self.run_ask('all', env={'CODEX_THREAD_ID': 'abc'})
        self.assertEqual([r['target'] for r in report['results']], ['claude', 'kiro-cli'])
        self.assertEqual(report['caller'], 'codex')

    def test_adapter_flags_are_checked_against_help(self):
        report = self.run_ask('claude,codex', env={'FAKE_OLD': 'codex'})
        codex = report['results'][1]
        self.assertEqual(codex['status'], 'unsupported'); self.assertIn('--sandbox', codex['missing_flags'])
        self.assertFalse(Path(self.log+'.codex').exists())

    def test_child_env_strips_session_identity_and_sets_depth(self):
        self.run_ask('claude', env={'AGTERM_SESSION_ID': 'row', 'AGTERM_UTILS_RECORD': 'r', 'KIRO_SESSION_ID': 'k',
                                    'CLAUDECODE': '1', 'KEEP': 'yes'})
        env = self.recorded('claude')['env']
        for var in ('AGTERM_SESSION_ID', 'AGTERM_UTILS_RECORD', 'KIRO_SESSION_ID', 'CLAUDECODE'):self.assertNotIn(var, env)
        self.assertEqual(env['KEEP'], 'yes'); self.assertEqual(env[ask.DEPTH_VAR], '1')
        self.assertEqual(Path(self.recorded('claude')['cwd']).resolve(), self.work.resolve())

    def test_nested_ask_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'nested'):self.run_ask('claude', env={ask.DEPTH_VAR: '1'})
        self.assertEqual(self.run_ask('claude', env={ask.DEPTH_VAR: '1', ask.MAX_DEPTH_VAR: '2'})['results'][0]['status'], 'ok')

    def test_failure_timeout_and_size_cap(self):
        failed = self.run_ask('claude', env={'FAKE_MODE': 'fail'})['results'][0]
        self.assertEqual((failed['status'], failed['exit_code']), ('failed', 3)); self.assertIn('boom', failed['stderr_tail'])
        started = time.monotonic()
        slow = self.run_ask('claude', env={'FAKE_MODE': 'sleep'}, timeout=1)['results'][0]
        self.assertEqual(slow['status'], 'timeout'); self.assertLess(time.monotonic()-started, 15)
        original = ask.ANSWER_CAP
        try:
            ask.ANSWER_CAP = 1000
            big = ask.run_one('claude', self.which('claude'), 'p', self.work, self.make_run(), dict(self.env, FAKE_MODE='big'), 20, cap=1000)
        finally:ask.ANSWER_CAP = original
        self.assertEqual(big['status'], 'too-large'); self.assertTrue(big['truncated'])

    def make_run(self):
        path = self.runs/'manual'; path.mkdir(parents=True, exist_ok=True); return path

    def test_secret_guard_for_files_and_diffs(self):
        (self.work/'.env').write_text('TOKEN=1'); (self.work/'notes.txt').write_text('fine')
        (self.work/'k.txt').write_text('-----BEGIN OPENSSH PRIVATE KEY-----\nabc')
        for name in ('.env', 'k.txt'):
            with self.assertRaisesRegex(ValueError, 'Refusing'):self.run_ask('claude', files=[name])
        self.run_ask('claude', files=['notes.txt'])
        self.assertIn('## File: notes.txt', self.recorded('claude')['argv'][-1])
        git = ['git', '-C', str(self.work)]
        subprocess.run(git+['init', '-q'], check=True)
        subprocess.run(git+['add', 'notes.txt'], check=True)
        (self.work/'notes.txt').write_text('changed')
        self.run_ask('claude', diff='unstaged')
        self.assertIn('+changed', self.recorded('claude')['argv'][-1])
        subprocess.run(git+['add', '-f', '.env'], check=True)
        with self.assertRaisesRegex(ValueError, 'secret-looking'):self.run_ask('claude', diff='staged')
        with self.assertRaises(ValueError):self.run_ask('claude', diff='--output=x')

    def test_context_cap(self):
        (self.work/'big.txt').write_text('a'*(ask.CONTEXT_CAP+10))
        with self.assertRaisesRegex(ValueError, 'limit'):self.run_ask('claude', files=['big.txt'])

    def test_preamble_lists_files_the_target_does_not_load(self):
        (self.work/'AGENTS.md').write_text('a'); (self.work/'CLAUDE.md').write_text('c')
        (self.work/'.kiro/steering').mkdir(parents=True)
        (self.home/'.claude').mkdir(); (self.home/'.claude/CLAUDE.md').write_text('g')
        codex = ask.instruction_files('codex', self.work, 'claude', self.home)
        self.assertEqual([Path(p).name for p in codex], ['CLAUDE.md', 'steering', 'CLAUDE.md'])
        self.assertTrue(all(Path(p).is_absolute() for p in codex))
        claude = ask.instruction_files('claude', self.work, 'codex', self.home)
        self.assertEqual([Path(p).name for p in claude], ['AGENTS.md', 'steering'])
        self.assertIn('You are consulted by Claude', ask.preamble('codex', self.work, 'claude', self.home))

    def test_target_args_go_before_the_prompt(self):
        argv = ask.build('codex', 'codex', 'P', '/w', '/a', ['-c', 'x=1'])
        self.assertEqual(argv[-4:], ['-c', 'x=1', '--', 'P'])
        argv = ask.build('kimi', 'kimi', 'P', '/w', '/a', ['--model', 'm'])
        self.assertEqual(argv[-4:], ['--model', 'm', '--prompt', 'P'])
        argv = ask.build('opencode', 'opencode', 'P', '/w', '/a', ['--model', 'm'])
        self.assertEqual(argv[-3:], ['--model', 'm', 'P'])
        self.assertEqual(ask._extra(['kiro=--model x']), {'kiro-cli': ['--model', 'x']})

    def test_every_adapter_puts_the_prompt_last(self):
        for agent, (_, flags, builder) in ask.ADAPTERS.items():
            argv = builder('PROMPT', '/w', '/a')
            self.assertEqual(argv[-1], 'PROMPT', agent)
            used = [a.split('=')[0] for a in argv if a.startswith('-') and a != '--']
            self.assertTrue(set(used) <= set(flags), (agent, set(used)-set(flags)))


class BundledSkillTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]/'skills'

    def test_skills_are_portable_and_scripts_compile(self):
        from agent_utils.core.skills import claude_only
        for name in ('ask-agents', 'agent-history'):
            path = self.ROOT/name
            self.assertTrue((path/'SKILL.md').read_text().startswith('---\nname: %s\n' % name))
            self.assertEqual(claude_only(path), [], name)
            for script in path.glob('scripts/*.py'):compile(script.read_text(), str(script), 'exec')
            for ref in __import__('re').findall(r'references/[\w.-]+', (path/'SKILL.md').read_text()):
                self.assertTrue((path/ref).is_file(), ref)


if __name__ == '__main__':
    unittest.main()
