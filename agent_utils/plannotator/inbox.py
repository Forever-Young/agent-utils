"""Plannotator Inbox: health check, thread listing and session dispatch.

Thread data is read from the Inbox's project files (written only by state.py), because the
supported API does not say which agent session a thread is attached to. The file
layout is undocumented (checked on Plannotator 0.29.0); unknown lines are skipped.
"""
from argparse import Namespace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

CHECKED_VERSIONS = ('0.29.0',)


def plannotator():
    return shutil.which('plannotator') or 'plannotator'


def data_dir():
    """The data directory Plannotator itself reports, with its documented fallbacks."""
    if os.environ.get('PLANNOTATOR_DATA_DIR'):
        return Path(os.environ['PLANNOTATOR_DATA_DIR']).expanduser()
    try:
        out = subprocess.run([plannotator(), 'data-dir'], capture_output=True, text=True, timeout=10).stdout.strip()
        if out:
            return Path(out)
    except (OSError, subprocess.SubprocessError):
        pass
    legacy = Path.home()/'.plannotator'
    if legacy.exists() or not os.environ.get('XDG_DATA_HOME'):
        return legacy
    return Path(os.environ['XDG_DATA_HOME'])/'plannotator'


def _records(path):
    """Last record per id from an append-only JSONL file."""
    latest = {}
    if not path.exists():
        return latest
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                item = json.loads(line)
                latest[item['id']] = item['record']
            except (ValueError, KeyError, TypeError):
                continue
    return latest


def projects(base=None):
    """Inbox projects: [{'id','key','name','root','dir'}]."""
    base = Path(base or data_dir())/'inbox'/'projects'
    result = []
    for folder in sorted(base.iterdir()) if base.is_dir() else []:
        for record in _records(folder/'project.json').values():
            if record.get('root'):
                result.append({'id': record.get('id'), 'key': record.get('key', folder.name),
                               'name': record.get('name', folder.name), 'root': record['root'], 'dir': folder})
    return result


def threads(project):
    """Threads of one Inbox project with their attached agent sessions."""
    grouped = {}
    for message in _records(Path(project['dir'])/'messages.jsonl').values():
        if message.get('thread_id'):
            grouped.setdefault(message['thread_id'], []).append(message)
    result = []
    for thread_id, messages in grouped.items():
        messages.sort(key=lambda m: m.get('created_at') or '')
        root = next((m for m in messages if m.get('id') == thread_id), messages[0])
        sessions = []
        for message in messages:
            author = message.get('author') or {}
            if author.get('kind') == 'agent' and author.get('session'):
                pair = {'host': author.get('host'), 'session': author['session']}
                if pair not in sessions:
                    sessions.append(pair)
        last = messages[-1]
        result.append({
            'thread_id': thread_id, 'subject': root.get('subject') or '', 'thread_name': root.get('thread_name'),
            'project': project['name'], 'root': project['root'], 'resolved': bool(root.get('resolved_at')),
            'sessions': sessions, 'last_author': (last.get('author') or {}).get('kind'),
            'last_message_id': last.get('id'), 'last_at': last.get('created_at'), 'messages': len(messages)})
    result.sort(key=lambda t: t['last_at'] or '', reverse=True)
    return result


def all_threads(base=None):
    return [t for p in projects(base) for t in threads(p)]


def _resolve(path):
    return Path(path).expanduser().resolve()


def match_project(root, pairs):
    """Best configured (window, project) for an Inbox project root, or None.

    An exact root wins over a parent folder; the first root over later ones; a
    project with fewer roots over a broader one; then config order.
    """
    target = _resolve(root)
    best = None
    for order, (window, project) in enumerate(pairs):
        for index, configured in enumerate(project.get('roots', [])):
            path = _resolve(configured)
            if path == target:
                rank = 0 if index == 0 else 1
            elif path in target.parents:
                rank = 2
            else:
                continue
            key = (rank, len(project['roots']), order)
            if best is None or key < best[0]:
                best = (key, window, project)
    return (best[1], best[2]) if best else None


PROMPT = '''Plannotator Inbox thread {thread_id} ("{subject}") has no agent session yet. Adopt it and work on it in this session:
1. Read it with your Plannotator Inbox tool: read_thread with thread_id {thread_id}.
2. Reply with send_message, reply_to its latest message, from that tool (not the `plannotator inbox call` shell command, which sends without a session), so the person's next replies come to this session. Say briefly how you will handle it.
3. Then do the work the thread asks for, following this project's instructions.'''

# Hosts whose MCP shim names only the process: pass a stable identity so the
# thread stays attached after the conversation is resumed.
IDENTITY = {'kiro-cli': ('kiro', 'Kiro', 'kiro-$KIRO_SESSION_ID')}


def dispatch_prompt(thread, agent=None):
    text = PROMPT.format(thread_id=thread['thread_id'], subject=thread['subject'].replace('"', "'"))
    if agent in IDENTITY:
        host, name, session = IDENTITY[agent]
        text += ('\nOn every Inbox send_message and read_thread call pass agent_host "%s", agent_name "%s" and '
                 'agent_session "%s" (expand the variable with a shell echo first).') % (host, name, session)
    return text


def select(found, ids=(), answered=False, everything=False, force=False):
    """Threads to dispatch and the ones skipped, with the reason."""
    chosen, skipped = [], []
    wanted = set(ids)
    for thread in found:
        if wanted and thread['thread_id'] not in wanted:
            continue
        reason = None
        if thread['resolved']:
            reason = 'resolved'
        elif thread['sessions'] and not force:
            reason = 'already attached to ' + ', '.join(s['session'] for s in thread['sessions'])
        elif not wanted and answered and thread['last_author'] != 'person':
            reason = 'not answered'
        elif not wanted and not answered and not everything:
            reason = 'not selected'
        (skipped if reason else chosen).append(dict(thread, reason=reason) if reason else thread)
    missing = wanted - {t['thread_id'] for t in found}
    skipped += [{'thread_id': i, 'reason': 'unknown thread'} for i in sorted(missing)]
    return chosen, skipped


def dispatch(args, ag, pairs, base=None, launcher=None):
    """Launch one session per selected thread in its matching workspace."""
    from ..agterm.state import locked_registry
    if launcher is None:
        from ..agterm.sessions import launch as launcher
    if not (args.thread or args.answered or args.all):
        raise ValueError('inbox dispatch needs --thread ID (repeatable), --answered or --all')
    chosen, skipped = select(all_threads(base), args.thread, args.answered, args.all, args.force)
    with locked_registry('inbox') as state:
        already = dict(state.setdefault('dispatched', {}))
    report = {'launched': [], 'planned': [], 'skipped': skipped}
    for thread in chosen:
        if thread['thread_id'] in already and not args.force:
            report['skipped'].append(dict(thread, reason='already dispatched to record ' + str(already[thread['thread_id']].get('record'))))
            continue
        match = match_project(thread['root'], pairs)
        if not match:
            report['skipped'].append(dict(thread, reason='no configured project for ' + thread['root']))
            continue
        window, project = match
        # Match against every project first, so a filter never sends a thread to a
        # broader project in another window.
        if (getattr(args, 'window', None) and window['name'] != args.window) or \
           (getattr(args, 'project', None) and project['id'] != args.project):
            report['skipped'].append(dict(thread, reason='belongs to %s / %s' % (window['name'], project['id'])))
            continue
        project = dict(project, roots=[str(_resolve(thread['root']))], session_title=thread['subject'][:80] or thread['thread_id'])
        agent = args.agent or project.get('default_agent', window['default_agent'])
        entry = {'thread_id': thread['thread_id'], 'subject': thread['subject'], 'window': window['name'],
                 'project': project['id'], 'cwd': project['roots'][0], 'agent': agent}
        if args.dry_run:
            report['planned'].append(entry)
            print('Would launch %s in %s / %s for %s' % (agent, window['name'], project['name'], thread['thread_id']))
            continue
        launch_args = Namespace(command='new', session=None, prompt=dispatch_prompt(thread, agent), cwd=project['roots'][0],
                                task=None, alias=[], pr=None, branch=None, repo=[])
        record = launcher(ag, window, project, agent, launch_args)
        entry['record'] = (record or {}).get('id')
        with locked_registry('inbox') as state:
            state.setdefault('dispatched', {})[thread['thread_id']] = {
                'record': entry['record'], 'agent': agent, 'at': datetime.now(timezone.utc).isoformat()}
        report['launched'].append(entry)
    print(json.dumps(report, indent=2, default=str))
    return report


# Plannotator 0.29.0 starts an Inbox launch in its own background process and offers
# "Open in Terminal" (always Terminal.app, not configurable). These are the commands
# it runs there, read from its source; only agents with a plain command are adopted.
def attach_argv(launch):
    """Command that attaches a terminal to an Inbox launch, or None with a reason."""
    agent, ref = launch.get('agent'), launch.get('session_ref')
    if not ref:
        return None, 'no session yet'
    if agent in ('claude-code', 'claude'):
        return ['claude', 'attach', ref], None
    if agent == 'codex':
        return ['codex', 'resume', ref], None
    return None, '%s launches are not adopted (Plannotator hands those over with its own process handling)' % agent


def launches(base=None):
    """Inbox launches (root messages with a `launch` record): [{id, thread_id, agent, folder, ...}]."""
    found = []
    for project in projects(base):
        for message in _records(Path(project['dir'])/'messages.jsonl').values():
            launch = message.get('launch')
            if not isinstance(launch, dict) or not launch.get('id'):
                continue
            found.append({'id': launch['id'], 'thread_id': message.get('thread_id') or message.get('id'),
                          'subject': message.get('subject') or '', 'agent': launch.get('agent'),
                          'folder': launch.get('folder') or project['root'], 'session_ref': launch.get('session_ref'),
                          'error': bool(launch.get('error')), 'deleted': bool(launch.get('deleted_at')),
                          'project_root': project['root']})
    return found


def list_launches(args, pairs=None, base=None):
    rows = []
    for launch in launches(base):
        match = match_project(launch['folder'], pairs) if pairs else None
        argv, why = attach_argv(launch)
        rows.append(dict(launch, window=match[0]['name'] if match else None, workspace=match[1]['id'] if match else None,
                         adoptable=bool(argv and match and not launch['deleted'] and not launch['error']), note=why))
    print(json.dumps(rows, indent=2))
    return rows


def adopt(args, ag, pairs, base=None):
    """Open a terminal row in the launch's agterm workspace that attaches to the launch."""
    ids = set(args.launch)
    if not ids:
        raise ValueError('inbox adopt needs --launch ID (repeatable); see `inbox launches`')
    report = {'adopted': [], 'skipped': []}
    known = {l['id']: l for l in launches(base)}
    for launch_id in sorted(ids):
        launch = known.get(launch_id)
        if not launch:
            report['skipped'].append({'id': launch_id, 'reason': 'unknown launch'}); continue
        argv, why = attach_argv(launch)
        match = match_project(launch['folder'], pairs)
        reason = why or ('deleted' if launch['deleted'] else 'start failed' if launch['error'] else
                         None if match else 'no configured project for ' + launch['folder'])
        if reason:
            report['skipped'].append({'id': launch_id, 'reason': reason}); continue
        window, project = match
        project = dict(project, session_title=launch['subject'][:80] or launch_id)
        cwd = str(_resolve(launch['folder']))
        if not ag.dry and not Path(cwd).is_dir():
            report['skipped'].append({'id': launch_id, 'reason': 'folder unavailable: ' + cwd}); continue
        terminal = ag.launch(window, project, argv, cwd)
        report['adopted'].append({'id': launch_id, 'window': window['name'], 'project': project['id'], 'argv': argv, 'terminal': terminal})
    print(json.dumps(report, indent=2, default=str))
    return report


def _status(timeout=10):
    proc = subprocess.run([plannotator(), 'inbox', 'status', '--json'], capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {'running': False, 'reason': (proc.stderr or proc.stdout).strip() or 'no status output'}


def _servers():
    """Inbox server processes (not the per-agent MCP stdio servers or CLI calls)."""
    try:
        out = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    result = []
    for line in out.splitlines():
        pid, _, command = line.strip().partition(' ')
        words = command.split()
        if len(words) < 2 or Path(words[0]).name != 'plannotator' or words[1] != 'inbox':
            continue
        rest = words[2:]
        if not rest or rest[0].startswith('--'):
            result.append(int(pid))
    return result


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def check(base=None, http_timeout=3, call_timeout=15):
    """One health report: {'healthy': bool, 'problems': [...], ...}."""
    problems, report = [], {}
    try:
        status = _status()
    except (OSError, subprocess.SubprocessError) as exc:
        status = {'running': False, 'reason': str(exc)}
    report['status'] = {k: status.get(k) for k in ('state', 'running', 'reason', 'url', 'pid', 'version', 'cli_version', 'started_at')}
    if not status.get('running'):
        problems.append('Inbox is not running: ' + str(status.get('reason') or status.get('state')))
        return dict(report, healthy=False, problems=problems)
    if not _alive(status.get('pid')):
        problems.append('Inbox record names pid %s, which is not running' % status.get('pid'))
    if status.get('version') and status.get('cli_version') and status['version'] != status['cli_version']:
        problems.append('Running Inbox %s differs from the installed CLI %s; restart it' % (status['version'], status['cli_version']))
    if status.get('version') and status['version'] not in CHECKED_VERSIONS:
        report['note'] = 'Thread files and the state edits (inbox rename/subject/reopen/author/withdraw/move) were checked on Plannotator %s only; on %s an edit still runs, then is checked through the Inbox and undone from its backup if it fails' % (', '.join(CHECKED_VERSIONS), status['version'])
    servers = _servers()
    report['servers'] = servers
    if servers is not None and len(servers) > 1:
        problems.append('Several Inbox servers run: pids ' + ', '.join(map(str, servers)))
    started = time.monotonic()
    try:
        with urllib.request.urlopen(status['url'], timeout=http_timeout) as response:
            report['http'] = {'status': response.status, 'seconds': round(time.monotonic()-started, 3)}
    except Exception as exc:  # timeouts, refused connections, HTTP errors
        problems.append('Inbox page did not answer within %ss: %s' % (http_timeout, exc))
    known = [p for p in projects(base) if Path(p['root']).is_dir()]
    if known:
        # A read from an existing project root; a call from another folder could add a project.
        started = time.monotonic()
        try:
            proc = subprocess.run([plannotator(), 'inbox', 'call', 'read_thread', '{"limit":1}'], cwd=known[0]['root'],
                                  capture_output=True, text=True, timeout=call_timeout)
            report['api'] = {'exit': proc.returncode, 'seconds': round(time.monotonic()-started, 3)}
            if proc.returncode:
                problems.append('Inbox API call failed: ' + (proc.stderr.strip() or 'exit %d' % proc.returncode))
        except subprocess.TimeoutExpired:
            problems.append('Inbox API call did not answer within %ss' % call_timeout)
        except OSError as exc:
            problems.append('Inbox API call could not run: %s' % exc)
    return dict(report, healthy=not problems, problems=problems)


def doctor(args, base=None):
    report = check(base)
    if report['healthy'] or not args.fix:
        print(json.dumps(report, indent=2))
        if not report['healthy']:
            print('Run with --fix --yes to restart the Inbox (connected agents reconnect within seconds).')
        return 0 if report['healthy'] else 1
    command = [plannotator(), 'inbox', 'restart']
    if args.dry_run or not args.yes:
        print(json.dumps(report, indent=2))
        print('Would run: ' + ' '.join(command) + ('' if args.dry_run else ' (add --yes to apply)'))
        return 1
    subprocess.run(command, timeout=60, check=False)
    time.sleep(2)
    after = check(base)
    print(json.dumps({'before': report, 'after': after}, indent=2))
    return 0 if after['healthy'] else 1


def list_threads(args, pairs=None, base=None):
    chosen, _ = select(all_threads(base), args.thread, args.answered, True, True)
    if not args.all:
        chosen = [t for t in chosen if not t['sessions']]
    rows = []
    for thread in chosen:
        row = {k: thread[k] for k in ('thread_id', 'subject', 'project', 'last_author', 'sessions', 'last_at')}
        match = match_project(thread['root'], pairs) if pairs else None
        row['window'], row['workspace'] = (match[0]['name'], match[1]['id']) if match else (None, None)
        if (getattr(args, 'window', None) and row['window'] != args.window) or \
           (getattr(args, 'project', None) and row['workspace'] != args.project):
            continue
        rows.append(row)
    print(json.dumps(rows, indent=2))
    return rows
