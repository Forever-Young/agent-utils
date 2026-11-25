"""Focused worker identity, live probes and serialized dispatch.

Provider conversations remain provider-owned. Only utility metadata and dispatch
messages are stored here; terminal status never proves task success.
"""
from contextlib import contextmanager, redirect_stdout
from argparse import Namespace
import fcntl
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import time
import uuid

from .state import state_dir, locked_registry


@contextmanager
def dispatch_lock():
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'dispatch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def path_key(path):
    return str(Path(path).expanduser().resolve())


def matches(record, args):
    if getattr(args, 'record', None) and record['id'] != args.record:return False
    if args.project and record['project'] != args.project:return False
    if args.window and record['window'] != args.window:return False
    if getattr(args, 'agent', None) and record.get('agent') != args.agent:return False
    if args.task and args.task not in [record.get('task'), *record.get('aliases', [])]:return False
    if args.conversation and record.get('conversation_id') != args.conversation:return False
    if getattr(args, 'pr', None) and args.pr.rstrip('/') not in record.get('prs', []):return False
    if getattr(args, 'branch', None) and args.branch not in record.get('branches', []):return False
    repos = {path_key(r) for r in record.get('repos', []) + record.get('roots', [])}
    if any(path_key(r) not in repos for r in args.repo):return False
    return True


def selected(items, args, include_hidden=False):
    rows = [r for r in items.values() if matches(r, args) and
            (include_hidden or args.all or not (r.get('hidden') or r.get('archived')))]
    return sorted(rows, key=lambda r: r.get('updated_at', ''), reverse=True)


def claude_inventory():
    executable = shutil.which('claude')
    if not executable:return None
    try:
        result = subprocess.run([executable, 'agents', '--json'], capture_output=True,
                                text=True, check=True, timeout=10)
        rows = json.loads(result.stdout)
        if not isinstance(rows, list) or any(not isinstance(r, dict) or not r.get('sessionId') for r in rows):return None
        return rows
    except (OSError, ValueError, subprocess.SubprocessError):return None


def process_birth(pid):
    try:
        return subprocess.check_output(['ps', '-p', str(int(pid)), '-o', 'lstart='],
                                       text=True, stderr=subprocess.DEVNULL, timeout=5).strip() or None
    except (OSError, ValueError, subprocess.SubprocessError):return None


def process_exists(pid):
    try:os.kill(int(pid), 0)
    except ProcessLookupError:return False
    except (PermissionError, ValueError, OSError):return None
    return True


def snapshot(ag, rows):
    from .sessions import live_rows
    if ag.dry:
        from ..cli import Agterm
        ag=Agterm()
    terminals = {}
    errors = []
    for window in sorted({r['window'] for r in rows}):
        try:terminals.update({r['id']: r for r in live_rows(ag, window)})
        except (OSError, ValueError, subprocess.SubprocessError):errors.append(window)
    inventory = claude_inventory() if any(r.get('agent') == 'claude' for r in rows) else None
    return terminals, errors, inventory


def observe(record, snap):
    """Return evidence, separate from the worker's explicit outcome."""
    terminals, errors, inventory = snap
    row = terminals.get(record.get('terminal_id'))
    provider = None
    if inventory is not None and record.get('conversation_id'):
        found = [r for r in inventory if r['sessionId'] == record['conversation_id']]
        if len(found) > 1:
            return dict(alive=None, state='unknown', terminal_present=bool(row), reason='multiple provider processes')
        provider = found[0] if found else None
    pid = record.get('provider_pid')
    birth = process_birth(pid) if pid else None
    tracked_alive = bool(birth and birth == record.get('provider_birth'))
    if provider:
        alive = True
        state = {'busy': 'running', 'idle': 'idle', 'waiting': 'blocked'}.get(provider.get('status'), 'running')
    elif tracked_alive:
        alive = True
        state = {'active': 'running', 'completed': 'idle', 'blocked': 'blocked'}.get((row or {}).get('status'), 'running')
    elif record.get('exited_at') or (pid and process_exists(pid) is False):
        alive = False;state = 'dead'
    elif record.get('agent') == 'claude' and inventory is not None and record.get('conversation_id'):
        alive = False;state = 'dead'
    else:
        alive = None;state = 'unknown'
    if alive and (row or {}).get('status')=='blocked':state='blocked'
    if record.get('status') in ('completed', 'failed') and alive is False:state = record['status']
    return dict(alive=alive, state=state, terminal_present=bool(row),
                terminal_window=(row or {}).get('window_id'),
                provider_pid=(provider or {}).get('pid') or (pid if tracked_alive else None),
                inventory_available=inventory is not None,
                terminal_inventory_available=record['window'] not in errors)


def compact(record, evidence=None):
    result = {k: record.get(k) for k in ('id', 'project', 'title', 'task', 'conversation_id', 'terminal_id', 'last_seen', 'checked_at')}
    result.update(aliases=record.get('aliases', []), prs=record.get('prs', []),
                  branches=record.get('branches', []), repos=record.get('repos', []),
                  status=record.get('status'), state=record.get('observed_state', 'unknown'))
    if evidence:result.update(evidence)
    return result


def refresh(rows, ag, persist=True):
    from .sessions import now
    snap = snapshot(ag, rows)
    output = []
    observations = {}
    for record in rows:
        evidence = observe(record, snap)
        changes = {'observed_state': evidence['state'], 'checked_at': now()}
        if evidence['alive']:changes['last_seen'] = now()
        observations[record['id']] = changes
        output.append((dict(record, **changes), evidence))
    if persist:
        with locked_registry() as registry:
            for key, changes in observations.items():
                if key in registry.get('sessions', {}):registry['sessions'][key].update(changes)
    return output


def collapse_observations(observed):
    """Keep distinct identities; fold only known duplicate conversations."""
    groups = {}
    for record, evidence in observed:
        key = (record.get('project'), record.get('agent'), record.get('conversation_id')) if record.get('conversation_id') else ('record', record['id'])
        groups.setdefault(key, []).append((record, evidence))
    result = []
    for duplicates in groups.values():
        duplicates.sort(key=lambda pair: bool(pair[1].get('alive') and pair[1].get('terminal_present')), reverse=True)
        record, evidence = duplicates[0]
        if len(duplicates) > 1:
            evidence = dict(evidence, duplicate_records=[r['id'] for r, e in duplicates[1:]])
        result.append((record, evidence))
    return result


def list_workers(args, ag):
    from .sessions import records
    focused = any((args.task, args.conversation, args.pr, args.branch, args.repo, args.record))
    rows = selected(records(), args, include_hidden=bool(focused))
    concise = args.compact or focused
    if args.live or args.refresh or focused:
        observed = refresh(rows, ag, not ag.dry)
        if concise:observed=collapse_observations(observed)
        if args.live:observed = [(r, e) for r, e in observed if e['alive'] is True]
        output = [compact(r, e) if concise else dict(r, **e) for r, e in observed]
    else:output = [compact(r) if concise else r for r in rows]
    print(json.dumps(output, separators=(',', ':') if concise else None,
                     indent=None if concise else 2))


def link_task(args, ag):
    from .sessions import now
    with dispatch_lock(), locked_registry() as registry:
        selectors = Namespace(**vars(args))
        selectors.pr=None;selectors.branch=None;selectors.repo=[]
        if args.record:selectors.task=None
        rows = selected(registry.setdefault('sessions', {}), selectors, True)
        # Link flags add metadata; they are not filters for this operation.
        if len(rows) != 1:raise ValueError('Task binding requires one matching worker; use --record to disambiguate')
        record = dict(rows[0])
        if args.operation=='bind' and args.record and args.task:
            if record.get('task') and record['task']!=args.task:
                record['aliases']=sorted(set(record.get('aliases',[])+[args.task]))
            else:record['task']=args.task
        if args.operation == 'rename':
            if not args.to_task:raise ValueError('task rename requires --to-task')
            aliases = record.get('aliases', []) + ([record['task']] if record.get('task') else [])
            record['task'] = args.to_task
            record['aliases'] = sorted(set(aliases) - {args.to_task})
        for attr, field, values in [('alias', 'aliases', args.alias), ('pr', 'prs', [args.pr] if args.pr else []),
                                    ('branch', 'branches', [args.branch] if args.branch else []), ('repo', 'repos', args.repo)]:
            normalized = [v.rstrip('/') if attr == 'pr' else path_key(v) if attr == 'repo' else v for v in values]
            record[field] = sorted(set(record.get(field, []) + normalized))
        identities = {record.get('task'), *record.get('aliases', [])} - {None}
        for other in registry['sessions'].values():
            if other['id'] == record['id'] or other.get('project') != record['project']:continue
            if identities.intersection({other.get('task'), *other.get('aliases', [])} - {None}):
                if other.get('conversation_id') != record.get('conversation_id') or not record.get('conversation_id'):
                    raise ValueError('Task identity already belongs to another worker')
        record['updated_at'] = now()
        if not ag.dry:registry['sessions'][record['id']] = record
        print(json.dumps(compact(record), separators=(',', ':')))


def unique_worker(rows):
    # Collapse only proven same-conversation duplicates in the query view.
    groups = {}
    for record in rows:
        identity = (record.get('agent'), record.get('conversation_id')) if record.get('conversation_id') else ('record', record['id'])
        groups.setdefault(identity, record)
    if len(groups) > 1:raise ValueError('Multiple workers match; use --record or bind task aliases to the intended worker')
    return next(iter(groups.values()), None)


def deliver(ag, record, prompt, evidence):
    """Submit one bounded line to a verified agent TUI through agtermctl; preserve the full text."""
    agent = record.get('agent')
    if agent not in ('claude', 'codex', 'kiro-cli'):raise ValueError('Live follow-up delivery supports Claude, Codex and Kiro workers')
    if not evidence['alive'] or not evidence['terminal_present']:
        raise ValueError('Worker process is not attached to its recorded terminal; reattach it before delivery')
    if evidence['state'] == 'blocked':raise ValueError('Worker is blocked; resolve its permission/input request before sending a follow-up')
    from .sessions import live_rows
    live = next((r for r in live_rows(ag, record['window']) if r['id'] == record.get('terminal_id')), None)
    if not live or live.get('remoteHost') or not live.get('foreground'):
        raise ValueError('Cannot verify the worker owns the terminal foreground; refusing to type')
    foreground = live['foreground']
    argv = foreground if isinstance(foreground, list) else shlex.split(foreground)
    managed = any(Path(a).name == 'runner.py' for a in argv) and record['id'] in argv
    names = {'claude': ('claude', 'claude.exe'), 'codex': ('codex',), 'kiro-cli': ('kiro-cli', 'kiro-cli-chat')}[agent]
    if agent == 'claude':
        if not managed and (not argv or record.get('conversation_id') not in argv or
                            not any(Path(a).name in names for a in argv[:2])):
            raise ValueError('Terminal foreground is not Claude; refusing to type')
        inventory = claude_inventory()
        provider = [r for r in inventory or [] if r['sessionId'] == record.get('conversation_id')]
        if live.get('status')=='blocked' or (provider and provider[0].get('status')=='waiting'):
            raise ValueError('Worker is blocked; resolve its input request first')
        if len(provider) != 1 or provider[0].get('pid') != evidence['provider_pid']:
            raise ValueError('Worker identity changed or is unavailable; retry lookup')
    else:
        # No provider inventory exists for these agents: rely on the runner's tracked
        # process (alive above) and on the foreground being that agent or its runner.
        if not managed and not any(Path(a).name in names for a in argv[:3]):
            raise ValueError('Terminal foreground is not %s; refusing to type' % agent)
        if live.get('status') == 'blocked':raise ValueError('Worker is blocked; resolve its input request first')
    message_id = str(uuid.uuid4())
    directory = state_dir() / 'messages' / record['id']
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (message_id + '.json')
    from ..cli import atomic_write
    atomic_write(path, {'id': message_id, 'record': record['id'], 'prompt': prompt})
    text = 'Coordinator follow-up: read the prompt field in ' + str(path) + ' and handle that request.'
    if any(c in text for c in '\r\n\x1b') or len(text.encode()) > 900:
        raise ValueError('Message path cannot be submitted as one bounded terminal line')
    ag.call('session', 'type', text + '\n', '--target', record['terminal_id'],
            '--window', live['window_id'])
    return message_id


def ensure(args, ag, window, project, agent):
    from .sessions import records, launch_unlocked, save, now
    from ..cli import agent_argv
    if not any((args.task, args.pr, args.branch, args.record, args.conversation)):
        raise ValueError('ensure requires --task, --pr, --branch, --record or --conversation')
    if not args.prompt:raise ValueError('ensure requires --prompt TEXT')
    if args.session:raise ValueError('ensure uses --conversation or --record, not --session')
    with dispatch_lock():
        # Metadata passed to create is also the exact identity to match.
        all_records = records()
        rows = []
        for candidate in all_records.values():
            if candidate.get('project') != project['id']:continue
            if args.record:
                identified = candidate['id'] == args.record
            else:
                identified = (args.task and args.task in [candidate.get('task'), *candidate.get('aliases', [])] or
                    args.pr and args.pr.rstrip('/') in candidate.get('prs', []) or
                    args.branch and args.branch in candidate.get('branches', []) or
                    args.conversation and args.conversation == candidate.get('conversation_id') or
                    set(args.alias).intersection([candidate.get('task'), *candidate.get('aliases', [])]))
            if identified:rows.append(candidate)
        rows.sort(key=lambda r: r.get('updated_at',''), reverse=True)
        record = unique_worker(rows)
        if args.conversation and not record:raise ValueError('Conversation is not registered; adopt or bind its exact worker first')
        if args.record and not record:raise ValueError('Worker record does not belong to the selected project')
        if record and args.conversation and record.get('conversation_id') != args.conversation:
            raise ValueError('Conversation identity conflicts with the selected worker')
        if record and record.get('agent') != agent:
            raise ValueError('This task already has a %s worker; finish or archive it before handing the task to %s, never run two workers for one task' % (record.get('agent'), agent))
        if record and agent not in ('claude', 'codex', 'kiro-cli'):
            raise ValueError('ensure follow-ups support Claude, Codex and Kiro workers; %s workers can only be created' % agent)
        if record:
            observed=collapse_observations(refresh(rows,ag,not ag.dry))
            record,evidence=observed[0]
        else:evidence=None
        if ag.dry:
            action = 'reused' if evidence and evidence['alive'] else 'resumed' if record else 'created'
            if record and (evidence['alive'] is None or record.get('provider_deleted') or
                           evidence['alive'] and not evidence['terminal_present'] or
                           not evidence['alive'] and (not record.get('conversation_id') or not evidence.get('inventory_available'))):action='blocked'
            print(json.dumps(dict(compact(record,evidence) if record else {}, action=action, dry_run=True), separators=(',', ':')))
            return
        if record:
            # Bind newly supplied aliases and links without replacing identity or
            # overwriting lifecycle writes from the running child.
            changes = {}
            for field, values in [('aliases', args.alias + ([args.task] if args.task and args.task != record.get('task') else [])),
                                  ('prs', [args.pr.rstrip('/')] if args.pr else []),
                                  ('branches', [args.branch] if args.branch else []),
                                  ('repos', [path_key(r) for r in args.repo])]:
                changes[field] = sorted(set(record.get(field, []) + values))
            new_names=set(changes['aliases']) - set(record.get('aliases',[])) - {record.get('task')}
            new_prs=set(changes['prs']) - set(record.get('prs',[]))
            for other in all_records.values():
                if other['id']==record['id'] or other.get('project')!=record['project']:continue
                if other.get('conversation_id')==record.get('conversation_id') and record.get('conversation_id'):continue
                if new_names.intersection([other.get('task'),*other.get('aliases',[])]) or new_prs.intersection(other.get('prs',[])):
                    raise ValueError('New task/PR identity already belongs to another worker')
            if record.get('provider_deleted'):raise ValueError('Provider conversation was deleted; select a different worker')
            if evidence['alive'] is None:raise ValueError('Worker liveness is unknown; refusing to resume or create a duplicate')
            if evidence['alive']:
                message_id = deliver(ag, record, args.prompt, evidence)
                with locked_registry() as registry:
                    registry['sessions'][record['id']].update(changes)
                    record = dict(registry['sessions'][record['id']])
                print(json.dumps(dict(compact(record, evidence), action='reused', delivery='submitted', message_id=message_id), separators=(',', ':')))
                return record
            if agent != 'claude':
                raise ValueError('%s worker is not running; resume it yourself (agents agterm resume) and repeat the follow-up' % agent)
            if not record.get('conversation_id'):raise ValueError('Existing worker has no bound conversation ID; bind it before reuse')
            if not evidence.get('inventory_available'):
                raise ValueError('Cannot exclude another live provider for this conversation; retry with provider inventory available')
            saved_project = {'id': record['project'], 'name': record['project_name'], 'roots': record['roots'],
                             'session_title': record['title'], 'agent_args': record.get('agent_args', {})}
            argv, cwd = agent_argv(saved_project, record['agent'], record['conversation_id'], args.prompt)
            for root in record['roots']:
                if not Path(root).expanduser().is_dir():raise ValueError('Saved root unavailable: ' + root)
            from .runner import wrapped
            # Clear only run evidence; never replace the conversation UUID.
            for key in ('provider_pid', 'provider_birth', 'exit_code', 'exited_at'):record.pop(key, None)
            record.update(status='launching', hidden=False, archived=False, updated_at=now())
            save(record)
            with redirect_stdout(sys.stderr):
                terminal = ag.launch({'name': record['window']}, saved_project, wrapped(record['id'], argv), record.get('cwd', cwd))
            with locked_registry() as registry:
                registry['sessions'][record['id']].update(terminal_id=terminal, hidden=False, archived=False, **changes)
                record = dict(registry['sessions'][record['id']])
            action = 'resumed'
        else:
            with redirect_stdout(sys.stderr):record = launch_unlocked(ag, window, project, agent, args)
            action = 'created'
        # The row can exist before the child starts. Bound the wait and report
        # unknown/launching honestly rather than declaring the worker healthy.
        for _ in range(10):
            current = records()[record['id']]
            if current.get('provider_pid') or current.get('exited_at'):break
            time.sleep(.1)
        record = records()[record['id']]
        evidence = refresh([record], ag)[0][1]
        print(json.dumps(dict(compact(record, evidence), action=action, delivery='launch_prompt'), separators=(',', ':')))
        return record
