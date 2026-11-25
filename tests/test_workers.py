"""Worker dispatch acceptance using synthetic state, processes and terminals."""
from argparse import Namespace
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent_utils.cli import main, atomic_write
from agent_utils.agterm.sessions import save, records, launch
from agent_utils.agterm import workers


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        env = patch.dict(os.environ, {'XDG_STATE_HOME': self.root})
        env.start();self.addCleanup(env.stop)
        self.record = dict(id='worker', project='demo', project_name='Demo', window='Team',
                           title='KEY-1 feature', task='KEY-1', agent='claude',
                           conversation_id='00000000-0000-4000-8000-000000000001', terminal_id='terminal',
                           roots=[self.root], updated_at='2026-01-01', status='launched')
        self.row = dict(id='terminal', window_id='wid', foreground=['/bin/claude', '--resume', self.record['conversation_id']])
        self.provider = dict(sessionId=self.record['conversation_id'], pid=123, status='idle')
        self.ag = Mock(dry=False)
        self.ag.launch.return_value='terminal'
        self.config = str(Path(self.root) / 'config.json')
        atomic_write(self.config, {'version': 1, 'windows': [{'name': 'Team', 'default_agent': 'claude',
                     'projects': [{'id': 'demo', 'name': 'Demo', 'roots': [self.root]}]}]})

    def cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):main(['--config', self.config, *argv])
        return json.loads(out.getvalue())

    def probes(self, rows=None, inventory=None):
        p1 = patch('agent_utils.agterm.sessions.live_rows', return_value=[self.row] if rows is None else rows)
        p2 = patch('agent_utils.agterm.workers.claude_inventory', return_value=[self.provider] if inventory is None else inventory)
        p1.start();p2.start();self.addCleanup(p1.stop);self.addCleanup(p2.stop)

    def test_task_filter_does_not_print_unrelated_records_and_queries_archived(self):
        save(dict(self.record, archived=True, aliases=['temporary-key']))
        save(dict(self.record, id='other', task='OTHER-2', project='other'))
        self.probes()
        result = self.cli('session', 'list', '--task', 'temporary-key')
        self.assertEqual([r['id'] for r in result], ['worker'])
        self.assertTrue(result[0]['alive'])
        self.assertEqual(result[0]['state'], 'idle')
        self.assertTrue(records()['worker']['last_seen'])
        self.assertEqual(self.cli('session', 'list', '--task', 'missing'), [])

    def test_bind_rename_and_all_identity_filters(self):
        save(self.record)
        self.cli('task', 'bind', '--task', 'KEY-1', '--pr', 'https://example.invalid/o/r/pull/5/',
                 '--branch', 'feature', '--alias', 'old-key', '--repo', self.root)
        self.cli('task', 'rename', '--task', 'old-key', '--to-task', 'KEY-2')
        self.probes()
        for flags in [('--task', 'KEY-1'), ('--task', 'KEY-2'), ('--task', 'old-key'),
                      ('--pr', 'https://example.invalid/o/r/pull/5'), ('--branch', 'feature'),
                      ('--conversation', self.record['conversation_id']), ('--repo', self.root)]:
            self.assertEqual([r['id'] for r in self.cli('session', 'list', *flags)], ['worker'])
        self.assertEqual(self.cli('session', 'list', '--task', 'KEY-2', '--branch', 'wrong'), [])

    def test_alias_collision_is_rejected(self):
        save(self.record);save(dict(self.record, id='other', task='OTHER', conversation_id='different'))
        with self.assertRaisesRegex(ValueError, 'already belongs'):
            self.cli('task', 'bind', '--task', 'KEY-1', '--alias', 'OTHER')

    def test_ensure_live_reuses_and_preserves_multiline_without_clipboard(self):
        save(self.record);self.probes()
        prompt = "Review this:\nline two with ; and $(literal)."
        with patch('agent_utils.cli.Agterm', return_value=self.ag):
            result = self.cli('ensure', '--project', 'demo', '--task', 'KEY-1', '--prompt', prompt)
        self.assertEqual(result['action'], 'reused')
        self.assertEqual(result['delivery'], 'submitted')
        self.ag.launch.assert_not_called()
        call = self.ag.call.call_args.args
        self.assertEqual(call[:2], ('session', 'type'))
        self.assertEqual(call[-4:], ('--target', 'terminal', '--window', 'wid'))
        self.assertEqual(call[2].count('\n'), 1)
        messages = list((Path(self.root) / 'agent-utils/messages/worker').glob('*.json'))
        self.assertEqual(json.loads(messages[0].read_text())['prompt'], prompt)
        self.assertEqual(len(records()), 1)

    def test_ensure_delivers_to_live_codex_and_kiro_workers_only_when_foreground_matches(self):
        for agent, exe in (('codex', '/bin/codex'), ('kiro-cli', '/bin/kiro-cli')):
            record = dict(self.record, agent=agent, conversation_id=None, provider_pid=os.getpid(),
                          provider_birth=workers.process_birth(os.getpid()))
            save(record)
            self.probes(rows=[dict(self.row, foreground=[exe, 'chat'])], inventory=[])
            self.ag.call.reset_mock()
            with patch('agent_utils.cli.Agterm', return_value=self.ag):
                result = self.cli('ensure', '--project', 'demo', '--agent', agent, '--task', 'KEY-1', '--prompt', 'more')
            self.assertEqual((result['action'], result['delivery']), ('reused', 'submitted'))
            self.assertEqual(self.ag.call.call_args.args[:2], ('session', 'type'))
            with patch('agent_utils.agterm.sessions.live_rows', return_value=[dict(self.row, foreground=['/bin/zsh'])]), \
                 patch('agent_utils.cli.Agterm', return_value=self.ag):
                with self.assertRaisesRegex(ValueError, 'refusing to type'):
                    self.cli('ensure', '--project', 'demo', '--agent', agent, '--task', 'KEY-1', '--prompt', 'again')

    def test_no_delivery_to_shell_unknown_or_detached_worker(self):
        save(self.record)
        for rows, inventory, error in [([dict(self.row, foreground=['/bin/zsh'])], [self.provider], 'not Claude'),
                                       ([], [self.provider], 'not attached'),
                                       ([self.row], None, 'unknown')]:
            with patch('agent_utils.agterm.sessions.live_rows', return_value=rows), \
                 patch('agent_utils.agterm.workers.claude_inventory', return_value=inventory), \
                 patch('agent_utils.cli.Agterm', return_value=self.ag):
                with self.assertRaisesRegex(ValueError, error):
                    self.cli('ensure', '--project', 'demo', '--task', 'KEY-1', '--prompt', 'follow-up')
        self.ag.launch.assert_not_called();self.ag.call.assert_not_called()

    def test_ensure_resume_keeps_record_and_exact_conversation(self):
        save(dict(self.record, archived=True));self.probes(rows=[], inventory=[])
        self.ag.launch.return_value = 'replacement-terminal'
        # Simulate the runner's concurrent state write before terminal creation returns.
        def started(*args):
            with workers.locked_registry() as registry:
                registry['sessions']['worker'].update(provider_pid=42, provider_birth='birth')
            return 'replacement-terminal'
        self.ag.launch.side_effect = started
        with patch('agent_utils.cli.Agterm', return_value=self.ag):
            result = self.cli('ensure', '--project', 'demo', '--task', 'KEY-1', '--prompt', 'follow-up')
        argv = self.ag.launch.call_args.args[2]
        self.assertEqual(argv[argv.index('--resume') + 1], self.record['conversation_id'])
        self.assertEqual(argv[-1], 'follow-up')
        self.assertEqual(result['action'], 'resumed')
        self.assertEqual(records()['worker']['provider_pid'], 42)
        self.assertFalse(records()['worker']['archived'])
        self.assertEqual(len(records()), 1)

    def test_ensure_create_and_repeat_never_creates_duplicate(self):
        self.ag.launch.return_value = 'terminal'
        self.probes(inventory=[])
        def started(window, project, command, cwd):
            key = command[4]
            with workers.locked_registry() as registry:
                registry['sessions'][key].update(provider_pid=42, provider_birth='birth')
            return 'terminal'
        self.ag.launch.side_effect = started
        with patch('agent_utils.cli.Agterm', return_value=self.ag), patch('agent_utils.cli.agent_executable', return_value='/bin/claude'):
            result = self.cli('ensure', '--project', 'demo', '--task', 'NEW', '--prompt', 'build')
        self.assertEqual(result['action'], 'created')
        self.assertEqual(len(records()), 1)
        self.assertIsNotNone(result['conversation_id'])
        with patch('agent_utils.cli.agent_executable', return_value='/bin/claude'):
            with self.assertRaisesRegex(ValueError, 'already registered'):
                launch(self.ag, {'name': 'Team'}, {'id': 'demo','name': 'Demo','roots': [self.root]}, 'claude',
                       Namespace(task='NEW',session=None,prompt='again',command='new'))
        self.assertEqual(self.ag.launch.call_count, 1)

    def test_ensure_creates_other_agent_but_refuses_follow_up_and_agent_switch(self):
        self.probes(inventory=[])
        self.ag.launch.return_value = 'terminal'
        with patch('agent_utils.cli.Agterm', return_value=self.ag), patch('agent_utils.cli.agent_executable', return_value='/bin/codex'):
            result = self.cli('ensure', '--project', 'demo', '--agent', 'codex', '--task', 'CX-1', '--prompt', 'build')
            self.assertEqual(result['action'], 'created')
            with self.assertRaisesRegex(ValueError, 'not attached|not running|refusing'):
                self.cli('ensure', '--project', 'demo', '--agent', 'codex', '--task', 'CX-1', '--prompt', 'more')
            with self.assertRaisesRegex(ValueError, 'never run two workers'):
                self.cli('ensure', '--project', 'demo', '--agent', 'claude', '--task', 'CX-1', '--prompt', 'more')

    def test_ambiguous_matches_refuse_to_spawn(self):
        save(self.record);save(dict(self.record, id='other', conversation_id='other-conversation'))
        with patch('agent_utils.cli.Agterm', return_value=self.ag):
            with self.assertRaisesRegex(ValueError, 'Multiple workers'):
                self.cli('ensure', '--project', 'demo', '--task', 'KEY-1', '--prompt', 'follow-up')
        self.ag.launch.assert_not_called()

    def test_record_and_branch_cannot_point_to_other_project(self):
        save(dict(self.record, project='another'))
        with patch('agent_utils.cli.Agterm', return_value=self.ag):
            with self.assertRaisesRegex(ValueError, 'does not belong'):
                self.cli('ensure', '--project', 'demo', '--record', 'worker', '--prompt', 'follow-up')
        self.ag.launch.assert_not_called()

    def test_runner_tracks_real_synthetic_process_exit(self):
        from agent_utils.agterm.runner import wrapped
        for code in (0, 7):
            save(self.record)
            command = wrapped('worker', [sys.executable, '-c', 'raise SystemExit(' + str(code) + ')'])
            result = subprocess.run(command, cwd='/', capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, code, result.stderr)
            record = records()['worker']
            self.assertEqual(record['exit_code'], code)
            self.assertEqual(record['status'], 'failed' if code else 'completed')
            self.assertTrue(record['provider_pid']);self.assertTrue(record['exited_at'])

    def test_live_provider_overrides_stale_exit_and_absent_row_does_not_resume(self):
        saved = dict(self.record, exited_at='old', status='completed')
        observed = workers.observe(saved, ({}, [], [self.provider]))
        self.assertTrue(observed['alive']);self.assertEqual(observed['state'], 'idle')
        save(saved);self.probes(rows=[])
        with patch('agent_utils.cli.Agterm', return_value=self.ag):
            with self.assertRaisesRegex(ValueError, 'not attached'):
                self.cli('ensure', '--project', 'demo', '--task', 'KEY-1', '--prompt', 'follow-up')
        self.ag.launch.assert_not_called()

    def test_new_root_and_cwd_are_used(self):
        sub = Path(self.root)/'sub';sub.mkdir()
        with patch('agent_utils.cli.Agterm', return_value=self.ag), patch('agent_utils.cli.agent_executable', return_value='/bin/claude'):
            with redirect_stdout(io.StringIO()):
                main(['--config', self.config, 'new', '--project', 'demo', '--root', str(sub), '--cwd', self.root, '--name', 'Custom worker'])
        self.assertEqual(self.ag.launch.call_args.args[3], str(Path(self.root).resolve()))
        record = next(iter(records().values()))
        self.assertEqual(record['roots'], [str(sub.resolve())])
        self.assertEqual(record['title'], 'Custom worker')

    def test_known_duplicate_conversation_uses_attached_record(self):
        save(dict(self.record, id='old', terminal_id='gone', updated_at='2026-01-02'))
        save(self.record);self.probes()
        rows=self.cli('session','list','--task','KEY-1')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['id'],'worker')
        self.assertEqual(rows[0]['duplicate_records'],['old'])
        with patch('agent_utils.cli.Agterm',return_value=self.ag):
            result=self.cli('ensure','--project','demo','--task','KEY-1','--prompt','follow-up')
        self.assertEqual(result['id'],'worker')
        self.ag.launch.assert_not_called()

    def test_concurrent_task_launches_only_register_once(self):
        script = """
import os,time
from argparse import Namespace
from agent_utils.agterm.sessions import launch
from unittest.mock import patch
class Adapter:
    dry=False
    def launch(self,*args):time.sleep(.15);return 'terminal'
    def window(self,*args):return 'wid'
    def call(self,*args,**kwargs):return None
try:
    with patch('agent_utils.cli.agent_executable',return_value='/bin/echo'):
        launch(Adapter(),{'name':'Team'},{'id':'demo','name':'Demo','roots':[os.environ['XDG_STATE_HOME']]},
               'claude',Namespace(task='KEY-1',session=None,prompt='text',command='new'))
except ValueError:raise SystemExit(2)
"""
        procs=[subprocess.Popen([sys.executable,'-c',script],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
        for proc in procs:proc.communicate(timeout=10)
        self.assertEqual(sorted(p.returncode for p in procs),[0,2])
        self.assertEqual(len(records()),1)

    def test_unknown_inventory_prevents_resume_even_with_old_exit(self):
        save(dict(self.record,exited_at='old',status='completed'))
        with patch('agent_utils.agterm.workers.claude_inventory',return_value=None), \
             patch('agent_utils.agterm.sessions.live_rows',return_value=[]), \
             patch('agent_utils.cli.Agterm',return_value=self.ag):
            with self.assertRaisesRegex(ValueError,'Cannot exclude'):
                self.cli('ensure','--project','demo','--task','KEY-1','--prompt','follow-up')
        self.ag.launch.assert_not_called()

    def test_same_terminal_different_conversation_never_receives_input(self):
        save(self.record);self.probes(rows=[dict(self.row,foreground=['/bin/claude','--resume','someone-else'])])
        with patch('agent_utils.cli.Agterm',return_value=self.ag):
            with self.assertRaisesRegex(ValueError,'not Claude'):
                self.cli('ensure','--project','demo','--task','KEY-1','--prompt','follow-up')
        self.ag.call.assert_not_called()

    def test_native_blocked_indicator_prevents_input_even_if_provider_idle(self):
        save(self.record);self.probes(rows=[dict(self.row,status='blocked')])
        with patch('agent_utils.cli.Agterm',return_value=self.ag):
            with self.assertRaisesRegex(ValueError,'blocked'):
                self.cli('ensure','--project','demo','--task','KEY-1','--prompt','follow-up')
        self.ag.call.assert_not_called()

    def test_dry_ensure_observes_without_creating_or_changing_state(self):
        save(self.record);self.probes()
        before=records()
        with patch('agent_utils.cli.Agterm') as cls:
            cls.side_effect=[Mock(dry=True),self.ag]
            result=self.cli('--dry-run','ensure','--project','demo','--task','KEY-1','--prompt','follow-up')
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['action'],'reused')
        self.assertEqual(records(),before)
        self.ag.launch.assert_not_called();self.ag.call.assert_not_called()

    def test_exact_record_cannot_steal_another_workers_alias(self):
        save(self.record);save(dict(self.record,id='other',task='OTHER',conversation_id='other-conversation'))
        self.probes()
        with patch('agent_utils.cli.Agterm',return_value=self.ag):
            with self.assertRaisesRegex(ValueError,'already belongs'):
                self.cli('ensure','--project','demo','--record','worker','--alias','OTHER','--prompt','follow-up')
        self.ag.call.assert_not_called()
