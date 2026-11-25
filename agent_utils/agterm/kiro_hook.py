"""Kiro CLI hook: mirror permission prompts and activity into the agterm session.

Also sends an ntfy event for attention/completed when `agents admin notify` is enabled.
Usage: kiro_hook.py attention|active|completed|session-start|session-end|inbox-learn|inbox-check.
The agterm actions are a silent no-op outside agterm. Only `inbox-check` prints: one
pointer line per unread Inbox reply (ids only, never reply text). Never raises, always
exits 0 (a PermissionRequest hook that prints nothing leaves Kiro's normal prompt in place).
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

STATES = {'attention': 'blocked', 'active': 'active', 'completed': 'completed'}


def run(args):
    ctl = os.environ.get('AGTERMCTL') or shutil.which('agtermctl')
    if not ctl:return
    sock = os.environ.get('AGTERM_SOCKET')
    base = [ctl] + args + ['--target', os.environ['AGTERM_SESSION_ID']]
    if sock:base += ['--socket', sock]
    try:subprocess.run(base + extra(args), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=5)
    except Exception:pass


def extra(args):
    if args[0] != 'session':return []
    out = []
    for flag, name in (('--pane', 'AGTERM_PANE'), ('--pane-id', 'AGTERM_PANE_ID')):
        if os.environ.get(name):out += [flag, os.environ[name]]
    return out


INBOX = ('session-start', 'session-end', 'inbox-learn', 'inbox-check')


def inbox(action, data):
    """Inbox pull and relay registry. Works outside agterm."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent_utils.plannotator import relay
    sid = (data.get('session_id') if isinstance(data, dict) else None) or os.environ.get('KIRO_SESSION_ID')
    cwd = (data.get('cwd') if isinstance(data, dict) else None) or os.getcwd()
    if action == 'session-start':relay.register(sid, cwd)
    elif action == 'session-end':relay.unregister(sid)
    elif action == 'inbox-learn':relay.learn(sid, data)
    else:
        lines = relay.notice(sid, cwd)
        if lines:print('\n'.join(lines))


def push(action, data):
    """Optional ntfy event. Disabled by default; works outside agterm."""
    if action not in ('attention', 'completed'):return
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent_utils.core import notify
    if not notify.load()['enabled']:return
    detail = ''
    if action == 'attention' and isinstance(data, dict) and isinstance(data.get('tool_name'), str):
        detail = data['tool_name'][:100]
    notify.send('kiro', notify.default_session(), 'input' if action == 'attention' else 'done', detail)


def zellij_status(action, data):
    """Tab status in Zellij (OSC 7501 on the pane's tty); a no-op outside Zellij."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from agent_utils.core import zellij
        if action == 'attention':
            tool = data.get('tool_name') if isinstance(data, dict) else None
            zellij.emit_status('blocked', 'permission', ('Kiro needs permission' + (': ' + tool if isinstance(tool, str) else '')), 'kiro')
        else:zellij.emit_status({'active': 'working', 'completed': 'done'}[action], app='kiro')
    except Exception:pass


def main(argv):
    action = argv[1] if len(argv) > 1 else ''
    try:data = json.loads(sys.stdin.read() or '{}')
    except Exception:data = {}
    if action in INBOX:return inbox(action, data)
    if action in STATES:
        try:push(action, data)
        except Exception:pass
    if action in STATES:zellij_status(action, data)
    if action not in STATES or not os.environ.get('AGTERM_SESSION_ID'):return
    if action == 'attention':
        run(['session', 'status', 'blocked', '--blink'])
        tool = data.get('tool_name') if isinstance(data, dict) else None
        body = 'Kiro needs permission' + (': ' + tool if isinstance(tool, str) and tool else '')
        run(['notify', body[:200], '--title', 'Kiro'])
    else:run(['session', 'status', STATES[action]])


if __name__ == '__main__':
    try:main(sys.argv)
    except BaseException:pass
    sys.exit(0)
