"""Zellij tabs for project sessions: `agents zellij open|list|focus|close|menu|adopt|layout|doctor`.

Tabs are found by their stable tab ID, never by name (names may repeat). IDs are valid only for the
life of one Zellij server: a resurrected session renumbers them, so the runner rewrites the record's
`zellij` field every time it starts. Written against Zellij 0.46;
every command used is checked against `zellij action --help` before `open` runs.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

NEEDED = ('new-tab', 'list-tabs', 'list-panes', 'go-to-tab-by-id', 'close-tab-by-id', 'rename-tab-by-id',
          'set-resurrect-command')
STATES = ('idle', 'working', 'done', 'blocked', 'error', 'clear')
KINDS = ('permission', 'question', 'auth')
COMMANDS = ('run', 'launcher', 'open', 'list', 'focus', 'close', 'menu', 'adopt', 'layout', 'doctor')


class ZellijError(ValueError):
    pass


def binary(env=None):
    env = os.environ if env is None else env
    return env.get('AGTERM_UTILS_ZELLIJ') or shutil.which('zellij') or 'zellij'


def in_zellij(env=None):
    env = os.environ if env is None else env
    return bool(env.get('ZELLIJ') is not None and env.get('ZELLIJ_PANE_ID'))


# ---- status (OSC 7501) -------------------------------------------------------------------------

def osc_status(state, kind=None, msg=None, app=None, progress=None):
    """The escape sequence Zellij reads as a pane's program status."""
    if state not in STATES:
        raise ValueError('state must be one of: ' + ', '.join(STATES))
    parts = ['state=' + state]
    if kind:
        if kind not in KINDS:raise ValueError('kind must be one of: ' + ', '.join(KINDS))
        parts.append('kind=' + kind)
    if progress is not None:parts.append('progress=%d' % max(0, min(100, int(progress))))
    if app:parts.append('app=' + re.sub(r'[^A-Za-z0-9._-]', '', app)[:32])
    if msg:parts.append('msg=' + base64.b64encode(msg.encode()[:2048]).decode())
    return '\x1b]7501;' + ':'.join(parts) + '\x1b\\'


def emit_status(state, kind=None, msg=None, app=None, env=None, tty='/dev/tty'):
    """Write the status to the pane's terminal. Silent no-op outside Zellij; returns True if written."""
    env = os.environ if env is None else env
    if not in_zellij(env):return False
    try:
        text = osc_status(state, kind, msg, app)
        with open(tty, 'w') as stream:stream.write(text)
        return True
    except (OSError, ValueError):
        return False


# ---- the Zellij CLI -----------------------------------------------------------------------------

class Zellij:
    def __init__(self, session=None, dry=False, env=None, config=None):
        self.env = os.environ if env is None else env
        self.session = session or self.env.get('ZELLIJ_SESSION_NAME')
        self.dry = dry
        self.config = config
        self._checked = False

    def argv(self, *args):
        return [binary(self.env)] + (['--session', self.session] if self.session else []) + ['action', *args]

    def run(self, *args, mutating=False):
        if not self.session:
            raise ZellijError('Not inside a Zellij session; pass --session NAME (or run `zellij attach -c main`)')
        argv = self.argv(*args)
        if self.dry and mutating:
            print(shlex.join(argv));return ''
        try:
            done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ZellijError('zellij failed: %s' % exc)
        if done.returncode:
            raise ZellijError('zellij %s: %s' % (args[0], (done.stderr or done.stdout).strip()[:200] or 'exit %d' % done.returncode))
        return done.stdout

    def check(self):
        """Fail early when this Zellij lacks a command we use (older releases)."""
        if self._checked:return
        text = self.run('--help')
        missing = [c for c in NEEDED if not re.search(r'^\s+%s\s' % re.escape(c), text, re.M)]
        if missing:raise ZellijError('This Zellij lacks: %s (needs 0.46 or newer)' % ', '.join(missing))
        self._checked = True

    def tabs(self):
        return json.loads(self.run('list-tabs', '--json', '--all') or '[]')

    def panes(self):
        return [p for p in json.loads(self.run('list-panes', '--json', '--tab', '--command') or '[]') if not p.get('is_plugin')]

    def new_tab(self, name, cwd, argv, focus=True, layout=None):
        """Create a tab and return (tab_id, agent pane_id). Zellij prints an ID even when no client is
        attached, so the tab is looked up afterwards. With `layout` (rendered KDL) the agent pane is the
        one named `agent`."""
        if layout:
            args = ['new-tab', '--layout-string', layout] + ([] if focus else ['--no-focus'])
        else:
            # A launcher workspace has a command in its new-tab template. Zellij's
            # initial command does not replace that command, so give managed tabs
            # their own explicit agent pane, just as configured layouts do.
            from .zellij_layouts import render
            layout = render('agent', name, cwd, argv, template=self.tab_template())
            args = ['new-tab', '--layout-string', layout] + ([] if focus else ['--no-focus'])
        out = self.run(*args, mutating=True)
        if self.dry:return 0, 0
        try:tab_id = int(out.strip())
        except ValueError:raise ZellijError('Unexpected new-tab output: %r' % out[:80])
        if tab_id not in [t.get('tab_id') for t in self.tabs()]:
            raise ZellijError('Zellij created no tab: the session has no attached client. Attach one (zellij attach %s) and retry' % self.session)
        return tab_id, self.agent_pane(tab_id)

    def agent_pane(self, tab_id):
        panes = [p for p in self.panes() if p.get('tab_id') == tab_id]
        named = [p for p in panes if p.get('title') == 'agent']
        return (named or panes or [{'id': None}])[0]['id']

    def tab_template(self):
        """This session's new-tab template, so layout tabs get the same bars."""
        from .zellij_layouts import tab_template
        try:return tab_template(self.run('dump-layout'))
        except ZellijError:return tab_template('')

    def create_session(self, layout, env=None):
        """Start this session in the background with `layout` as its first tab (no client needed).
        Agterm and Zellij variables are left out so the new server does not inherit stale ones."""
        argv = [binary(self.env)]
        path = ui_config(self.config, self.env)
        if path:argv += ['--config', path]
        argv += ['--layout-string', layout, 'attach', '--create-background', self.session]
        if self.dry:
            print(shlex.join(['<layout>' if arg == layout else arg for arg in argv]));return 0, 0
        clean = {k: v for k, v in (env or self.env).items() if not k.startswith(('AGTERM_', 'ZELLIJ'))}
        try:done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, timeout=20, env=clean)
        except (OSError, subprocess.SubprocessError) as exc:raise ZellijError('zellij failed: %s' % exc)
        if done.returncode:raise ZellijError('zellij attach: ' + ((done.stderr or done.stdout).strip()[:300] or 'failed'))
        import time
        for _ in range(30):
            try:
                tabs = self.tabs()
                if tabs:return tabs[0]['tab_id'], self.agent_pane(tabs[0]['tab_id'])
            except (ZellijError, ValueError):pass
            time.sleep(0.2)
        raise ZellijError('Session %s did not come up' % self.session)

    def set_resurrect(self, pane_id, argv):
        """What the pane runs after a Zellij restart. Zellij still starts it suspended (press ENTER)."""
        if pane_id is None and not self.dry:return False
        try:self.run('set-resurrect-command', '-p', str(pane_id), '--', *argv, mutating=True)
        except ZellijError:return False
        return True

    def focus(self, tab_id):self.run('go-to-tab-by-id', str(tab_id), mutating=True)
    def close(self, tab_id):self.run('close-tab-by-id', str(tab_id), mutating=True)
    def rename(self, tab_id, name):self.run('rename-tab-by-id', str(tab_id), name, mutating=True)


def list_sessions(env=None):
    """{name: 'active' | 'exited'} from `zellij list-sessions --no-formatting`."""
    env = os.environ if env is None else env
    try:out = subprocess.run([binary(env), 'list-sessions', '--no-formatting'], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):return {}
    result = {}
    for line in out.splitlines():
        words = line.split()
        if words and not line.startswith(' '):
            result[words[0]] = 'exited' if 'EXITED' in line else 'active'
    return result


def locate_self(env=None, zellij=None):
    """(session, tab_id, pane_id) of the pane this process runs in, or None."""
    env = os.environ if env is None else env
    if not in_zellij(env):return None
    try:
        pane = int(env['ZELLIJ_PANE_ID'])
        z = zellij or Zellij(env=env)
        for p in z.panes():
            if p.get('id') == pane:return z.session, p.get('tab_id'), pane
    except (ValueError, ZellijError, OSError):pass
    return None


def rename_own_tab(name, env=None):
    """Hook for `agents agent rename`: keeps a `Project: ` prefix. Returns a report string, or None outside Zellij."""
    env = os.environ if env is None else env
    if not in_zellij(env):return None
    try:
        z = Zellij(env=env)
        found = locate_self(env, z)
        if not found:return 'skipped: pane not found'
        tab = next((t for t in z.tabs() if t.get('tab_id') == found[1]), {})
        prefix = tab.get('name', '').split(': ', 1)[0] + ': ' if ': ' in tab.get('name', '') else ''
        if name.startswith(prefix):prefix = ''
        z.rename(found[1], prefix + name)
        return 'ok'
    except ZellijError as exc:
        return 'failed: ' + str(exc)


# ---- registry join ------------------------------------------------------------------------------

def zellij_records():
    from ..agterm.sessions import records
    return [r for r in records().values() if r.get('zellij')]


def provider_alive(record):
    pid = record.get('provider_pid')
    if not pid or record.get('exited_at'):return False
    try:
        os.kill(int(pid), 0)
        from ..agterm.workers import process_birth
        return not record.get('provider_birth') or process_birth(int(pid)) == record['provider_birth']
    except (OSError, ValueError):return False


RECORD_ID = re.compile(r'AGTERM_UTILS_RECORD=([0-9a-f-]{36})|runner\.py\s+([0-9a-f-]{36})\s+--')


def tab_records(z):
    """{tab_id: record} for this session. Joined by the record ID in the pane's own command line, which
    survives a Zellij restart (tab IDs are renumbered). A pane whose command shows no record ID falls
    back to the tab ID stored in the record."""
    by_id = {r['id']: r for r in zellij_records()}
    stored = {r['zellij'].get('tab_id'): r for r in by_id.values() if r['zellij'].get('session') == z.session}
    found, parsed = {}, set()
    for pane in z.panes():
        m = RECORD_ID.search(pane.get('terminal_command') or '')
        if m:
            parsed.add(pane['tab_id'])
            record = by_id.get(m.group(1) or m.group(2))
            if record and not record.get('archived'):found[pane['tab_id']] = record
    for tab_id, record in stored.items():
        if tab_id not in parsed and tab_id not in found and record['id'] not in {r['id'] for r in found.values()}:
            found[tab_id] = record
    return found


def live_records(z, project=None):
    """Records whose tab still exists in this session: [(record, tab)]."""
    tabs = {t['tab_id']: t for t in z.tabs()}
    return [(r, tabs[tab_id]) for tab_id, r in tab_records(z).items()
            if tab_id in tabs and (not project or r.get('project') == project)]


# ---- project settings and sessions ------------------------------------------------------------

def settings(window, project):
    """The project's `zellij` settings over its window's."""
    return {**(window.get('zellij') or {}), **(project.get('zellij') or {})}


def session_name(project, mode):
    if mode == 'project':
        return 'agents-' + re.sub(r'[^A-Za-z0-9_-]+', '-', project['id']).strip('-')[:60]
    return mode


def known_sessions():
    from ..agterm.state import locked_registry
    with locked_registry() as registry:return dict(registry.get('zellij_sessions', {}))


def remember_session(name, project_id):
    from ..agterm.state import locked_registry
    from ..agterm.sessions import now
    with locked_registry() as registry:
        registry.setdefault('zellij_sessions', {})[name] = {'project': project_id, 'created_at': now()}


def target(z, window, project):
    """(Zellij for the tab's session, create the session first?) for this project."""
    mode = settings(window, project).get('session', 'shared')
    if mode == 'shared':
        if z.session:return z, False
        # An SSH shell has no current multiplexer session. Use a stable shared
        # destination, creating its first agent tab through the usual layout path.
        mode = 'agents'
    name = session_name(project, mode)
    if name == z.session:return z, False
    tz = Zellij(name, z.dry, z.env, z.config)
    state = list_sessions(z.env).get(name)
    if state is None:return tz, True
    if state == 'exited':
        raise ZellijError('Zellij session %s has exited. Resurrect it first: zellij attach %s' % (name, name))
    if mode == 'project' and name not in known_sessions():
        raise ZellijError('A session named %s exists but was not created by agents; not adding tabs to it. '
                          'Set "zellij": {"session": "%s"} on the project to use it anyway.' % (name, name))
    return tz, False


def ui_config(config=None, env=None):
    """Resolve the terminal config independently of the agents project registry."""
    from ..cli import load
    env = os.environ if env is None else env
    data = load(config) if config and Path(config).is_file() else {}
    options = data.get('zellij', {})
    ssh = bool(env.get('SSH_CONNECTION') or env.get('SSH_TTY'))
    key = 'ssh_config_path' if ssh else 'config_path'
    path = options.get(key, '~/.config/zellij/config-ssh.kdl' if ssh else '')
    if not path:return None
    path = Path.home()/path[2:] if path.startswith('~/') else Path(path).expanduser()
    if not path.is_file():
        if key in options:raise ZellijError('Zellij config not found: ' + str(path))
        return None
    return str(path)


def attach_argv(name, env=None, config=None):
    env = os.environ if env is None else env
    argv = [binary(env)]
    path = ui_config(config, env)
    if path:argv += ['--config', path]
    return argv + ['attach', name]


def run_session(config, z):
    """Start the launcher workspace once, or attach without changing existing tabs."""
    from ..cli import load
    from .zellij_layouts import command_pane, indent
    data = load(config)
    name = z.session or data.get('zellij', {}).get('session', 'agents')
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', name) or name.startswith('-'):
        raise ZellijError('Use a session name with letters, digits, dot, dash or underscore')
    state = list_sessions(z.env).get(name)
    if state == 'exited':
        raise ZellijError('Session has exited; resurrect it explicitly with zellij attach ' + name)
    if in_zellij(z.env) and name == z.env.get('ZELLIJ_SESSION_NAME'):
        return launcher(config, z)
    if in_zellij(z.env):
        raise ZellijError('Run this from a plain shell; use the Zellij session manager to switch sessions')
    argv = attach_argv(name, z.env, config)
    if state is None:
        # Explicit argv, with no shell interpolation. Every new tab gets the chooser.
        command = [sys.executable, str(Path(__file__).with_name('zellij_launcher.py')),
                   '--config', str(Path(config).expanduser().resolve())]
        pane = command_pane('Start', command)
        body = pane + '\npane size=1 borderless=true {\n    plugin location="compact-bar"\n}'
        layout = 'layout {\n    default_tab_template {\n' + indent(body, '        ') + '\n    }\n    tab name="Start"\n}\n'
        argv[1:1] = ['--layout-string', layout]
        argv += ['--create']
    if z.dry:
        print(shlex.join(argv));return 0
    env = {k:v for k,v in z.env.items() if not k.startswith(('AGTERM_', 'AGT_', 'ZELLIJ'))}
    return subprocess.call(argv, env=env)


def launcher(config, z):
    from ..cli import pick
    choice = pick('New Zellij tab', [('Choose project and agent', 'agent'), ('Shell', 'shell')])
    if choice == 'agent':menu(config, z)
    # A chooser cancellation or completed launch leaves a usable shell in this tab.
    shell = z.env.get('SHELL') or '/bin/sh'
    if z.dry:
        print('Would open shell: ' + shell);return 0
    os.execv(shell, [shell, '-l'])


def how_to_get_there(name, env=None, config=None):
    env = os.environ if env is None else env
    if env.get('ZELLIJ_SESSION_NAME') == name:return None
    if in_zellij(env):
        return 'It is in Zellij session %s: open the session manager (Ctrl o w) and pick it.' % name
    return 'Attach with: ' + shlex.join(attach_argv(name, env, config))


def switch_client(name, tab_position=None, env=None):
    """Move the client of the current pane to session `name` (`zellij action switch-session`).

    Seen live on 0.46.0: it moves the client that last typed into the current session, so it works when
    run from a command the user just typed. Returns True when a client is listed in `name` afterwards."""
    env = os.environ if env is None else env
    argv = [binary(env), 'action', 'switch-session', name] + (['--tab-position', str(tab_position)] if tab_position is not None else [])
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, env=env)
        if done.returncode:return False
        import time
        for _ in range(10):
            out = subprocess.run([binary(env), '--session', name, 'action', 'list-clients'], stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, timeout=10).stdout
            if len(out.strip().splitlines()) > 1:return True
            time.sleep(0.2)
    except (OSError, subprocess.SubprocessError):pass
    return False


# ---- launch backend: plugs into agterm.sessions.launch -----------------------------------------

class Backend:
    """Stands in for the agterm client in sessions.launch: same dry/launch/call surface."""
    session_position = 'default'

    def __init__(self, z, label, focus=True, layout=None, create=False, template=None, task=None):
        self.z = z;self.dry = z.dry;self.label = label;self.focus = focus
        self.layout = layout;self.create = create;self.template = template;self.task = task

    def call(self, *args, **kwargs):return None
    def window(self, name):return name

    def launch(self, window, project, argv, cwd):
        from .zellij_layouts import render
        self.z.check()
        name = '%s: %s' % (project['name'], self.label)
        if self.create:
            kdl = render(self.layout or 'agent', name, cwd, argv, project, self.task, self.template)
            tab_id, pane_id = self.z.create_session(kdl)
            if not self.dry:remember_session(self.z.session, project['id'])
        elif self.layout:
            kdl = render(self.layout, name, cwd, argv, project, self.task, self.z.tab_template())
            tab_id, pane_id = self.z.new_tab(name, cwd, argv, self.focus, layout=kdl)
        else:
            tab_id, pane_id = self.z.new_tab(name, cwd, argv, self.focus)
        if self.dry:
            self.z.set_resurrect(0, argv);return None
        record_id = next((a.split('=', 1)[1] for a in argv[:3] if a.startswith('AGTERM_UTILS_RECORD=')), None)
        resumable = self.z.set_resurrect(pane_id, argv)
        if record_id:
            from ..agterm.runner import update
            update(record_id, zellij={'session': self.z.session, 'tab_id': tab_id, 'pane_id': pane_id, 'resumable': resumable})
        print('%s tab %d "%s" in Zellij session %s%s%s' % ('Created session with' if self.create else 'Opened', tab_id, name,
              self.z.session, ' (layout %s)' % self.layout if self.layout else '', '' if resumable else ' (resurrect command not set)'))
        return 'zellij:%s:%d' % (self.z.session, tab_id)


def open_tab(config, window, project, agent, z, resume=False, conversation=None, task=None, focus=True, layout=None):
    """Open the agent tab where the project's settings say. Returns the Zellij of that session."""
    from argparse import Namespace
    from ..agterm.sessions import launch
    from .zellij_layouts import check_name
    tz, create = target(z, window, project)
    layout = layout or settings(window, project).get('layout')
    if layout:check_name(layout)
    template = z.tab_template() if create and z.session else None
    args = Namespace(command='resume' if resume else 'new', session=conversation, prompt=None, task=task,
                     alias=[], pr=None, branch=None, repo=[], cwd=None)
    launch(Backend(tz, task or agent, focus, layout, create, template, task), window, project, agent, args)
    return tz


def find_project(data, ident):
    from ..cli import projects
    for w, p in projects(data):
        if p['id'] == ident or p['name'] == ident:return w, p
    raise ValueError('Unknown project: ' + ident)


def find_record(z, target_id):
    live = live_records(z)
    for r, tab in live:
        if target_id in (r['id'], r.get('task')) or r['id'].startswith(target_id) or str(tab['tab_id']) == target_id:
            return r, tab
    for tab in z.tabs():
        if str(tab['tab_id']) == target_id:return None, tab
    raise ValueError('No tab or record matches ' + target_id)


# ---- commands -----------------------------------------------------------------------------------

def attention(tab):
    return (tab.get('attention') or {}).get('status', '')


def rows(z):
    tabs = z.tabs()
    by_tab = tab_records(z)
    result = []
    for t in tabs:
        r = by_tab.get(t['tab_id'])
        state = r.get('status') if r else None
        if state in ('running', 'adopted') and not provider_alive(r):state = 'suspended'
        result.append({'session': z.session, 'tab_id': t['tab_id'], 'name': t['name'], 'active': t.get('active', False),
                       'attention': attention(t), 'record': r['id'] if r else None, 'project': r.get('project') if r else None,
                       'agent': r.get('agent') if r else None, 'status': state,
                       'resumable': bool(r and r['zellij'].get('resumable'))})
    shown = {x['record'] for x in result if x['record']}
    for r in zellij_records():
        if r['zellij'].get('session') == z.session and r['id'] not in shown and not r.get('archived'):
            result.append({'session': z.session, 'tab_id': None, 'name': r.get('title', ''), 'active': False, 'attention': '',
                           'record': r['id'], 'project': r.get('project'), 'agent': r.get('agent'), 'status': 'tab gone',
                           'resumable': False})
    return result


def all_sessions(z):
    """The current session plus every active session agents created."""
    active = list_sessions(z.env)
    names = ([z.session] if z.session else []) + sorted(n for n in known_sessions() if active.get(n) == 'active' and n != z.session)
    return [z] + [Zellij(n, z.dry, z.env) for n in names if n != z.session] if z.session else [Zellij(n, z.dry, z.env) for n in names]


def doctor(z, out=print):
    bad = 0
    try:version = subprocess.run([binary(z.env), '--version'], stdout=subprocess.PIPE, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out('zellij: not found');return 1
    out('zellij: ' + version)
    out('inside Zellij: ' + ('yes' if in_zellij(z.env) else 'no'))
    out('session: ' + (z.session or 'none (pass --session NAME)'))
    known = known_sessions()
    if known:
        states = list_sessions(z.env)
        out('project sessions: ' + ', '.join('%s (%s)' % (n, states.get(n, 'gone')) for n in sorted(known)))
    if not z.session:return 1
    try:
        text = z.run('--help')
        for c in NEEDED:
            ok = bool(re.search(r'^\s+%s\s' % re.escape(c), text, re.M))
            out('  %-22s %s' % (c, 'ok' if ok else 'MISSING'));bad += not ok
        out('tabs: %d' % len(z.tabs()))
    except ZellijError as exc:
        out('failed: ' + str(exc));return 1
    return 1 if bad else 0


def go_there(tz, tab_id=None, exec_attach=True):
    """Focus the tab and bring the user to its session: exec attach from outside Zellij, switch-session
    from inside (else a hint)."""
    if tab_id is not None:
        try:tz.focus(tab_id)
        except ZellijError:pass
    hint = how_to_get_there(tz.session, tz.env, tz.config)
    if not hint:return 0
    if tz.dry:
        print(hint);return 0
    if in_zellij(tz.env):
        position = None
        if tab_id is not None:
            try:position = next((t.get('position') for t in tz.tabs() if t.get('tab_id') == tab_id), None)
            except (ZellijError, ValueError):pass
        if switch_client(tz.session, position, tz.env):
            print('Switched to Zellij session %s.' % tz.session);return 0
    elif exec_attach and sys.stdin.isatty():
        argv = attach_argv(tz.session, tz.env, tz.config)
        os.execv(argv[0] if os.path.isabs(argv[0]) else shutil.which(argv[0]) or argv[0], argv)
    print(hint)
    return 0


def menu(config, z):
    from ..cli import load, pick, projects
    from ..agterm.startup import choose_session, choose_single_root, pick_provider
    data = load(config)
    entries = [(w['name'] + ' / ' + p['name'], (w, p)) for w, p in projects(data)]
    selected = pick('Project', entries)
    if selected is None:return 0
    window, project = selected
    try:tz, create = target(z, window, project)
    except ZellijError as exc:
        print(str(exc));return 2
    live = [] if create else live_records(tz, project['id'])
    if live:
        where = '' if tz.session == z.session else ' in session ' + tz.session
        choice = pick(project['name'] + where, [('Attach: %s (%s)' % (t['name'], attention(t) or 'idle'), ('attach', t))
                                               for r, t in live] + [('New or resume a session', ('new', None))])
        if choice is None:return 0
        if choice[0] == 'attach':return go_there(tz, choice[1]['tab_id'])
    default = project.get('default_agent', window['default_agent'])
    agent = pick_provider(project['name'], default, data, config, order=project.get('agent_order', window.get('agent_order')))
    if agent in (None, 'coordinator'):
        print('Choose an agent provider for a Zellij tab.' if agent else 'Cancelled.');return 0
    rooted = choose_single_root(project, agent, pick)
    if rooted is None:return 0
    action = choose_session(rooted, agent, coordinator=False)
    if action is None:return 0
    operation, ident = action
    tz = open_tab(config, window, rooted, agent, z, operation == 'resume', ident)
    return go_there(tz)


def layout_command(a):
    from . import zellij_layouts as L
    sub = a.target or 'list'
    if sub == 'list':
        for name, src in L.available():print('%-22s %s' % (name, src))
        print('Own layouts: %s/NAME.kdl' % L.layouts_dir())
        return 0
    if sub == 'set':return layout_set_menu(a.config)
    if not a.name:raise ValueError('layout %s needs a NAME' % sub)
    if sub == 'new':
        print('Created %s' % L.new_file(a.name, a.from_preset));return 0
    if sub == 'show':
        project, root = {'name': 'Project'}, '/path/to/project'
        if a.project:
            from ..cli import load
            window, project = find_project(load(a.config), a.project)
            project = {**project, 'zellij': settings(window, project)}
            root = str(Path(project['roots'][0]).expanduser())
        print(L.render(a.name, '%s: agent' % project['name'], root, ['<agent command>'], project, a.task), end='')
        return 0
    raise ValueError('layout needs list, show, new or set')


def layout_set_menu(config, pick=None, ask=input):
    """`agents zellij layout set`: choose a window or project, then its layout and session mode."""
    from . import zellij_layouts as L
    from ..cli import load
    from ..agterm.configuration import edit_config, set_zellij
    if pick is None:
        from ..cli import pick
    data = load(config)
    def label(item):
        z = item.get('zellij') or {}
        return 'layout %s, session %s' % (z.get('layout', '(none)'), z.get('session', '(shared)'))
    targets = []
    for w in data['windows']:
        targets.append(('Window %s: %s' % (w['name'], label(w)), ('window', w['name'])))
        targets += [('  %s / %s: %s' % (w['name'], p['name'], label(p)), ('project', p['id'])) for p in w.get('projects', [])]
    choice = pick('Zellij settings for', targets)
    if choice is None:return 0
    layout = pick('Layout', [('%s (%s)' % (n, src), n) for n, src in L.available()] +
                  [('No layout (single agent pane)', ''), ('Keep current', None)])
    session = pick('Session', [('Shared: the current Zellij session', 'shared'), ('Own session agents-<project id>', 'project'),
                               ('A named session', 'named'), ('Keep current', None)])
    if session == 'named':
        session = ask('Session name: ').strip() or None
    changes = {k: v for k, v in (('layout', layout), ('session', session)) if v is not None}
    if changes.get('session') == 'shared':changes['session'] = ''  # shared is the default: clear the key
    if not changes:
        print('No change.');return 0
    kind, ident = choice
    with edit_config(config) as fresh:
        set_zellij(fresh, ident if kind == 'window' else None, ident if kind == 'project' else None, changes)
    print('Saved (a backup of config.json is kept). Undo: agents admin config undo config --yes')
    return 0


def adopt_command(a, z):
    from . import zellij_adopt as A
    if a.self_:
        try:
            record, message = A.adopt_self(a.config, conversation=a.conversation, rename=a.rename, agent=a.agent)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            record, message = None, 'skipped: ' + str(exc)
        if not a.quiet:print(message)
        return 0 if record or a.quiet else 1
    z.check()
    if a.pane is not None:
        print(A.describe(A.adopt_pane(z, a.pane, a.config, a.conversation, a.rename)));return 0
    found = A.adoptable(z)
    if not found:
        print('No unmanaged agent panes in session %s.' % z.session);return 0
    if not a.all:
        for pane, agent in found:
            print('pane %-4s tab %-24s %-9s %s' % (pane['id'], pane.get('tab_name', '')[:24], agent, pane.get('pane_cwd') or ''))
        print('Adopt one with --pane ID, or all unambiguous ones with --all.')
        return 0
    bad = 0
    for pane, agent in found:
        try:print(A.describe(A.adopt_pane(z, pane['id'], a.config, None, a.rename)))
        except ValueError as exc:
            bad += 1;print('pane %s: %s' % (pane['id'], exc))
    return 1 if bad else 0


def command(argv):
    from ..cli import config_path, load
    p = argparse.ArgumentParser(prog='agents zellij', description='Project agent sessions as Zellij tabs')
    p.add_argument('operation', nargs='?', choices=COMMANDS)
    p.add_argument('target', nargs='?', help='focus/close: record id, task key or tab id; layout: list, show, new or set')
    p.add_argument('name', nargs='?', help='layout show/new: layout name')
    p.add_argument('--config', default=str(config_path()))
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--session', help='Zellij session name (default: the current one)')
    p.add_argument('--project');p.add_argument('--agent');p.add_argument('--task')
    p.add_argument('--resume', action='store_true', help='open: resume (provider picker, or --conversation ID)')
    p.add_argument('--conversation');p.add_argument('--no-focus', action='store_true')
    p.add_argument('--layout', help='open: layout for this tab instead of the project setting')
    p.add_argument('--from', dest='from_preset', default='agent-shell', help='layout new: preset to copy')
    p.add_argument('--self', dest='self_', action='store_true', help='adopt: the agent this command runs under')
    p.add_argument('--pane', type=int, help='adopt: Zellij terminal pane id')
    p.add_argument('--all', action='store_true', help='adopt: every unambiguous unmanaged agent pane; list: every session agents created')
    p.add_argument('--rename', action='store_true', help='adopt: rename the tab to "Project: agent"')
    p.add_argument('--quiet', action='store_true', help='adopt --self: print nothing, exit 0 (for hooks)')
    p.add_argument('--force', action='store_true', help='close: also close a tab whose provider still runs')
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    z = Zellij(a.session, a.dry_run, config=a.config)
    if a.operation is None:a.operation = 'menu' if in_zellij(z.env) else 'run'
    if a.operation == 'run':return run_session(a.config, z)
    if a.operation == 'launcher':return launcher(a.config, z)
    if a.operation == 'doctor':return doctor(z)
    if a.operation == 'layout':return layout_command(a)
    if a.operation == 'adopt':return adopt_command(a, z)
    if a.operation == 'menu':return menu(a.config, z)
    if a.operation == 'list':
        data = []
        for zs in (all_sessions(z) if a.all else [z]):
            zs.check();data += rows(zs)
        if a.json:print(json.dumps(data, indent=2))
        else:
            for r in data:
                print('%-16s %-4s %-28s %-9s %-10s %-9s %s' % (r['session'][:16], r['tab_id'] if r['tab_id'] is not None else '-',
                      r['name'][:28], r['attention'] or '-', r['agent'] or '-', r['status'] or 'no record',
                      (r['record'] or '')[:8] + (' resumable' if r['resumable'] else '')))
        return 0
    if a.operation in ('focus', 'close'):
        z.check()
        if not a.target:raise ValueError(a.operation + ' needs a TARGET (record id, task key or tab id)')
        record, tab = find_record(z, a.target)
        if a.operation == 'focus':
            z.focus(tab['tab_id']);return 0
        if record and provider_alive(record) and not a.force:
            raise ValueError('The provider in this tab is still running; use --force to close it')
        z.close(tab['tab_id']);return 0
    if not a.project:raise ValueError('open needs --project')
    data = load(a.config)
    window, project = find_project(data, a.project)
    if a.task:
        tz, create = target(z, window, project)
        for r, tab in ([] if create else live_records(tz, project['id'])):
            if r.get('task') == a.task:
                print('Task %s already has a tab; focusing it' % a.task)
                return go_there(tz, tab['tab_id'], exec_attach=False)
    agent = a.agent or project.get('default_agent', window['default_agent'])
    tz = open_tab(a.config, window, project, agent, z, a.resume or bool(a.conversation), a.conversation, a.task,
                  not a.no_focus, a.layout)
    if a.no_focus:
        hint = how_to_get_there(tz.session, tz.env, tz.config)
        if hint:print(hint)
        return 0
    return go_there(tz, exec_attach=False)
