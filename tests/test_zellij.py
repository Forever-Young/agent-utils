import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from agent_utils.core import zellij
from agent_utils.core.setup import zellij_status as setup_status

# A fake `zellij`: keeps tabs in a JSON file next to it and logs every call. `FAKE_CLIENT=0`
# mimics a session with no attached client (new-tab prints an ID but creates nothing).
FAKE = r'''#!/usr/bin/env python3
import json, os, re, sys
here = os.path.dirname(os.path.abspath(__file__))
log = os.path.join(here, 'log.txt')
args = sys.argv[1:]
if args == ['--version']:
    print('zellij 0.46.0'); sys.exit(0)
session = None
if args[:1] == ['--session']:
    session, args = args[1], args[2:]
def path(name): return os.path.join(here, 'state.json' if name in (None, 'main') else 'state-%s.json' % name)
def load(name):
    p = path(name)
    return json.load(open(p)) if os.path.exists(p) else {'next': 1, 'tabs': [{'tab_id': 0, 'name': 'Tab #1', 'cmd': None}]}
open(log, 'a').write(json.dumps(([] if session in (None, 'main') else ['@' + session]) + (args[1:] if args[:1] == ['action'] else args)) + '\n')
if args[:1] == ['list-sessions']:
    names = ['main'] + sorted(f[6:-5] for f in os.listdir(here) if f.startswith('state-'))
    for n in names: print('%s [Created 1s ago] %s' % (n, '(EXITED - attach to resurrect)' if os.path.exists(os.path.join(here, 'exited-' + n)) else ''))
    sys.exit(0)
def from_kdl(kdl):
    name = re.search(r'tab name="((?:[^"\\]|\\.)*)"', kdl).group(1)
    m = re.search(r'pane name="agent"[^\n]*command="([^"]*)"[^\n]*\{\s*args ([^\n]*)', kdl)
    cmd = [m.group(1)] + re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2)) if m else None
    return {'name': name, 'cmd': cmd, 'title': 'agent', 'kdl': kdl}
if args[:1] == ['--layout-string'] and args[2:4] == ['attach', '--create-background']:
    name = args[4]
    if os.path.exists(path(name)): sys.exit(0)
    json.dump({'next': 1, 'tabs': [dict(tab_id=0, **from_kdl(args[1]))]}, open(path(name), 'w')); sys.exit(0)
data = load(session)
assert args[0] == 'action', args
cmd, rest = args[1], args[2:]
def save(): json.dump(data, open(path(session), 'w'))
if cmd == '--help':
    if os.environ.get('FAKE_OLD'): print('Commands:\n  new-tab  x')
    else:
        for c in ['new-tab','list-tabs','list-panes','go-to-tab-by-id','close-tab-by-id','rename-tab-by-id','set-resurrect-command']:
            print('  %-30s text' % c)
elif cmd == 'switch-session':
    if not os.environ.get('FAKE_NO_SWITCH'): open(os.path.join(here, 'client-' + rest[0]), 'w').write(' '.join(rest))
elif cmd == 'list-clients':
    print('CLIENT_ID  ZELLIJ_PANE_ID RUNNING_COMMAND')
    if os.path.exists(os.path.join(here, 'client-' + (session or 'main'))): print('1  terminal_0  N/A')
elif cmd == 'dump-layout':
    print('layout {\n    new_tab_template {\n        pane size=1 borderless=true {\n            plugin location="zellij:compact-bar"\n        }\n        pane\n    }\n}')
elif cmd == 'new-tab':
    tid = data['next']; data['next'] += 1
    if '--layout-string' in rest: tab = from_kdl(rest[rest.index('--layout-string') + 1])
    else: tab = {'name': rest[rest.index('--name') + 1], 'cmd': rest[rest.index('--') + 1:]}
    if os.environ.get('FAKE_CLIENT') != '0':
        data['tabs'].append(dict(tab_id=tid, **tab))
    save(); print(tid)
elif cmd == 'list-tabs':
    print(json.dumps([{'tab_id': t['tab_id'], 'name': t['name'], 'position': i, 'active': False,
                       'attention': {'status': t.get('att', 'Idle')}} for i, t in enumerate(data['tabs'])]))
elif cmd == 'list-panes':
    print(json.dumps([{'id': 10 + t['tab_id'], 'is_plugin': False, 'tab_id': t['tab_id'], 'tab_name': t['name'],
                       'title': t.get('title', 'zsh'), 'pane_cwd': t.get('cwd'),
                       'terminal_command': t.get('fg') or (' '.join(t['cmd']) if t.get('cmd') else None)} for t in data['tabs']]))
elif cmd == 'rename-tab-by-id':
    for t in data['tabs']:
        if str(t['tab_id']) == rest[0]: t['name'] = rest[1]
    save()
elif cmd == 'close-tab-by-id':
    data['tabs'] = [t for t in data['tabs'] if str(t['tab_id']) != rest[0]]; save()
'''


class FakeZellijCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.bin = d / 'zellij'
        self.bin.write_text(FAKE)
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IEXEC)
        self.env = {'AGTERM_UTILS_ZELLIJ': str(self.bin), 'ZELLIJ_SESSION_NAME': 'main', 'XDG_STATE_HOME': str(d / 's'),
                    'XDG_CONFIG_HOME': str(d / 'c')}
        patcher = patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def log(self):
        path = Path(self.tmp.name) / 'log.txt'
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


class StatusTests(unittest.TestCase):
    def test_osc_status(self):
        self.assertEqual(zellij.osc_status('working'), '\x1b]7501;state=working\x1b\\')
        text = zellij.osc_status('blocked', 'permission', 'Run rm?', 'kiro')
        self.assertIn('state=blocked:kind=permission:app=kiro:msg=UnVuIHJtPw==', text)
        for bad in (('nope',), ('blocked', 'other')):
            with self.assertRaises(ValueError):zellij.osc_status(*bad)

    def test_emit_writes_to_tty_only_inside_zellij(self):
        with tempfile.TemporaryDirectory() as d:
            tty = str(Path(d) / 'tty')
            self.assertFalse(zellij.emit_status('done', env={}, tty=tty))
            self.assertFalse(Path(tty).exists())
            self.assertTrue(zellij.emit_status('done', env={'ZELLIJ': '0', 'ZELLIJ_PANE_ID': '3'}, tty=tty))
            self.assertEqual(Path(tty).read_text(), '\x1b]7501;state=done\x1b\\')
            self.assertFalse(zellij.emit_status('done', env={'ZELLIJ': '0', 'ZELLIJ_PANE_ID': '3'}, tty=str(Path(d) / 'no/such')))


class CliTests(FakeZellijCase):
    def project_config(self):
        root = Path(self.tmp.name) / 'repo'
        root.mkdir()
        config = Path(self.tmp.name) / 'config.json'
        config.write_text(json.dumps({'version': 1, 'windows': [{'name': 'Work', 'default_agent': 'claude', 'projects': [
            {'id': 'demo', 'name': 'Demo', 'roots': [str(root)]}]}]}))
        return str(config), root

    def run_cmd(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = zellij.command(list(argv))
        return code, out.getvalue()

    def test_open_creates_tab_resurrect_and_record(self):
        config, root = self.project_config()
        with patch('shutil.which', return_value='/bin/claude'):
            code, out = self.run_cmd('open', '--config', config, '--project', 'demo', '--agent', 'claude')
        self.assertEqual(code, 0)
        self.assertIn('Opened tab 1 "Demo: claude"', out)
        calls = self.log()
        new = next(c for c in calls if c[0] == 'new-tab')
        layout = new[new.index('--layout-string') + 1]
        self.assertIn('cwd="%s"' % root.resolve(), layout)
        self.assertNotIn('--no-focus', new)
        runner_cmd = json.loads((self.bin.parent/'state.json').read_text())['tabs'][-1]['cmd']
        self.assertEqual(runner_cmd[0], 'env')
        self.assertIn('AGTERM_UTILS_RECORD=', runner_cmd[1])
        res = next(c for c in calls if c[0] == 'set-resurrect-command')
        self.assertEqual(res[res.index('--') + 1:], runner_cmd)
        self.assertEqual(res[res.index('-p') + 1], '11')
        from agent_utils.agterm.sessions import records
        record = next(iter(records().values()))
        self.assertEqual(record['zellij'], {'session': 'main', 'tab_id': 1, 'pane_id': 11, 'resumable': True})
        # list joins the record by tab id
        code, out = self.run_cmd('list', '--json')
        rows = json.loads(out)
        self.assertEqual(next(r for r in rows if r['tab_id'] == 1)['record'], record['id'])

    def test_join_survives_renumbered_tab_ids(self):
        config, _ = self.project_config()
        with patch('shutil.which', return_value='/bin/claude'):
            self.run_cmd('open', '--config', config, '--project', 'demo')
        state = Path(self.tmp.name) / 'state.json'
        data = json.loads(state.read_text())
        mine = next(t for t in data['tabs'] if t['tab_id'] == 1)
        mine['tab_id'] = 7
        data['tabs'].append({'tab_id': 1, 'name': 'Hand made', 'cmd': None})
        state.write_text(json.dumps(data))
        from agent_utils.agterm.runner import update
        from agent_utils.agterm.sessions import records
        update(next(iter(records())), status='running', provider_pid=2 ** 22 + 12345)
        rows = {r['tab_id']: r for r in json.loads(self.run_cmd('list', '--json')[1])}
        self.assertIsNotNone(rows[7]['record'])
        self.assertEqual(rows[7]['status'], 'suspended')
        self.assertIsNone(rows[1]['record'])
        self.run_cmd('focus', str(rows[7]['record'])[:8])
        self.assertIn(['go-to-tab-by-id', '7'], self.log())

    def test_open_fails_without_client(self):
        config, _ = self.project_config()
        with patch.dict(os.environ, {'FAKE_CLIENT': '0'}), patch('shutil.which', return_value='/bin/claude'):
            with self.assertRaisesRegex(zellij.ZellijError, 'no attached client'):
                self.run_cmd('open', '--config', config, '--project', 'demo')
        from agent_utils.agterm.sessions import records
        self.assertEqual(records(), {})

    def test_old_zellij_is_rejected_before_opening(self):
        config, _ = self.project_config()
        with patch.dict(os.environ, {'FAKE_OLD': '1'}), patch('shutil.which', return_value='/bin/claude'):
            with self.assertRaisesRegex(zellij.ZellijError, 'lacks: list-tabs'):
                self.run_cmd('open', '--config', config, '--project', 'demo')
        self.assertFalse([c for c in self.log() if c[0] == 'new-tab'])

    def test_task_reuses_live_tab_and_close_guards_running_provider(self):
        config, _ = self.project_config()
        with patch('shutil.which', return_value='/bin/claude'):
            self.run_cmd('open', '--config', config, '--project', 'demo', '--task', 'T-1')
            code, out = self.run_cmd('open', '--config', config, '--project', 'demo', '--task', 'T-1')
        self.assertIn('already has a tab', out)
        self.assertEqual(len([c for c in self.log() if c[0] == 'new-tab']), 1)
        self.assertEqual([c for c in self.log() if c[0] == 'go-to-tab-by-id'], [['go-to-tab-by-id', '1']])
        from agent_utils.agterm.sessions import records
        from agent_utils.agterm.runner import update
        update(next(iter(records())), provider_pid=os.getpid())
        with self.assertRaisesRegex(ValueError, 'still running'):
            self.run_cmd('close', 'T-1')
        self.run_cmd('close', 'T-1', '--force')
        self.assertIn(['close-tab-by-id', '1'], self.log())
        self.assertEqual(self.run_cmd('list')[0], 0)

    def test_dry_run_prints_without_changing(self):
        config, _ = self.project_config()
        with patch('shutil.which', return_value='/bin/claude'):
            code, out = self.run_cmd('open', '--config', config, '--project', 'demo', '--dry-run')
        self.assertIn(' new-tab ', out)
        self.assertFalse([c for c in self.log() if c[0] == 'new-tab'])

    def test_doctor_reports_missing_commands(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(zellij.doctor(zellij.Zellij()), 0)
        self.assertIn('zellij 0.46.0', out.getvalue())
        with patch.dict(os.environ, {'FAKE_OLD': '1'}), redirect_stdout(io.StringIO()) as out2:
            self.assertEqual(zellij.doctor(zellij.Zellij()), 1)
        self.assertIn('MISSING', out2.getvalue())

    def test_shared_project_creates_destination_outside_zellij(self):
        z=zellij.Zellij(env={})
        with patch('agent_utils.core.zellij.list_sessions',return_value={}):
            target,create=zellij.target(z,{}, {'id':'example'})
        self.assertEqual(target.session,'agents')
        self.assertTrue(create)
        with patch('agent_utils.core.zellij.list_sessions',return_value={'agents':'running'}):
            target,create=zellij.target(z,{}, {'id':'example'})
        self.assertFalse(create)


class RenameTests(FakeZellijCase):
    def test_rename_keeps_project_prefix(self):
        env = {'ZELLIJ': '0', 'ZELLIJ_PANE_ID': '10', 'ZELLIJ_SESSION_NAME': 'main', 'AGTERM_UTILS_ZELLIJ': str(self.bin)}
        state = Path(self.tmp.name) / 'state.json'
        state.write_text(json.dumps({'next': 5, 'tabs': [{'tab_id': 0, 'name': 'Demo: claude'}]}))
        self.assertEqual(zellij.rename_own_tab('Fix login', env), 'ok')
        self.assertEqual(json.loads(state.read_text())['tabs'][0]['name'], 'Demo: Fix login')
        self.assertIsNone(zellij.rename_own_tab('x', {}))

    def test_agent_rename_reports_zellij(self):
        from agent_utils.agterm import names
        env = {'ZELLIJ': '0', 'ZELLIJ_PANE_ID': '10', 'ZELLIJ_SESSION_NAME': 'main', 'AGTERM_UTILS_ZELLIJ': str(self.bin)}
        report = names.rename('Name', agent='codex', sid=None, env=env)
        self.assertIn(('zellij', 'ok'), report)


class HooksTests(unittest.TestCase):
    def test_setup_plan_adds_and_removes_only_our_entries(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            settings = home / '.claude/settings.json'
            settings.parent.mkdir()
            settings.write_text(json.dumps({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'other'}]}]}}))
            script = home / 'zellij_status.py'
            with patch.object(setup_status, 'agent_executable', return_value=None):
                plan = setup_status.make_plan(home, script, False, every=False)
            self.assertEqual([p[0] for p in plan], ['claude'])
            hooks = json.loads(plan[0][2])['hooks']
            self.assertEqual(set(hooks), {'UserPromptSubmit', 'PostToolUse', 'PermissionRequest', 'Stop', 'Notification'})
            self.assertEqual(len(hooks['Stop']), 2)
            settings.write_text(plan[0][2])
            with patch.object(setup_status, 'agent_executable', return_value=None):
                removed = json.loads(setup_status.make_plan(home, script, True)[0][2])
            self.assertEqual(removed['hooks']['Stop'][0]['hooks'][0]['command'], 'other')
            self.assertNotIn('UserPromptSubmit', removed['hooks'])

    def test_hook_script_is_a_silent_noop_outside_zellij(self):
        import subprocess, sys
        root = Path(__file__).resolve().parents[1]
        env = {k: v for k, v in os.environ.items() if not k.startswith('ZELLIJ')}
        done = subprocess.run([sys.executable, str(root / 'agent_utils/core/zellij_status.py'), 'done', 'claude'],
                              input='{}', text=True, capture_output=True, env=env)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, '', ''))


if __name__ == '__main__':
    unittest.main()


class KiroHookZellijTests(unittest.TestCase):
    def test_kiro_hook_emits_tab_status(self):
        from agent_utils.agterm import kiro_hook
        calls = []
        with patch('agent_utils.core.zellij.emit_status', side_effect=lambda *a, **k: calls.append((a, k))):
            kiro_hook.zellij_status('attention', {'tool_name': 'shell'})
            kiro_hook.zellij_status('active', {})
            kiro_hook.zellij_status('completed', {})
        self.assertEqual([c[0][0] for c in calls], ['blocked', 'working', 'done'])
        self.assertEqual(calls[0][0][1], 'permission')
        self.assertIn('shell', calls[0][0][2])


class LayoutTests(FakeZellijCase):
    def test_render_wraps_template_and_escapes(self):
        from agent_utils.core import zellij_layouts as L
        kdl = L.render('agent-shell', 'Demo "x": agent', '/r/a b', ['env', 'A=1', 'say "hi"'], {'name': 'Demo'},
                       template=L.tab_template('layout {\n    new_tab_template {\n        pane size=1\n        pane\n    }\n}'))
        self.assertIn('default_tab_template {', kdl)
        self.assertIn('children', kdl)
        self.assertIn('tab name="Demo \\"x\\": agent" cwd="/r/a b" focus=true {', kdl)
        self.assertIn('pane name="agent" focus=true command="env" {', kdl)
        self.assertIn('args "A=1" "say \\"hi\\""', kdl)
        self.assertIn('pane name="shell" size="30%"', kdl)
        self.assertEqual(L.tab_template('no template'), L.FALLBACK_TEMPLATE)

    def test_logs_placeholder_and_body_checks(self):
        from agent_utils.core import zellij_layouts as L
        kdl = L.render('agent-shell-logs', 'T', '/r', ['a'], {'name': 'P', 'zellij': {'logs_command': ['tail', '-f', 'x.log']}})
        self.assertIn('pane name="logs" command="tail" start_suspended=true {', kdl)
        self.assertIn('pane name="agent" focus=true command="a"', kdl)
        for body in ('pane\n', '{{agent}}\n{{agent}}\n', '{{agent}}\npane {\n'):
            with self.assertRaises(ValueError):L.check_body(body)
        with self.assertRaises(ValueError):L.check_name('../x')

    def test_own_file_overrides_and_new_never_overwrites(self):
        from agent_utils.core import zellij_layouts as L
        path = L.new_file('web', 'agent-shell-below')
        self.assertTrue(path.read_text().startswith('//'))
        with self.assertRaises(ValueError):L.new_file('web')
        path.write_text('pane split_direction="vertical" {\n    {{agent}}\n    pane name="dev" cwd="{{root}}/web"\n}\n')
        self.assertIn(('web', 'file'), L.available())
        self.assertIn('cwd="/r/web"', L.render('web', 'T', '/r', ['a']))

    def test_open_with_project_layout_finds_agent_pane(self):
        root = Path(self.tmp.name) / 'repo';root.mkdir()
        config = Path(self.tmp.name) / 'config.json'
        config.write_text(json.dumps({'version': 1, 'windows': [{'name': 'Work', 'default_agent': 'claude', 'projects': [
            {'id': 'demo', 'name': 'Demo', 'roots': [str(root)], 'zellij': {'layout': 'agent-shell'}}]}]}))
        with patch('shutil.which', return_value='/bin/claude'), redirect_stdout(io.StringIO()) as out:
            zellij.command(['open', '--config', str(config), '--project', 'demo'])
        self.assertIn('(layout agent-shell)', out.getvalue())
        new = next(c for c in self.log() if c[0] == 'new-tab')
        kdl = new[new.index('--layout-string') + 1]
        self.assertIn('compact-bar', kdl)  # the session's own template
        res = next(c for c in self.log() if c[0] == 'set-resurrect-command')
        self.assertEqual(res[res.index('-p') + 1], '11')
        self.assertEqual(res[res.index('--') + 1], 'env')


class ProjectSessionTests(FakeZellijCase):
    def config(self, mode):
        root = Path(self.tmp.name) / 'repo';root.mkdir(exist_ok=True)
        config = Path(self.tmp.name) / 'config.json'
        config.write_text(json.dumps({'version': 1, 'windows': [{'name': 'Work', 'default_agent': 'claude', 'projects': [
            {'id': 'demo', 'name': 'Demo', 'roots': [str(root)], 'zellij': {'session': mode}}]}]}))
        return str(config)

    def run_cmd(self, *argv):
        with patch('shutil.which', return_value='/bin/claude'), redirect_stdout(io.StringIO()) as out:
            code = zellij.command(list(argv))
        return code, out.getvalue()

    def test_creates_headless_session_then_adds_tabs(self):
        config = self.config('project')
        with patch.dict(os.environ, {'ZELLIJ': '0', 'ZELLIJ_PANE_ID': '3', 'AGTERM_SESSION_ID': 'stale'}):
            code, out = self.run_cmd('open', '--config', config, '--project', 'demo')
        self.assertIn('Created session with tab 0 "Demo: claude" in Zellij session agents-demo', out)
        self.assertIn('Switched to Zellij session agents-demo.', out)
        self.assertIn(['switch-session', 'agents-demo'], self.log())
        create = next(c for c in self.log() if c[:1] == ['--layout-string'])
        self.assertEqual(create[2:], ['attach', '--create-background', 'agents-demo'])
        from agent_utils.agterm.sessions import records
        self.assertEqual(next(iter(records().values()))['zellij']['session'], 'agents-demo')
        self.assertIn('agents-demo', zellij.known_sessions())
        code, out = self.run_cmd('open', '--config', config, '--project', 'demo', '--task', 'T-2')
        self.assertIn('Opened tab 1 "Demo: T-2" in Zellij session agents-demo', out)
        self.assertIn('Attach with:', out)
        rows = json.loads(self.run_cmd('list', '--all', '--json')[1])
        self.assertEqual(sorted(r['session'] for r in rows if r['record']), ['agents-demo', 'agents-demo'])

    def test_switch_failure_falls_back_to_hint(self):
        config = self.config('project')
        with patch.dict(os.environ, {'ZELLIJ': '0', 'ZELLIJ_PANE_ID': '3', 'FAKE_NO_SWITCH': '1'}), patch('time.sleep'):
            code, out = self.run_cmd('open', '--config', config, '--project', 'demo')
        self.assertIn('Ctrl o w', out)

    def test_refuses_foreign_and_exited_sessions(self):
        config = self.config('project')
        (Path(self.tmp.name) / 'state-agents-demo.json').write_text(json.dumps({'next': 1, 'tabs': [{'tab_id': 0, 'name': 'x'}]}))
        with self.assertRaisesRegex(zellij.ZellijError, 'not created by agents'):
            self.run_cmd('open', '--config', config, '--project', 'demo')
        (Path(self.tmp.name) / 'exited-agents-demo').write_text('')
        with self.assertRaisesRegex(zellij.ZellijError, 'has exited'):
            self.run_cmd('open', '--config', config, '--project', 'demo')

    def test_explicit_name_may_use_existing_session(self):
        config = self.config('team')
        (Path(self.tmp.name) / 'state-team.json').write_text(json.dumps({'next': 4, 'tabs': [{'tab_id': 0, 'name': 'x'}]}))
        code, out = self.run_cmd('open', '--config', config, '--project', 'demo')
        self.assertIn('Opened tab 4', out)

    def test_create_session_strips_agterm_and_zellij_env(self):
        z = zellij.Zellij('agents-x', env={'AGTERM_UTILS_ZELLIJ': str(self.bin), 'AGTERM_SESSION_ID': 'a', 'ZELLIJ': '0', 'PATH': os.environ['PATH']})
        with patch('subprocess.run', wraps=zellij.subprocess.run) as run:
            z.create_session('layout {\n    tab name="X: a" {\n        pane name="agent" command="a" {\n            args "b"\n        }\n    }\n}')
        env = run.call_args_list[0].kwargs['env']
        self.assertNotIn('AGTERM_SESSION_ID', env);self.assertNotIn('ZELLIJ', env)

    def test_attach_argv_uses_ssh_config_over_ssh(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / '.config/zellij').mkdir(parents=True)
            (Path(home) / '.config/zellij/config-ssh.kdl').write_text('')
            with patch('pathlib.Path.home', return_value=Path(home)):
                argv = zellij.attach_argv('agents-demo', {'SSH_TTY': '/dev/ttys1', 'AGTERM_UTILS_ZELLIJ': 'zellij'})
                self.assertEqual(argv, ['zellij', '--config', str(Path(home) / '.config/zellij/config-ssh.kdl'), 'attach', 'agents-demo'])
                self.assertEqual(zellij.attach_argv('s', {'AGTERM_UTILS_ZELLIJ': 'zellij'}), ['zellij', 'attach', 's'])


class AdoptTests(FakeZellijCase):
    TABLE = {1: (0, 'launchd'), 50: (1, '/opt/homebrew/bin/zellij --server /tmp/x/main'), 60: (50, '/bin/zsh'),
             70: (60, 'claude --resume 11111111-2222-3333-4444-555555555555'), 71: (70, 'node helper'),
             80: (60, '/opt/homebrew/bin/kiro-cli chat'), 81: (80, '/x/bun'), 82: (81, '/x/node'), 83: (82, 'sh -c hook'),
             90: (1, '/bin/zsh'), 91: (90, 'claude --resume 11111111-2222-3333-4444-555555555555')}

    def test_ancestor_and_ids(self):
        from agent_utils.core import zellij_adopt as A
        self.assertEqual(A.provider_ancestor(83, self.TABLE)[:2], (80, 'kiro-cli'))
        self.assertEqual(A.provider_ancestor(71, self.TABLE)[:2], (70, 'claude'))
        self.assertIsNone(A.provider_ancestor(60, self.TABLE))
        self.assertTrue(A.under_zellij(70, self.TABLE));self.assertFalse(A.under_zellij(91, self.TABLE))
        self.assertEqual(A.agent_of('node /usr/lib/node_modules/codex/bin/codex'), 'codex')
        self.assertIsNone(A.agent_of('node /x/server.js'))
        self.assertEqual(A.agent_of('node /opt/homebrew/bin/codex resume x'), 'codex')
        self.assertEqual(A.conversation_from_args('codex', 'codex resume abc --no-daemon'), 'abc')
        self.assertEqual(A.conversation_from_args('claude', 'claude --session-id=abc'), 'abc')
        self.assertEqual(A.conversation_from_args('kiro-cli', 'kiro-cli chat --resume-id sess_1'), 'sess_1')
        self.assertIsNone(A.conversation_from_args('claude', 'claude'))

    def state_with_hand_tab(self, cwd):
        state = Path(self.tmp.name) / 'state.json'
        state.write_text(json.dumps({'next': 3, 'tabs': [{'tab_id': 0, 'name': 'Tab #1'},
            {'tab_id': 2, 'name': 'by hand', 'fg': 'claude --resume 11111111-2222-3333-4444-555555555555', 'cwd': cwd}]}))

    def test_adopt_pane_unique_match_sets_resurrect(self):
        from agent_utils.core import zellij_adopt as A
        root = Path(self.tmp.name) / 'repo';root.mkdir()
        self.state_with_hand_tab(str(root))
        z = zellij.Zellij('main')
        self.assertEqual([(p['id'], a) for p, a in A.adoptable(z)], [(12, 'claude')])
        with patch.object(A, 'process_table', return_value=self.TABLE), patch.object(A, 'process_cwd', return_value=str(root)), \
                patch('agent_utils.agterm.workers.process_birth', return_value='b'), patch('shutil.which', return_value='/bin/claude'):
            record = A.adopt_pane(z, 12, str(Path(self.tmp.name) / 'none.json'), rename=True)
            self.assertEqual((record['provider_pid'], record['conversation_id'], record['status']),
                             (70, '11111111-2222-3333-4444-555555555555', 'adopted'))
            self.assertTrue(record['zellij']['resumable'])
            res = next(c for c in self.log() if c[0] == 'set-resurrect-command')
            self.assertIn('--resume', res);self.assertIn('AGTERM_UTILS_RECORD=' + record['id'], res)
            self.assertIn(['rename-tab-by-id', '2', 'repo: claude'], self.log())
            self.assertEqual(A.adoptable(z), [])
            with self.assertRaisesRegex(ValueError, 'No unmanaged'):
                A.adopt_pane(z, 12, 'none.json')

    def test_adopt_pane_refuses_ambiguity(self):
        from agent_utils.core import zellij_adopt as A
        self.state_with_hand_tab('/r')
        table = dict(self.TABLE);table[95] = (60, 'claude --resume 11111111-2222-3333-4444-555555555555')
        with patch.object(A, 'process_table', return_value=table), patch.object(A, 'process_cwd', return_value='/r'):
            with self.assertRaisesRegex(ValueError, '2 claude processes match pane 12'):
                A.adopt_pane(zellij.Zellij('main'), 12, 'none.json')

    def test_adopt_self(self):
        from agent_utils.core import zellij_adopt as A
        env = {'ZELLIJ': '0', 'ZELLIJ_PANE_ID': '10', 'ZELLIJ_SESSION_NAME': 'main', 'AGTERM_UTILS_ZELLIJ': str(self.bin),
               'KIRO_SESSION_ID': 'sess_abc'}
        managed = {**self.TABLE, 80: (79, '/opt/homebrew/bin/kiro-cli chat'), 79: (60, 'python3 /x/agent_utils/agterm/runner.py R -- kiro-cli chat')}
        with patch.object(A, 'process_table', return_value=managed), patch('os.getppid', return_value=83):
            self.assertIn('already managed', A.adopt_self('c.json', env)[1])
        self.assertIn('not inside', A.adopt_self('c.json', {})[1])
        with patch.object(A, 'process_table', return_value=self.TABLE), patch('os.getppid', return_value=83), \
                patch.object(A, 'process_cwd', return_value='/w'), patch('agent_utils.agterm.workers.process_birth', return_value='b'), \
                patch('shutil.which', return_value='/bin/kiro-cli'):
            record, message = A.adopt_self('c.json', env)
            self.assertEqual((record['agent'], record['conversation_id'], record['zellij']['pane_id']), ('kiro-cli', 'sess_abc', 10))
            self.assertIn('resumes after a Zellij restart', message)
            with self.assertRaisesRegex(ValueError, 'Already managed'):
                A.adopt_self('c.json', env)

    def test_hook_adopt_is_silent(self):
        import subprocess, sys
        root = Path(__file__).resolve().parents[1]
        env = {k: v for k, v in os.environ.items() if not k.startswith('ZELLIJ')}
        done = subprocess.run([sys.executable, str(root / 'agent_utils/core/zellij_status.py'), 'adopt', 'claude'],
                              input='{"session_id": "x"}', text=True, capture_output=True, env=env)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, '', ''))


class AdoptHookSetupTests(unittest.TestCase):
    def test_adopt_flag_adds_session_start_and_kiro_file(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / '.codex').mkdir();(home / '.codex/hooks.json').write_text('{}')
            with patch.object(setup_status, 'agent_executable', side_effect=lambda a: '/x' if a == 'kiro-cli' else None):
                plan = setup_status.make_plan(home, home / 'zellij_status.py', False, adopt=True)
                self.assertEqual([p[0] for p in plan], ['codex', 'kiro-cli'])
                start = json.loads(plan[0][2])['hooks']['SessionStart'][0]
                self.assertEqual(start['matcher'], 'startup|resume')
                self.assertIn('adopt codex', start['hooks'][0]['command'])
                self.assertIn('adopt kiro-cli', plan[1][2])
                (home / setup_status.KIRO_ADOPT).parent.mkdir(parents=True)
                (home / setup_status.KIRO_ADOPT).write_text(plan[1][2])
                removed = setup_status.make_plan(home, home / 'zellij_status.py', True)
                self.assertIn(('kiro-cli', home / setup_status.KIRO_ADOPT, None), removed)


class ConfigTests(unittest.TestCase):
    def test_zellij_key_validation(self):
        from agent_utils.cli import validate
        def cfg(z):
            return {'version': 1, 'windows': [{'name': 'W', 'default_agent': 'claude', 'projects': [
                {'id': 'p', 'name': 'P', 'roots': ['/x'], 'zellij': z}]}]}
        validate(cfg({'layout': 'agent-shell', 'session': 'project', 'logs_command': ['tail', '-f', 'x']}))
        for bad in ({'layout': '../x'}, {'session': 'a b'}, {'other': 1}, {'logs_command': []}, 'x'):
            with self.assertRaises(ValueError):validate(cfg(bad))


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / 'config.json'
        self.config.write_text(json.dumps({'version': 1, 'windows': [{'name': 'W', 'default_agent': 'claude', 'projects': [
            {'id': 'p', 'name': 'P', 'roots': ['/x']}]}]}))
        patcher = patch.dict(os.environ, {'XDG_CONFIG_HOME': self.tmp.name});patcher.start();self.addCleanup(patcher.stop)

    def project(self):
        return json.loads(self.config.read_text())['windows'][0]['projects'][0]

    def test_admin_settings_flags(self):
        from agent_utils.cli import main
        with redirect_stdout(io.StringIO()):
            main(['--config', str(self.config), 'settings', '--project', 'p', '--zellij-layout', 'agent-shell', '--zellij-session', 'project'])
        self.assertEqual(self.project()['zellij'], {'layout': 'agent-shell', 'session': 'project'})
        with redirect_stdout(io.StringIO()):
            main(['--config', str(self.config), 'settings', '--project', 'p', '--zellij-layout', '', '--zellij-session', ''])
        self.assertNotIn('zellij', self.project())
        with self.assertRaises(ValueError):
            main(['--config', str(self.config), 'settings', '--zellij-layout', 'x'])

    def test_layout_set_menu(self):
        answers = iter([('project', 'p'), 'agent-shell-below', 'named'])
        with redirect_stdout(io.StringIO()) as out:
            zellij.layout_set_menu(str(self.config), pick=lambda title, entries, **k: next(answers), ask=lambda prompt: 'team')
        self.assertEqual(self.project()['zellij'], {'layout': 'agent-shell-below', 'session': 'team'})
        self.assertIn('backup', out.getvalue())
        answers = iter([('project', 'p'), None, 'shared'])
        with redirect_stdout(io.StringIO()):
            zellij.layout_set_menu(str(self.config), pick=lambda title, entries, **k: next(answers))
        self.assertEqual(self.project()['zellij'], {'layout': 'agent-shell-below'})

    def test_configure_lists_zellij(self):
        from agent_utils.core import settings
        self.assertIn('zellij-settings', [a.id for a in settings.ACTIONS])
        self.assertTrue(any('Zellij' in row[1] for row in settings.managed(Path(self.tmp.name))))


class PluginFileTests(unittest.TestCase):
    def test_links_for_pi_and_opencode(self):
        with tempfile.TemporaryDirectory() as d:
            home, package = Path(d), Path(d) / 'pkg'
            (home / '.pi/agent/extensions').mkdir(parents=True)
            with patch.object(setup_status, 'agent_executable', side_effect=lambda a: '/x' if a == 'opencode' else None):
                plan = setup_status.link_plan(home, package, False, False)
            self.assertEqual(sorted(p[0] for p in plan), ['opencode'])
            self.assertEqual(plan[0][2], str(package / 'opencode_zellij_status.js'))
            path = plan[0][1];path.parent.mkdir(parents=True);path.symlink_to(plan[0][2])
            with patch.object(setup_status, 'agent_executable', return_value=None):
                self.assertEqual(setup_status.link_plan(home, package, False, False), [])
                self.assertEqual([p[0] for p in setup_status.link_plan(home, package, True, False)], ['opencode'])

    def test_plugin_sources_ship(self):
        root = Path(__file__).resolve().parents[1] / 'agent_utils/core'
        for name in ('pi_zellij_status.ts', 'opencode_zellij_status.js'):
            text = (root / name).read_text()
            self.assertIn('7501', text);self.assertIn('ZELLIJ_PANE_ID', text)
        self.assertIn('*.js', (root.parents[1] / 'pyproject.toml').read_text())
