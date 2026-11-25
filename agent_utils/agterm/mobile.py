"""`agents mobile`: the Mac side of the agterm mobile app.

serve   versioned JSON lines over stdin/stdout. The app runs `ssh MAC agents mobile serve`
        on its control channel. Nothing but protocol lines is written to stdout.
attach  one pane on a PTY channel, with the same checks as `agents remote terminal`.

The protocol is described in docs/mobile-protocol.md.
"""
import argparse
from contextlib import redirect_stdout
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading

from .remote import attach_pane, inventory

PROTOCOL = 1
MAX_LINE = 1 << 20
MAX_TEXT = 16384
PANES = ('left', 'right')


class RequestError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _ag(dry=False, position='default'):
    from ..cli import Agterm
    return Agterm(dry, position)


def _config(path):
    from ..cli import load
    if not path or not os.path.exists(path):
        return {'version': 1, 'windows': []}
    return load(path)


def _windows(ag):
    items = ag.call('window', 'list', json_result=True) or {}
    if isinstance(items, dict):
        items = items.get('windows', [])
    return [w for w in items if not w.get('closed') and w.get('open', True)]


def _agent_from(foreground):
    from ..cli import AGENTS
    words = [os.path.basename(str(x)) for x in foreground or []]
    return next((a for w in words for a in AGENTS if w == a), None)


def build_tree(ag):
    """Windows > workspaces > sessions, joined with zmx attachability and the registry."""
    from .sessions import records
    attachable = {s['id']: s for s in inventory(ag).get('sessions', [])}
    by_terminal = {r['terminal_id']: r for r in records().values() if r.get('terminal_id')}
    result = []
    for window in _windows(ag):
        tree = ag.call('tree', '--window', window['id'], json_result=True) or {}
        tree = tree.get('tree', tree)
        workspaces = []
        for workspace in tree.get('workspaces', []):
            sessions = []
            for row in workspace.get('sessions', []):
                live = attachable.get(row.get('id'))
                leads = {s.get('kind'): s.get('lead') for s in row.get('surfaces', [])}
                panes = [{'pane': p.get('pane'), 'lead': leads.get(p.get('pane')), 'foreground': p.get('foreground', [])}
                         for p in (live or {}).get('panes', [])]
                record = by_terminal.get(row.get('id'))
                foreground = row.get('foreground') or (panes[0]['foreground'] if panes else [])
                sessions.append({
                    'id': row.get('id'), 'name': row.get('name'), 'status': row.get('status', 'idle'),
                    'statusChangedAt': row.get('statusChangedAt'), 'cwd': row.get('cwd'),
                    'unseen': row.get('unseen', 0), 'flagged': bool(row.get('flagged')),
                    'info': row.get('info'), 'parent': row.get('parent'),
                    'companions': (live or {}).get('companions', []), 'split': bool(row.get('split')),
                    'attachable': live is not None, 'panes': panes,
                    'agent': (record or {}).get('agent') or _agent_from(foreground),
                    'record': {k: record.get(k) for k in ('id', 'project', 'task', 'conversation_id', 'title')} if record else None,
                })
            workspaces.append({'id': workspace.get('id'), 'name': workspace.get('name'), 'sessions': sessions})
        result.append({'id': window['id'], 'name': window.get('name'), 'active': bool(window.get('active')),
                       'workspaces': workspaces})
    return {'windows': result}


def _find(ag, session_id):
    """(window id, row) from a fresh tree, or RequestError('gone')."""
    if not isinstance(session_id, str) or not session_id:
        raise RequestError('bad_request', 'session is required')
    for window in build_tree(ag)['windows']:
        for workspace in window['workspaces']:
            for row in workspace['sessions']:
                if row['id'] == session_id:
                    return window['id'], row
    raise RequestError('gone', 'Session is not in the tree any more')


def list_projects(data):
    from ..cli import AGENT_NAMES, projects
    from .startup import ordered_agents, visible_agents
    available = visible_agents(data)
    result = []
    for window, project in projects(data):
        default = project.get('default_agent', window['default_agent'])
        agents = ordered_agents(available, default, project.get('agent_order', window.get('agent_order')))
        result.append({'id': project['id'], 'name': project['name'], 'window': window['name'],
                       'default_agent': default, 'roots': project['roots'],
                       'agents': [{'id': a, 'name': AGENT_NAMES.get(a, a)} for a in agents]})
    return result


def _project(data, project_id):
    from ..cli import projects
    match = [(w, dict(p)) for w, p in projects(data) if p['id'] == project_id]
    if not match:
        raise RequestError('bad_request', 'Unknown project: %s' % project_id)
    return match[0]


def history(data, params):
    from .history import claude_history, codex_history, opencode_session_entries
    from pathlib import Path
    _, project = _project(data, params.get('project'))
    agent = params.get('agent')
    limit = max(1, min(int(params.get('limit', 50)), 200))
    if agent == 'opencode':
        return [{'conversation_id': ident, 'title': label} for label, ident in opencode_session_entries(project)][:limit]
    if agent in ('claude', 'codex'):
        roots = [str(Path(r).expanduser().resolve()) for r in project['roots']]
        rows = (claude_history if agent == 'claude' else codex_history)(roots, limit)
        return [{'conversation_id': r['conversation_id'], 'title': r['title']} for r in rows]
    return []


def launch(ag, data, params):
    """New, resume or task launch through the same code as `agents agterm new|resume|task`."""
    from ..cli import AGENTS
    from .sessions import launch as launch_session, task_project
    from .startup import SINGLE_ROOT_AGENTS
    window, project = _project(data, params.get('project'))
    agent = params.get('agent') or project.get('default_agent', window['default_agent'])
    mode = params.get('mode', 'new')
    if agent not in AGENTS:
        raise RequestError('bad_request', 'Unsupported agent: %s' % agent)
    if mode not in ('new', 'resume', 'task'):
        raise RequestError('bad_request', 'mode must be new, resume or task')
    session = params.get('session')
    if agent == 'opencode' and mode == 'resume' and not session:
        raise RequestError('bad_request', 'OpenCode resume needs a session id from history')
    root = params.get('root')
    if agent in SINGLE_ROOT_AGENTS and len(project['roots']) > 1:
        if root not in project['roots']:
            raise RequestError('needs_root', '%s supports one root; pass root, one of the project roots' % agent)
        project['roots'] = [root]
    task = params.get('task')
    if mode == 'task' and not task:
        raise RequestError('bad_request', 'task mode needs task')
    if params.get('name'):
        project['session_title'] = str(params['name'])
    elif task:
        project['session_title'] = task
    args = argparse.Namespace(command=mode, session=session if mode == 'resume' else None, prompt=params.get('prompt'),
                              task=task if mode == 'task' else None, description='', alias=[], pr=None, branch=None,
                              repo=[], cwd=None, in_place=False, destination=None, base='HEAD', dry_run=ag.dry)
    if mode == 'task':
        project = task_project(project, args, ag.dry)
    record = launch_session(ag, window, project, agent, args)
    if not isinstance(record, dict):
        return {'dry_run': True} if ag.dry else {}
    return {'record': record['id'], 'session': record.get('terminal_id'), 'conversation_id': record.get('conversation_id')}


def _ctl(argv, stdin=None):
    """agtermctl with text on stdin, so leading dashes in text are never parsed as options."""
    command = [shutil.which('agtermctl') or 'agtermctl', *argv]
    sock = os.environ.get('AGTERM_SOCKET') or os.environ.get('AGT_SOCKET')
    if sock:
        command += ['--socket', sock]
    done = subprocess.run(command, input=stdin, text=True, capture_output=True, timeout=15)
    if done.returncode:
        raise RequestError('failed', (done.stderr or done.stdout).strip()[:300] or 'agtermctl failed')


def rename(ag, params):
    from .names import clean
    name = clean(str(params.get('name') or ''))
    if not name:
        raise RequestError('bad_request', 'name is required')
    window, row = _find(ag, params.get('session'))
    ag.call('session', 'rename', name, '--target', row['id'], '--window', window)
    return {'session': row['id'], 'name': name}


def type_text(ag, params):
    text = params.get('text')
    if not isinstance(text, str) or '\x00' in text or len(text) > MAX_TEXT:
        raise RequestError('bad_request', 'text must be a string without NUL, at most %d characters' % MAX_TEXT)
    pane = params.get('pane', 'left')
    if pane not in PANES:
        raise RequestError('bad_request', 'pane must be left or right')
    if params.get('enter'):
        text += '\r'
    window, row = _find(ag, params.get('session'))
    _ctl(['session', 'type', '--stdin', '--target', row['id'], '--window', window, '--pane', pane], text)
    return {'session': row['id'], 'typed': len(text)}


def screen(ag, params):
    pane = params.get('pane', 'left')
    lines = max(1, min(int(params.get('lines', 80)), 2000))
    session_id = params.get('session')
    row = next((s for s in inventory(ag).get('sessions', []) if s.get('id') == session_id), None)
    if row is None:
        raise RequestError('gone', 'Session has no live zmx pane')
    daemon = next((p.get('daemon') for p in row.get('panes', []) if p.get('pane') == pane), None)
    if not daemon:
        raise RequestError('bad_request', 'Session has no %s pane' % pane)
    reply = ag.call('zmx', 'screen', daemon, '--lines', str(lines), json_result=True) or {}
    return {'session': session_id, 'pane': pane, 'text': reply.get('text', '')}


def hello(ag):
    version = ag.call('version', json_result=True) or {}
    return {'protocol': PROTOCOL, 'host': socket.gethostname(),
            'agterm': (version.get('app') or {}).get('version'),
            'methods': sorted(METHODS) + ['subscribe', 'unsubscribe']}


METHODS = {
    'hello': lambda s, p: hello(s.ag),
    'appearance': lambda s, p: __import__('agent_utils.agterm.mobile_pairing', fromlist=['appearance']).appearance(s.ag),
    'tree': lambda s, p: build_tree(s.ag),
    'projects': lambda s, p: list_projects(s.data()),
    'history': lambda s, p: history(s.data(), p),
    'launch': lambda s, p: launch(s.launch_ag, s.data(), p),
    'rename': lambda s, p: rename(s.ag, p),
    'type': lambda s, p: type_text(s.ag, p),
    'screen': lambda s, p: screen(s.ag, p),
}


class Server:
    def __init__(self, ag, config, out=None, interval=2.0, launch_ag=None):
        self.ag, self.config, self.out = ag, config, out or sys.stdout
        self.launch_ag = launch_ag or ag
        self.lock = threading.Lock()
        self.interval = interval
        self.stop = threading.Event()
        self.poller = None

    def data(self):
        return _config(self.config)

    def send(self, obj):
        with self.lock:
            self.out.write(json.dumps(obj, ensure_ascii=False, separators=(',', ':')) + '\n')
            self.out.flush()

    def poll(self):
        # Poll fallback; the agterm events cursor (`agtermctl events --run --after`) is a later step.
        digest = None
        while not self.stop.is_set():
            try:
                with redirect_stdout(sys.stderr):
                    tree = build_tree(self.ag)
                current = hashlib.sha256(json.dumps(tree, sort_keys=True).encode()).hexdigest()
                if current != digest:
                    digest = current
                    self.send({'event': 'tree.changed', 'result': tree})
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                self.send({'event': 'error', 'error': str(exc)[:300]})
            self.stop.wait(self.interval)

    def handle(self, line):
        if len(line) > MAX_LINE:
            return {'id': None, 'ok': False, 'code': 'bad_request', 'error': 'Line too long'}
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            return {'id': None, 'ok': False, 'code': 'bad_request', 'error': 'Not JSON'}
        if not isinstance(request, dict):
            return {'id': None, 'ok': False, 'code': 'bad_request', 'error': 'Request must be an object'}
        rid, method, params = request.get('id'), request.get('method'), request.get('params', {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return {'id': rid, 'ok': False, 'code': 'bad_request', 'error': 'params must be an object'}
        if method == 'subscribe':
            self.interval = max(0.5, min(float(params.get('interval', self.interval)), 60))
            if self.poller is None or not self.poller.is_alive():
                self.stop.clear()
                self.poller = threading.Thread(target=self.poll, daemon=True)
                self.poller.start()
            return {'id': rid, 'ok': True, 'result': {'interval': self.interval}}
        if method == 'unsubscribe':
            self.stop.set()
            return {'id': rid, 'ok': True, 'result': {}}
        handler = METHODS.get(method)
        if handler is None:
            return {'id': rid, 'ok': False, 'code': 'unknown_method', 'error': 'Unknown method: %s' % method}
        try:
            # Launch and history code print progress; keep it off the protocol stream.
            with redirect_stdout(sys.stderr):
                result = handler(self, params)
            return {'id': rid, 'ok': True, 'result': result}
        except RequestError as exc:
            return {'id': rid, 'ok': False, 'code': exc.code, 'error': str(exc)}
        except (ValueError, TypeError, OSError, subprocess.SubprocessError) as exc:
            return {'id': rid, 'ok': False, 'code': 'failed', 'error': str(exc)[:300]}

    def run(self, lines):
        for line in lines:
            if line.strip():
                self.send(self.handle(line))
        self.stop.set()
        return 0


def attach(ag, session_id, pane_name):
    if not os.isatty(0) or not os.isatty(1):
        raise ValueError('mobile attach needs a PTY; the app opens one on its SSH channel')
    original = inventory(ag)
    row = next((s for s in original.get('sessions', []) if s.get('id') == session_id), None)
    if row is None:
        raise ValueError('Session is absent or has no live daemon; nothing attached')
    pane = next((p for p in row.get('panes', []) if p.get('pane') == pane_name), None)
    if pane is None:
        raise ValueError('Session does not have that pane')
    return attach_pane(ag, original, session_id, pane, notice=False)


def command(argv):
    from ..core.paths import config_path
    parser = argparse.ArgumentParser(prog='agents mobile', description='Mac side of the agterm mobile app (JSON lines over SSH)')
    parser.add_argument('action', choices=['serve', 'attach', 'pair', 'authorize', 'gate'])
    parser.add_argument('--config', default=str(config_path()))
    parser.add_argument('--session', help='attach: session id from the tree')
    parser.add_argument('--pane', choices=PANES, default='left')
    parser.add_argument('--interval', type=float, default=2.0, help='serve: seconds between tree polls after subscribe')
    parser.add_argument('--dry-run', action='store_true', help='serve: launches print the agtermctl commands instead')
    parser.add_argument('--host', help='pair: reachable hostname or Tailscale name')
    parser.add_argument('--port', type=int, default=22, help='pair: SSH port')
    parser.add_argument('--user', help='pair: SSH username')
    parser.add_argument('--host-key', help='pair: SSH host public key file')
    parser.add_argument('--key', help='authorize: OpenSSH public key text')
    parser.add_argument('--yes', action='store_true', help='authorize: write after preview')
    access = parser.add_mutually_exclusive_group()
    access.add_argument('--restricted', action='store_true', help='authorize: restricted access (default)')
    access.add_argument('--full', action='store_true', help='authorize: allow a plain shell too')
    args = parser.parse_args(argv)
    if args.action in ('pair', 'authorize', 'gate'):
        from . import mobile_pairing
        if args.action == 'pair':
            link, pin = mobile_pairing.pairing(args.host, args.port, args.user, args.host_key)
            print(link); print('Host key: ' + pin)
            if shutil.which('qrencode'):
                subprocess.run(['qrencode', '-t', 'ANSIUTF8', link], check=True)
            return 0
        if args.action == 'authorize':
            if not args.key: parser.error('authorize needs --key')
            mobile_pairing.authorize(args.key, args.yes, not args.full)
            return 0
        return mobile_pairing.gate()
    if args.action == 'attach':
        if not args.session:
            parser.error('attach needs --session')
        return attach(_ag(), args.session, args.pane)
    out = sys.stdout
    position = _config(args.config).get('new_session_position', 'default')
    with redirect_stdout(sys.stderr):
        server = Server(_ag(False, position), args.config, out=out, interval=args.interval,
                        launch_ag=_ag(args.dry_run, position))
        return server.run(sys.stdin)
