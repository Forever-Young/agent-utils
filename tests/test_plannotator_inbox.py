from argparse import Namespace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent_utils.plannotator import inbox
from agent_utils.plannotator import state as _state_module

SERVED = _state_module.served  # the real one, kept before tests patch it


def line(seq, kind, ident, record):
    return json.dumps({'v': 1, 'seq': seq, 'at': '2030-01-01T00:00:00Z', 'kind': kind, 'id': ident, 'record': record}) + '\n'


def message(ident, thread, author, created, **extra):
    return dict({'id': ident, 'thread_id': thread, 'author': author, 'created_at': created,
                 'subject': extra.pop('subject', None), 'thread_name': None, 'resolved_at': None}, **extra)


AGENT = {'kind': 'agent', 'host': None, 'session': None, 'name': None}
KIRO = {'kind': 'agent', 'host': 'kiro', 'session': 'kiro-sess_1234', 'name': 'Kiro'}
PERSON = {'kind': 'person'}


def fixture(base, root):
    folder = Path(base)/'inbox'/'projects'/'demo-abc'
    folder.mkdir(parents=True)
    (folder/'project.json').write_text(line(1, 'project', 'prj_1', {'id': 'prj_1', 'key': 'demo-abc', 'name': 'demo', 'root': root}))
    rows = [
        # t1: answered, no session; the first copy is superseded by a later full copy.
        line(2, 'message', 't1', message('t1', 't1', AGENT, '2030-01-01T00:00:01Z', subject='Plan 1: old')),
        line(3, 'message', 't1', message('t1', 't1', AGENT, '2030-01-01T00:00:01Z', subject='Plan 1: first')),
        line(4, 'message', 'm2', message('m2', 't1', PERSON, '2030-01-01T00:00:02Z')),
        # t2: unanswered, no session.
        line(5, 'message', 't2', message('t2', 't2', AGENT, '2030-01-01T00:00:03Z', subject='Plan 2: second')),
        # t3: answered but attached to a Kiro session.
        line(6, 'message', 't3', message('t3', 't3', AGENT, '2030-01-01T00:00:04Z', subject='Plan 3: third')),
        line(7, 'message', 'm4', message('m4', 't3', KIRO, '2030-01-01T00:00:05Z')),
        line(8, 'message', 'm5', message('m5', 't3', PERSON, '2030-01-01T00:00:06Z')),
        # t4: resolved.
        line(9, 'message', 't4', message('t4', 't4', AGENT, '2030-01-01T00:00:07Z', subject='Plan 4', resolved_at='x')),
        'not json\n',
    ]
    (folder/'messages.jsonl').write_text(''.join(rows))


class InboxThreadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)/'data'
        self.root = Path(self.tmp.name)/'repo'/'sub'
        self.root.mkdir(parents=True)
        fixture(self.base, str(self.root))
        self.env = patch.dict(os.environ, {'XDG_STATE_HOME': self.tmp.name, 'XDG_CONFIG_HOME': self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def found(self):
        return {t['thread_id']: t for t in inbox.all_threads(self.base)}

    def test_threads_use_last_record_and_attached_sessions(self):
        found = self.found()
        self.assertEqual(found['t1']['subject'], 'Plan 1: first')
        self.assertEqual(found['t1']['last_author'], 'person')
        self.assertEqual(found['t1']['sessions'], [])
        self.assertEqual(found['t3']['sessions'], [{'host': 'kiro', 'session': 'kiro-sess_1234'}])
        self.assertTrue(found['t4']['resolved'])

    def test_select_answered_unattached_only(self):
        chosen, skipped = inbox.select(inbox.all_threads(self.base), answered=True)
        self.assertEqual([t['thread_id'] for t in chosen], ['t1'])
        reasons = {t['thread_id']: t['reason'] for t in skipped}
        self.assertIn('already attached', reasons['t3'])
        self.assertEqual(reasons['t2'], 'not answered')
        self.assertEqual(reasons['t4'], 'resolved')

    def test_select_explicit_ids_and_unknown(self):
        chosen, skipped = inbox.select(inbox.all_threads(self.base), ids=['t2', 't3', 'nope'])
        self.assertEqual([t['thread_id'] for t in chosen], ['t2'])
        self.assertEqual({t['thread_id'] for t in skipped}, {'t3', 'nope'})
        chosen, _ = inbox.select(inbox.all_threads(self.base), ids=['t3'], force=True)
        self.assertEqual([t['thread_id'] for t in chosen], ['t3'])

    def test_match_prefers_exact_single_root_project(self):
        parent = str(self.root.parent)
        pairs = [({'name': 'A'}, {'id': 'parent', 'roots': [parent]}),
                 ({'name': 'B'}, {'id': 'broad', 'roots': ['/elsewhere', str(self.root)]}),
                 ({'name': 'C'}, {'id': 'exact', 'roots': [str(self.root)]})]
        self.assertEqual(inbox.match_project(str(self.root), pairs)[1]['id'], 'exact')
        self.assertEqual(inbox.match_project(str(self.root), pairs[:2])[1]['id'], 'broad')
        self.assertEqual(inbox.match_project(str(self.root), pairs[:1])[1]['id'], 'parent')
        self.assertIsNone(inbox.match_project('/unrelated', pairs))

    def test_dispatch_launches_once_in_matching_workspace(self):
        window = {'name': 'Personal', 'default_agent': 'codex'}
        pairs = [(window, {'id': 'demo', 'name': 'Demo', 'roots': [str(self.root.parent)], 'default_agent': 'kiro-cli'})]
        launcher = Mock(return_value={'id': 'rec-1'})
        args = Namespace(thread=[], answered=True, all=False, force=False, agent=None, dry_run=False)
        with patch('builtins.print'):
            report = inbox.dispatch(args, Mock(), pairs, self.base, launcher)
        self.assertEqual([e['thread_id'] for e in report['launched']], ['t1'])
        _, win, project, agent, launch_args = launcher.call_args.args
        self.assertEqual((win['name'], agent), ('Personal', 'kiro-cli'))
        self.assertEqual(project['roots'], [str(self.root.resolve())])
        self.assertEqual(project['session_title'], 'Plan 1: first')
        self.assertEqual(launch_args.cwd, str(self.root.resolve()))
        self.assertIn('read_thread with thread_id t1', launch_args.prompt)
        self.assertIn('reply_to', launch_args.prompt)
        self.assertIn('agent_session "kiro-$KIRO_SESSION_ID"', launch_args.prompt)
        self.assertNotIn('agent_session', inbox.dispatch_prompt({'thread_id': 't', 'subject': 's'}, 'claude'))
        with patch('builtins.print'):
            again = inbox.dispatch(args, Mock(), pairs, self.base, launcher)
        self.assertEqual(launcher.call_count, 1)
        self.assertIn('already dispatched', again['skipped'][-1]['reason'])

    def test_dispatch_dry_run_and_missing_project(self):
        launcher = Mock()
        args = Namespace(thread=['t1', 't2'], answered=False, all=False, force=False, agent='claude', dry_run=True)
        with patch('builtins.print'):
            report = inbox.dispatch(args, Mock(), [({'name': 'W', 'default_agent': 'codex'}, {'id': 'x', 'name': 'X', 'roots': ['/unrelated']})], self.base, launcher)
        launcher.assert_not_called()
        self.assertEqual(report['planned'], [])
        self.assertTrue(all('no configured project' in s['reason'] for s in report['skipped']))
        with self.assertRaises(ValueError):
            inbox.dispatch(Namespace(thread=[], answered=False, all=False, force=False, agent=None, dry_run=True), Mock(), [], self.base, launcher)


class InboxLaunchTests(InboxThreadTests):
    def add(self, launch_id, agent, ref, **extra):
        file = self.base/'inbox'/'projects'/'demo-abc'/'messages.jsonl'
        record = message(launch_id, launch_id, AGENT, '2030-01-01T00:00:09Z', subject='Launched ' + launch_id,
                         launch=dict({'id': launch_id, 'agent': agent, 'folder': str(self.root), 'session_ref': ref}, **extra))
        with open(file, 'a') as stream:
            stream.write(line(20 + len(launch_id), 'message', launch_id, record))

    def test_attach_argv(self):
        self.assertEqual(inbox.attach_argv({'agent': 'claude-code', 'session_ref': 'ab12'})[0], ['claude', 'attach', 'ab12'])
        self.assertEqual(inbox.attach_argv({'agent': 'codex', 'session_ref': 'th1'})[0], ['codex', 'resume', 'th1'])
        self.assertIsNone(inbox.attach_argv({'agent': 'pi', 'session_ref': 'x'})[0])
        self.assertEqual(inbox.attach_argv({'agent': 'codex'}), (None, 'no session yet'))

    def test_list_and_adopt(self):
        self.add('l1', 'claude-code', 'ab12'); self.add('l2', 'pi', 'p1'); self.add('l3', 'codex', 'c1', deleted_at='x')
        pairs = [({'name': 'Personal', 'default_agent': 'codex'}, {'id': 'demo', 'name': 'Demo', 'roots': [str(self.root)]})]
        with patch('builtins.print'):
            rows = {r['id']: r for r in inbox.list_launches(Namespace(), pairs, self.base)}
        self.assertEqual({k: r['adoptable'] for k, r in rows.items()}, {'l1': True, 'l2': False, 'l3': False})
        ag = Mock(dry=False); ag.launch.return_value = 'term-1'
        with patch('builtins.print'):
            report = inbox.adopt(Namespace(launch=['l1', 'l2', 'zz']), ag, pairs, self.base)
        window, project, argv, cwd = ag.launch.call_args.args
        self.assertEqual((window['name'], project['session_title'], argv, cwd),
                         ('Personal', 'Launched l1', ['claude', 'attach', 'ab12'], str(self.root.resolve())))
        self.assertEqual([r['id'] for r in report['adopted']], ['l1'])
        self.assertEqual({r['id'] for r in report['skipped']}, {'l2', 'zz'})
        with self.assertRaises(ValueError):
            inbox.adopt(Namespace(launch=[]), ag, pairs, self.base)


class InboxFilterTests(InboxThreadTests):
    def test_window_filter_applies_after_matching(self):
        work = ({'name': 'Work', 'default_agent': 'codex'}, {'id': 'exact', 'name': 'E', 'roots': [str(self.root)]})
        home = ({'name': 'Personal', 'default_agent': 'codex'}, {'id': 'parent', 'name': 'P', 'roots': [str(self.root.parent)]})
        launcher = Mock(return_value={'id': 'r'})
        args = Namespace(thread=['t1'], answered=False, all=False, force=False, agent='kiro-cli', dry_run=False, window='Personal', project=None)
        with patch('builtins.print'):
            report = inbox.dispatch(args, Mock(), [work, home], self.base, launcher)
            launcher.assert_not_called()
            self.assertIn('belongs to Work', report['skipped'][-1]['reason'])
            rows = inbox.list_threads(Namespace(thread=[], answered=True, all=False, window=None, project=None), [work, home], self.base)
        self.assertEqual([(r['thread_id'], r['window'], r['workspace']) for r in rows], [('t1', 'Work', 'exact')])


class InboxDoctorTests(unittest.TestCase):
    def test_stopped_inbox_is_unhealthy(self):
        with patch.object(inbox, '_status', return_value={'running': False, 'state': 'stopped'}):
            report = inbox.check(base='/nonexistent')
        self.assertFalse(report['healthy'])
        self.assertIn('not running', report['problems'][0])

    def test_dead_pid_duplicate_servers_and_unreachable_page(self):
        status = {'running': True, 'pid': 999999, 'url': 'http://127.0.0.1:9/', 'version': '0.29.0', 'cli_version': '0.30.0'}
        with patch.object(inbox, '_status', return_value=status), patch.object(inbox, '_alive', return_value=False), \
             patch.object(inbox, '_servers', return_value=[1, 2]), \
             patch('urllib.request.urlopen', side_effect=OSError('refused')):
            report = inbox.check(base='/nonexistent')
        text = ' '.join(report['problems'])
        for expected in ('not running', 'differs', 'Several Inbox servers', 'did not answer'):
            self.assertIn(expected, text)

    def test_servers_ignores_mcp_and_cli_calls(self):
        ps = '\n'.join(['10 /x/plannotator inbox --background', '11 /x/plannotator inbox mcp',
                        '12 plannotator inbox --no-open', '13 /x/plannotator inbox status', '14 /x/other inbox'])
        with patch('subprocess.run', return_value=Mock(stdout=ps)):
            self.assertEqual(inbox._servers(), [10, 12])

    def test_fix_needs_yes(self):
        with patch.object(inbox, 'check', return_value={'healthy': False, 'problems': ['x']}), \
             patch('subprocess.run') as run, patch('builtins.print'):
            self.assertEqual(inbox.doctor(Namespace(fix=True, yes=False, dry_run=False)), 1)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class ThreadTitleTests(unittest.TestCase):
    def setUp(self):
        from agent_utils.plannotator import state
        self.state = state
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)/'data'
        fixture(self.base, str(Path(self.tmp.name)/'repo'))
        self.file = self.base/'inbox'/'projects'/'demo-abc'/'messages.jsonl'
        self.backups = Path(self.tmp.name)/'bak'
        served = patch.object(state, 'served', return_value=(True, ''))
        served.start();self.addCleanup(served.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def title(self, thread='t1'):
        return inbox._records(self.file)[thread]['thread_name']

    def test_preview_writes_nothing(self):
        before = self.file.read_text()
        report = self.state.rename('t1', '  New   title ', base=self.base)
        self.assertEqual((report['after'], report['applied'], report['message_ids']), ('New title', False, ['t1']))
        self.assertEqual(self.file.read_text(), before)

    def test_rejects_empty_long_unknown_and_taken_names(self):
        with self.assertRaises(SystemExit):
            self.state.rename('t1', '   ', base=self.base)
        with self.assertRaises(SystemExit):
            self.state.rename('t1', 'x'*81, base=self.base)
        with self.assertRaises(SystemExit):
            self.state.rename('nope', 'a', base=self.base)
        with open(self.file, 'a') as stream:
            stream.write(line(20, 'message', 't2', message('t2', 't2', AGENT, 'x', subject='s', thread_name='Taken')))
        with self.assertRaises(SystemExit):
            self.state.rename('t1', 'taken', base=self.base)

    def test_apply_rewrites_every_copy_backs_up_and_keeps_other_lines(self):
        with patch.object(inbox, '_status', side_effect=[{'running': True, 'version': '0.29.0'}, {'running': False}]), \
             patch.object(inbox, 'check', return_value={'healthy': True, 'problems': []}), \
             patch.object(self.state, '_inbox') as run, patch.object(self.state.time, 'sleep'):
            report = self.state.rename('t1', 'Ünï title', apply=True, base=self.base, backup_dir=self.backups)
        self.assertEqual([c.args for c in run.call_args_list], [('stop',), ('--background',)])
        self.assertTrue(report['applied'] and report['doctor']['healthy'])
        self.assertEqual(self.title(), 'Ünï title')
        text = self.file.read_text()
        self.assertEqual(text.count('Ünï title'), 2)  # both copies of t1
        self.assertIn('not json\n', text)
        self.assertEqual(self.title('t3'), None)
        self.assertIn('Plan 3: third', text)
        self.assertEqual(Path(report['backups'][0]).read_text().count('Ünï'), 0)
        for raw in text.splitlines():
            if raw != 'not json':
                json.loads(raw)

    def test_clear_and_noop(self):
        with patch.object(inbox, '_status', return_value={'running': False}), patch.object(self.state, '_inbox') as run:
            self.state.rename('t1', 'Named', apply=True, base=self.base, backup_dir=self.backups)
            self.assertEqual(self.title(), 'Named')
            self.assertEqual(run.call_count, 0)  # a stopped Inbox is left stopped
            self.state.rename('t1', None, apply=True, base=self.base, backup_dir=self.backups)
        self.assertIsNone(self.title())
        self.assertFalse(self.state.rename('t1', None, apply=True, base=self.base)['applied'])

    def test_unchecked_version_proceeds_with_backup_and_note(self):
        with patch.object(inbox, '_status', side_effect=[{'running': True, 'version': '9.9.9'}, {'running': False}]), \
             patch.object(inbox, 'check', return_value={'healthy': True, 'problems': []}), \
             patch.object(self.state, '_inbox'), patch.object(self.state.time, 'sleep'):
            report = self.state.rename('t1', 'New', apply=True, base=self.base, backup_dir=self.backups)
        self.assertEqual(self.title(), 'New')
        self.assertIn('9.9.9 was not checked', report['version_note'])
        self.assertTrue(report['served'] and report['backups'])

    def test_refuses_when_server_does_not_stop(self):
        before = self.file.read_text()
        with patch.object(inbox, '_status', return_value={'running': True, 'version': '0.29.0'}), \
             patch.object(self.state, '_inbox'), \
             patch.object(self.state.time, 'sleep'):
            with self.assertRaises(SystemExit):
                self.state.rename('t1', 'x', apply=True, base=self.base, backup_dir=self.backups)
        self.assertEqual(self.file.read_text(), before)

    def test_failure_after_restart_restores_and_warns(self):
        before = self.file.read_text()
        with patch.object(inbox, '_status', side_effect=[{'running': True, 'version': '9.9.9'}, {'running': False}]), \
             patch.object(inbox, 'check', side_effect=[{'healthy': False, 'problems': ['Inbox page did not answer']},
                                                       {'healthy': True, 'problems': []}]), \
             patch.object(self.state, '_inbox') as run, patch.object(self.state.time, 'sleep'):
            with self.assertRaises(SystemExit) as caught:
                self.state.rename('t1', 'Broken', apply=True, base=self.base, backup_dir=self.backups)
        self.assertTrue(str(caught.exception).startswith('\u26a0\ufe0f The title edit failed after the restart'))
        self.assertIn('restarted (healthy)', str(caught.exception))
        self.assertEqual(self.file.read_text(), before)
        self.assertEqual([c.args for c in run.call_args_list], [('stop',), ('--background',), ('stop',), ('--background',)])

    def test_served_checks_the_change_through_the_api(self):
        report = {'recipe': 'title', 'thread_id': 't1', 'root': '/nowhere', 'after': 'New'}
        answer = lambda name: Mock(returncode=0, stdout=json.dumps({'thread': {'thread_id': 't1', 'thread_name': name}}), stderr='')
        with patch.object(self.state, '_call_read', return_value=answer('New')):
            self.assertEqual(SERVED(report), (True, ''))
        with patch.object(self.state, '_call_read', return_value=answer('Old')):
            self.assertFalse(SERVED(report)[0])
        with patch.object(self.state, '_call_read', return_value=Mock(returncode=1, stdout='', stderr='thread_not_found')):
            self.assertIn('thread_not_found', SERVED(report)[1])
        moved = {'recipe': 'move', 'thread_id': 't1', 'root': '/a', 'destination_root': '/b'}
        with patch.object(self.state, '_call_read', return_value=Mock(returncode=0, stderr='',
                          stdout=json.dumps({'thread': {'thread_id': 't1', 'project': {'root': '/b'}}}))) as call:
            self.assertTrue(SERVED(moved)[0])
        self.assertEqual(call.call_args.args[1], '/b')


class StateRecipeTests(unittest.TestCase):
    """subject, reopen, author, withdraw and move on synthetic Inbox files."""

    def setUp(self):
        from agent_utils.plannotator import state
        self.state = state
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)/'data'
        fixture(self.base, str(Path(self.tmp.name)/'repo'))
        self.folder = self.base/'inbox'/'projects'/'demo-abc'
        self.messages = self.folder/'messages.jsonl'
        self.questions = self.folder/'questions.jsonl'
        self.questions.write_text(
            line(30, 'question', 'm4/q-1', {'id': 'm4/q-1', 'message_id': 'm4', 'key': 'q-1', 'answer': None, 'sent_reply_id': None}) +
            line(31, 'question', 'm4/q-2', {'id': 'm4/q-2', 'message_id': 'm4', 'key': 'q-2', 'answer': 'x', 'sent_reply_id': None}))
        other = self.base/'inbox'/'projects'/'other-def'
        other.mkdir()
        (other/'project.json').write_text(line(40, 'project', 'prj_2', {'id': 'prj_2', 'key': 'other-def', 'name': 'other', 'root': str(Path(self.tmp.name)/'o')}))
        self.other = other
        self.backups = Path(self.tmp.name)/'bak'
        served = patch.object(state, 'served', return_value=(True, ''))
        served.start();self.addCleanup(served.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, recipe, thread, **opts):
        with patch.object(inbox, '_status', side_effect=[{'running': True, 'version': '0.29.0'}, {'running': False}]), \
             patch.object(inbox, 'check', return_value={'healthy': True, 'problems': []}), \
             patch.object(self.state, '_inbox'), patch.object(self.state.time, 'sleep'):
            return self.state.edit(recipe, thread, apply=True, base=self.base, backup_dir=self.backups, **opts)

    def test_subject_changes_every_copy_of_the_root_only(self):
        report = self.apply('subject', 't1', subject=' Plan 1:  renamed ')
        self.assertTrue(report['applied'])
        self.assertEqual(inbox._records(self.messages)['t1']['subject'], 'Plan 1: renamed')
        self.assertNotIn('Plan 1: first', self.messages.read_text())
        self.assertEqual(inbox._records(self.messages)['t2']['subject'], 'Plan 2: second')
        with self.assertRaises(SystemExit):
            self.state.edit('subject', 't1', base=self.base, subject='  ')

    def test_reopen_clears_resolved_only_for_resolved_threads(self):
        self.assertFalse(self.state.edit('reopen', 't1', base=self.base)['changed'])
        self.apply('reopen', 't4')
        self.assertIsNone(inbox._records(self.messages)['t4']['resolved_at'])

    def test_author_only_for_agent_messages(self):
        self.apply('author', 't3', message='m4', name='Kiro 2', host='kiro')
        self.assertEqual(inbox._records(self.messages)['m4']['author'], dict(KIRO, name='Kiro 2'))
        with self.assertRaises(SystemExit):
            self.state.edit('author', 't1', base=self.base, message='m2', name='x')
        with self.assertRaises(SystemExit):
            self.state.edit('author', 't1', base=self.base, message='m4', name='x')  # other thread

    def test_withdraw_marks_open_question_and_refuses_answered(self):
        self.apply('withdraw', 't3', question='m4/q-1')
        self.assertEqual(inbox._records(self.questions)['m4/q-1']['superseded_by'], {'message_id': 'm4', 'key': None})
        self.assertFalse(self.state.edit('withdraw', 't3', base=self.base, question='m4/q-1')['changed'])
        with self.assertRaises(SystemExit):
            self.state.edit('withdraw', 't3', base=self.base, question='m4/q-2')

    def test_move_carries_messages_and_questions_to_the_other_project(self):
        report = self.apply('move', 't3', destination='other')
        self.assertEqual(report['moved'], {'messages': 3, 'questions': 2})
        project = {p['name']: p for p in inbox.projects(self.base)}
        self.assertEqual([t['thread_id'] for t in inbox.threads(project['other'])], ['t3'])
        self.assertNotIn('t3', [t['thread_id'] for t in inbox.threads(project['demo'])])
        moved = inbox._records(self.other/'messages.jsonl')['m4']
        self.assertEqual(moved['project_id'], 'prj_2')
        self.assertEqual(inbox._records(self.other/'questions.jsonl')['m4/q-1']['project_id'], 'prj_2')
        self.assertNotIn('"m4"', self.messages.read_text())
        with self.assertRaises(SystemExit):
            self.state.edit('move', 't1', base=self.base, destination='nowhere')
        with self.assertRaises(SystemExit):
            self.state.edit('move', 't1', base=self.base, destination='demo')

    def test_failed_readback_restores_originals(self):
        before = self.messages.read_text()
        with patch.object(inbox, '_status', side_effect=[{'running': True, 'version': '0.29.0'}, {'running': False}]), \
             patch.object(self.state, '_inbox'), patch.object(self.state.time, 'sleep'), \
             patch.object(self.state.json, 'dumps', side_effect=lambda item, **kw: '{"id": "x"}'):
            with self.assertRaises(SystemExit):
                self.state.edit('subject', 't1', apply=True, base=self.base, backup_dir=self.backups, subject='New')
        self.assertEqual(self.messages.read_text(), before)
