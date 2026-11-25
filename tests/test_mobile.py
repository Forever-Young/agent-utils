import io
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent_utils.agterm import mobile
from agent_utils.entry import main as entry_main


ZMX = {'remote': {'endpoint': {'executable': '/x/zmx', 'socketDirectory': '/x'}, 'sessions': [
    {'id': 'row-1', 'name': 'Agent', 'companions': [], 'panes': [
        {'pane': 'left', 'daemon': 'agterm-abc', 'foreground': ['/usr/bin/python', 'runner.py', '--', '/opt/bin/claude']}]}]}}
WINDOWS = {'windows': [{'id': 'W1', 'name': 'Demo', 'open': True, 'active': True}, {'id': 'W0', 'name': 'old', 'open': False}]}
TREE = {'tree': {'workspaces': [{'id': 'WS', 'name': 'demo-project', 'sessions': [
    {'id': 'row-1', 'name': 'Agent', 'status': 'blocked', 'unseen': 2, 'surfaces': [{'kind': 'left', 'lead': 'leader'}]},
    {'id': 'row-2', 'name': 'Shell', 'status': 'idle', 'surfaces': []}]}]}}


def fake_call(*args, json_result=False):
    if args[:2] == ('zmx', 'tree'):
        return ZMX
    if args[:2] == ('window', 'list'):
        return WINDOWS
    if args[0] == 'tree':
        assert args[2] == 'W1', 'closed windows are skipped'
        return TREE
    if args[:2] == ('zmx', 'screen'):
        return {'text': 'screen of ' + args[2]}
    if args[0] == 'version':
        return {'app': {'version': '9.9.9'}}
    return 'ok'


class MobileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'XDG_STATE_HOME': self.tmp.name, 'XDG_CONFIG_HOME': self.tmp.name})
        self.env.start()
        self.ag = Mock(dry=False)
        self.ag.call.side_effect = fake_call
        self.config = os.path.join(self.tmp.name, 'config.json')
        with open(self.config, 'w') as f:
            json.dump({'version': 1, 'windows': [{'name': 'Demo', 'default_agent': 'claude', 'projects': [
                {'id': 'demo', 'name': 'demo-project', 'roots': [self.tmp.name]},
                {'id': 'multi', 'name': 'multi', 'roots': [self.tmp.name, '/tmp']}]}]}, f)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def serve(self, *requests):
        out = io.StringIO()
        server = mobile.Server(self.ag, self.config, out=out)
        server.run([r if isinstance(r, str) else json.dumps(r) for r in requests])
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_tree_joins_attachability_lead_and_agent(self):
        from agent_utils.agterm.sessions import save
        save({'id': 'rec', 'terminal_id': 'row-1', 'project': 'demo', 'agent': 'claude', 'task': 'T-1',
              'conversation_id': 'conv', 'title': 'Agent'})
        tree = self.serve({'id': 1, 'method': 'tree'})[0]['result']
        self.assertEqual([w['id'] for w in tree['windows']], ['W1'])
        first, second = tree['windows'][0]['workspaces'][0]['sessions']
        self.assertTrue(first['attachable'])
        self.assertEqual(first['panes'], [{'pane': 'left', 'lead': 'leader', 'foreground': ZMX['remote']['sessions'][0]['panes'][0]['foreground']}])
        self.assertEqual(first['record']['task'], 'T-1')
        self.assertEqual((first['status'], first['unseen'], first['agent']), ('blocked', 2, 'claude'))
        self.assertNotIn('daemon', json.dumps(tree))
        self.assertFalse(second['attachable'])
        self.assertIsNone(second['record'])

    def test_errors_have_codes_and_never_break_the_stream(self):
        replies = self.serve('not json', '[1]', {'id': 2, 'method': 'nope'}, {'id': 3, 'method': 'tree', 'params': []},
                             {'id': 4, 'method': 'rename', 'params': {'session': 'gone', 'name': 'x'}})
        self.assertEqual([r.get('code') for r in replies], ['bad_request', 'bad_request', 'unknown_method', 'bad_request', 'gone'])

    def test_progress_output_stays_off_the_protocol_stream(self):
        def noisy(server, params):
            print('Managed session: noise')
            return {'done': True}
        with patch.dict(mobile.METHODS, {'noisy': noisy}):
            replies = self.serve({'id': 1, 'method': 'noisy'})
        self.assertEqual(replies, [{'id': 1, 'ok': True, 'result': {'done': True}}])

    def test_rename_targets_the_fresh_row_and_window(self):
        reply = self.serve({'id': 1, 'method': 'rename', 'params': {'session': 'row-2', 'name': 'New name'}})[0]
        self.assertTrue(reply['ok'])
        self.assertIn(('session', 'rename', 'New name', '--target', 'row-2', '--window', 'W1'),
                      [c.args for c in self.ag.call.call_args_list])

    def test_type_uses_stdin_so_dashes_are_text(self):
        with patch('subprocess.run', return_value=Mock(returncode=0)) as run:
            reply = self.serve({'id': 1, 'method': 'type', 'params': {'session': 'row-1', 'text': '--help', 'enter': True}})[0]
        self.assertTrue(reply['ok'], reply)
        argv = run.call_args.args[0]
        self.assertEqual(argv[1:9], ['session', 'type', '--stdin', '--target', 'row-1', '--window', 'W1', '--pane'])
        self.assertEqual(run.call_args.kwargs['input'], '--help\r')

    def test_type_rejects_bad_text(self):
        replies = self.serve({'id': 1, 'method': 'type', 'params': {'session': 'row-1', 'text': 'a\x00b'}},
                             {'id': 2, 'method': 'type', 'params': {'session': 'row-1', 'text': 'x', 'pane': 'top'}})
        self.assertEqual([r['code'] for r in replies], ['bad_request', 'bad_request'])

    def test_screen_resolves_daemon_server_side(self):
        reply = self.serve({'id': 1, 'method': 'screen', 'params': {'session': 'row-1', 'lines': 5}})[0]
        self.assertEqual(reply['result']['text'], 'screen of agterm-abc')
        missing = self.serve({'id': 2, 'method': 'screen', 'params': {'session': 'row-2'}})[0]
        self.assertEqual(missing['code'], 'gone')

    def test_projects_list_agents_in_menu_order(self):
        with patch('agent_utils.agterm.startup.visible_agents', return_value=['codex', 'claude']):
            result = self.serve({'id': 1, 'method': 'projects'})[0]['result']
        self.assertEqual(result[0]['id'], 'demo')
        self.assertEqual([a['id'] for a in result[0]['agents']], ['claude', 'codex'])

    def test_launch_goes_through_managed_launch(self):
        with patch('agent_utils.agterm.sessions.launch', return_value={'id': 'rec', 'terminal_id': 'row-9', 'conversation_id': 'c'}) as launch:
            reply = self.serve({'id': 1, 'method': 'launch', 'params': {'project': 'demo', 'agent': 'claude', 'name': 'From phone'}})[0]
        self.assertEqual(reply['result'], {'record': 'rec', 'session': 'row-9', 'conversation_id': 'c'})
        _, window, project, agent, args = launch.call_args.args
        self.assertEqual((window['name'], project['session_title'], agent, args.command), ('Demo', 'From phone', 'claude', 'new'))

    def test_launch_refuses_what_would_need_a_terminal_picker(self):
        with patch('agent_utils.agterm.sessions.launch') as launch:
            replies = self.serve({'id': 1, 'method': 'launch', 'params': {'project': 'demo', 'agent': 'opencode', 'mode': 'resume'}},
                                 {'id': 2, 'method': 'launch', 'params': {'project': 'multi', 'agent': 'kiro-cli'}},
                                 {'id': 3, 'method': 'launch', 'params': {'project': 'nope'}},
                                 {'id': 4, 'method': 'launch', 'params': {'project': 'demo', 'mode': 'task'}})
        self.assertEqual([r['code'] for r in replies], ['bad_request', 'needs_root', 'bad_request', 'bad_request'])
        launch.assert_not_called()

    def test_subscribe_pushes_tree_once_until_it_changes(self):
        out = io.StringIO()
        server = mobile.Server(self.ag, self.config, out=out, interval=0.5)
        server.send(server.handle(json.dumps({'id': 1, 'method': 'subscribe', 'params': {'interval': 0.5}})))
        import time
        time.sleep(1.3)
        server.stop.set()
        server.poller.join(2)
        events = [json.loads(l) for l in out.getvalue().splitlines() if '"event"' in l]
        self.assertEqual([e['event'] for e in events], ['tree.changed'])

    def test_attach_needs_pty_and_existing_pane(self):
        with patch('os.isatty', return_value=False):
            with self.assertRaisesRegex(ValueError, 'PTY'):
                mobile.attach(self.ag, 'row-1', 'left')
        with patch('os.isatty', return_value=True):
            with self.assertRaisesRegex(ValueError, 'that pane'):
                mobile.attach(self.ag, 'row-1', 'right')
            with patch('agent_utils.agterm.mobile.attach_pane', return_value=0) as attach:
                self.assertEqual(mobile.attach(self.ag, 'row-1', 'left'), 0)
            self.assertEqual(attach.call_args.args[2:4], ('row-1', ZMX['remote']['sessions'][0]['panes'][0]))
            self.assertFalse(attach.call_args.kwargs['notice'])

    def test_entry_routes_mobile_group(self):
        with patch('agent_utils.agterm.mobile.command', return_value=0) as command:
            self.assertEqual(entry_main(['mobile', 'serve', '--interval', '5']), 0)
        command.assert_called_once_with(['serve', '--interval', '5'])


if __name__ == '__main__':
    unittest.main()
