"""Deliver Plannotator Inbox replies to Kiro CLI sessions.

Kiro is a plain MCP client of the Inbox, so a reply waits until the agent reads the
thread. Two cooperating parts:

* connect: `learn()` backs the hook action `inbox-learn` (Post Tool Use). When a session
  sends a message through the Inbox (MCP tool or `plannotator inbox call`), the thread
  is connected to that Kiro session. Only connected sessions hear about its replies.
* pull: `notice()` backs the Kiro hook action `inbox-check` (Prompt Submit and Stop). It
  prints one pointer line per unread person reply in a connected thread and never prints
  reply text.
* wake: `relay()` backs `agents admin inbox relay`. It types the same one-line pointer into the
  agterm pane of an idle connected Kiro session. Off by default: it runs only when started.

Both read replies with `plannotator inbox call read_thread` (supported API). State lives
in the external state dir: a registry of live Kiro sessions (written by the SessionStart
and SessionEnd hook actions), per-session notice counts and delivered reply ids.
Everything fails quiet: no Plannotator, a stopped Inbox or a bad answer yields no output.
"""
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..core.files import atomic_write
from ..core.paths import state_dir

MAX_NOTICES = 3          # a reply is announced at most this often per session
KEEP_DAYS = 60           # connections older than this are dropped
PROMPT_RE = re.compile(r'^›\s+ask a question or describe a task\b', re.I)
SAFE_ID = re.compile(r'^[A-Za-z0-9_.-]{1,80}$')


def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def parse_time(text):
    try:
        return datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    except ValueError:
        return None


# ---------------------------------------------------------------- state

def state_path():
    return state_dir()/'inbox-relay.json'


@contextmanager
def locked():
    """Exclusive lock on the state file; yields a mutable dict that is saved on exit."""
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + '.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        for key in ('sessions', 'notified', 'delivered', 'threads'):
            if not isinstance(data.get(key), dict):
                data[key] = {}
        before = json.dumps(data, sort_keys=True)
        yield data
        if json.dumps(data, sort_keys=True) != before:
            atomic_write(path, data)


def register(session_id, cwd, env=None):
    """SessionStart: record where a Kiro session lives. Re-registering keeps `started`."""
    env = os.environ if env is None else env
    if not (session_id and SAFE_ID.match(session_id)):
        return
    with locked() as data:
        old = data['sessions'].get(session_id, {})
        data['sessions'][session_id] = {
            'cwd': cwd, 'started': old.get('started') or now_iso(),
            'agterm_session': env.get('AGTERM_SESSION_ID'), 'pane_id': env.get('AGTERM_PANE_ID'),
            'socket': env.get('AGTERM_SOCKET')}


def unregister(session_id):
    with locked() as data:
        data['sessions'].pop(session_id, None)
        data['notified'].pop(session_id, None)


# ---------------------------------------------------------------- Inbox

def plannotator():
    return shutil.which('plannotator')


def inbox_running(binary, timeout=3):
    try:
        out = subprocess.run([binary, 'inbox', 'status'], capture_output=True, text=True, timeout=timeout,
                             stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return ': running' in out.splitlines()[0] if out.strip() else False


def unread_replies(cwd, timeout=5):
    """Unread threads of the project at cwd whose last message is the person's.

    Returns [{'thread_id','reply_id','at'}]. Never starts a stopped Inbox.
    """
    binary = plannotator()
    if not binary or not cwd or not inbox_running(binary):
        return []
    request = json.dumps({'asked_by': 'anyone', 'project_path': cwd, 'limit': 100})
    try:
        out = subprocess.run([binary, 'inbox', 'call', 'read_thread', request], capture_output=True, text=True,
                             timeout=timeout, stdin=subprocess.DEVNULL)
        listing = json.loads(out.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    result = []
    for item in listing.get('threads', []) if isinstance(listing, dict) else []:
        if not isinstance(item, dict) or not item.get('unread') or item.get('last_author') != 'person':
            continue
        thread, reply = item.get('thread_id'), item.get('last_person_message_id')
        if isinstance(thread, str) and isinstance(reply, str) and SAFE_ID.match(thread) and SAFE_ID.match(reply):
            result.append({'thread_id': thread, 'reply_id': reply, 'at': item.get('last_person_at')})
    return result


def newer(reply, since):
    """True when the reply arrived at or after SINCE (the connection time)."""
    a, b = parse_time(reply.get('at')), parse_time(since)
    return bool(a and b and a >= b)


# ---------------------------------------------------------------- connections

MCP_SEND = re.compile(r'(^|[_/@])plannotator[_-]inbox[_/]send_message$')
MCP_THREAD = re.compile(r'\(thread (msg_[A-Za-z0-9]+)')
JSON_THREAD = re.compile(r'"thread_id"\s*:\s*"(msg_[A-Za-z0-9]+)"')
SHELL_SEND = re.compile(r'\binbox\s+call\b[^\n|;&]*\bsend_message\b')


def sent_threads(payload):
    """Thread ids a Post Tool Use payload shows this session sending into."""
    if not isinstance(payload, dict):
        return []
    name, args, response = payload.get('tool_name'), payload.get('tool_input'), payload.get('tool_response')
    text = response if isinstance(response, str) else json.dumps(response) if response is not None else ''
    if isinstance(name, str) and MCP_SEND.search(name):
        found = MCP_THREAD.findall(text) + JSON_THREAD.findall(text)
    elif isinstance(args, dict) and isinstance(args.get('command'), str) and SHELL_SEND.search(args['command']):
        found = JSON_THREAD.findall(text)
    else:
        return []
    return [t for t in dict.fromkeys(found) if SAFE_ID.match(t)]


def connect(session_id, thread_id, at=None):
    if not (session_id and SAFE_ID.match(session_id) and thread_id and SAFE_ID.match(thread_id)):
        return
    with locked() as data:
        data['threads'].setdefault(thread_id, {})[session_id] = at or now_iso()
        prune(data)


def prune(data, days=KEEP_DAYS):
    now = datetime.now(timezone.utc)
    for tid in list(data['threads']):
        sessions = data['threads'][tid]
        for sid in list(sessions):
            at = parse_time(sessions[sid])
            if not at or (now - at).days > days:
                sessions.pop(sid)
        if not sessions:
            data['threads'].pop(tid)


def learn(session_id, payload):
    """Post Tool Use: connect every thread this session just sent into."""
    for tid in sent_threads(payload):
        connect(session_id, tid)


def pointer(reply):
    """The one line given to an agent. Ids only: thread titles and reply text are never echoed."""
    return ('Plannotator Inbox: the person replied in thread %s (message %s). '
            'Read it with read_thread.' % (reply['thread_id'], reply['reply_id']))


# ---------------------------------------------------------------- pull (hook)

def notice(session_id, cwd):
    """Pointer lines for replies this session has not been told about enough times."""
    if not (session_id and SAFE_ID.match(session_id)):
        return []
    replies = unread_replies(cwd)
    if not replies:
        return []
    lines = []
    with locked() as data:
        counts = data['notified'].setdefault(session_id, {})
        for reply in replies:
            since = data['threads'].get(reply['thread_id'], {}).get(session_id)
            if not since or not newer(reply, since) or reply['reply_id'] in data['delivered']:
                continue
            if counts.get(reply['reply_id'], 0) >= MAX_NOTICES:
                continue
            counts[reply['reply_id']] = counts.get(reply['reply_id'], 0) + 1
            lines.append(pointer(reply))
    return lines


# ---------------------------------------------------------------- wake (relay)

def lease_pid(session_id, home=None):
    """pid of the kiro process that owns the session, from its lease file."""
    path = Path(home or Path.home())/'.kiro/session-leases'/(session_id + '.lease')
    try:
        pid = json.loads(path.read_text()).get('parentPid')
    except (OSError, ValueError):
        return None
    if not isinstance(pid, int):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    return pid


def marker_dir():
    return Path(os.environ.get('KIRO_TURN_MARKER_DIR')
                or Path.home()/'Library/Application Support/kiro-cli/run/turn-markers')


def turn_running(pid, directory=None):
    """A turn marker file `<pid>-<ms>.json` exists only while that kiro process runs a turn."""
    directory = Path(directory or marker_dir())
    try:
        return any(name.startswith('%d-' % pid) for name in os.listdir(directory))
    except OSError:
        return True      # unknown: behave as busy


def target_args(session):
    return ['--target', session['agterm_session']] + \
        (['--pane-id', session['pane_id']] if session.get('pane_id') else []) + \
        (['--socket', session['socket']] if session.get('socket') else [])


def prompt_is_empty(session, ctl, timeout=5):
    """True when the pane's last non-blank line is Kiro's empty prompt placeholder."""
    tail = target_args(session)
    try:
        out = subprocess.run([ctl, 'session', 'text', '--lines', '12'] + tail, capture_output=True, text=True,
                             timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    return out.returncode == 0 and bool(lines) and bool(PROMPT_RE.match(lines[-1]))


def type_line(session, ctl, text, timeout=5):
    tail = target_args(session)
    result = subprocess.run([ctl, 'session', 'type', '--stdin'] + tail, input=text + '\r', capture_output=True,
                            text=True, timeout=timeout)
    return result.returncode == 0


def tick(ctl, idle_since, idle_seconds, dry_run=False, log=None, clock=time.monotonic, home=None):
    """One relay pass. Returns the reply ids typed (or that would be typed)."""
    log = log or (lambda message: None)
    with locked() as data:
        sessions = {sid: dict(info) for sid, info in data['sessions'].items()}
        delivered = set(data['delivered'])
        threads = {tid: dict(conn) for tid, conn in data['threads'].items()}
    live = {}
    for sid, info in sessions.items():
        pid = lease_pid(sid, home)
        if pid and info.get('agterm_session'):
            live[sid] = dict(info, pid=pid)
    for sid in list(idle_since):
        if sid not in live or turn_running(live[sid]['pid']):
            idle_since.pop(sid, None)
    for sid, info in live.items():
        if not turn_running(info['pid']):
            idle_since.setdefault(sid, clock())
    done = []
    for cwd in sorted({info['cwd'] for info in live.values()}):
        for reply in unread_replies(cwd):
            if reply['reply_id'] in delivered:
                continue
            conn = threads.get(reply['thread_id'], {})
            candidates = sorted((at, sid) for sid, at in conn.items() if sid in live and newer(reply, at))
            if not candidates:
                continue
            sid = candidates[-1][1]
            info = live[sid]
            if sid not in idle_since or clock() - idle_since[sid] < idle_seconds:
                log('wait %s: session busy or not idle for %ss' % (reply['reply_id'], idle_seconds))
                continue
            if not prompt_is_empty(info, ctl):
                log('wait %s: prompt is not the empty placeholder' % reply['reply_id'])
                continue
            line = pointer(reply)
            if dry_run:
                log('would type into %s: %s' % (info['agterm_session'], line))
                done.append(reply['reply_id'])
                continue
            with locked() as data:           # at most once per reply, recorded before typing
                data['delivered'][reply['reply_id']] = now_iso()
                delivered.add(reply['reply_id'])
            try:
                ok = type_line(info, ctl, line)
            except (OSError, subprocess.SubprocessError):
                ok = False
            log(('typed into %s: %s' if ok else 'failed typing into %s: %s') % (info['agterm_session'], line))
            done.append(reply['reply_id'])
    return done


def relay(args):
    """`agents admin inbox relay [--once] [--interval N] [--idle-seconds N]`."""
    ctl = os.environ.get('AGTERMCTL') or shutil.which('agtermctl')
    if not ctl:
        print('agtermctl not found', file=sys.stderr)
        return 1
    if not plannotator():
        print('plannotator not found', file=sys.stderr)
        return 1

    def log(message):
        print('%s %s' % (now_iso(), message), flush=True)

    idle_since = {}
    interval = max(2.0, float(getattr(args, 'interval', None) or 8))
    idle_seconds = float(getattr(args, 'idle_seconds', None) if getattr(args, 'idle_seconds', None) is not None else 10)
    once = getattr(args, 'once', False)
    if once:
        # a single pass cannot observe idle time; trust the absent turn marker
        idle_seconds = 0
    try:
        while True:
            tick(ctl, idle_since, idle_seconds, dry_run=getattr(args, 'dry_run', False), log=log)
            if once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0
