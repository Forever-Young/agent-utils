"""Plannotator Inbox state edits that no tool or UI supports.

The Inbox keeps state in memory and never rereads its files, so an edit is only
safe with the server stopped. Layout checked on Plannotator 0.29.0 (undocumented);
each recipe below was tried on a copy of the data directory (a second Inbox with
PLANNOTATOR_DATA_DIR on the copy) before it was written down:

- title:    `thread_name` on the root message (and any reply carrying one).
- subject:  `subject` on the root message.
- reopen:   `resolved_at` on the root message set to null.
- author:   `author.name` / `author.host` of one agent message.
- withdraw: `superseded_by` on one question record (the Inbox then shows it as
            superseded, exactly as a supersedes reply does).
- move:     the thread's records in messages/questions/steps/annotations move to
            another project's files with their `project_id` changed.

Every recipe rewrites all copies of the affected records (files are append-only and
the last copy of an id wins), writes atomically, reads the result back and restarts
the Inbox in the background.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

from . import inbox

MAX_NAME = 80
MAX_SUBJECT = 200
RECIPES = ('title', 'subject', 'reopen', 'author', 'withdraw', 'move')
THREAD_FILES = ('messages', 'questions', 'steps', 'annotations')


def _dump(item):
    return json.dumps(item, ensure_ascii=False, separators=(', ', ': ')) + '\n'


def _read(path):
    path = Path(path)
    return path.read_text(encoding='utf-8').splitlines(keepends=True) if path.exists() else []


def _map(path, fn):
    """Text of a JSONL file with fn(item) applied to every parsable line; fn returns True if it changed."""
    out = []
    for raw in _read(path):
        try:
            item = json.loads(raw)
            changed = isinstance(item, dict) and isinstance(item.get('record'), dict) and fn(item)
        except ValueError:
            changed = False
        out.append(_dump(item) if changed else raw)
    return ''.join(out)


def _locate(thread_id, base):
    for project in inbox.projects(base):
        for thread in inbox.threads(project):
            if thread['thread_id'] == thread_id:
                return project, thread
    raise SystemExit('No Inbox thread %s' % thread_id)


def _line(value, label, limit):
    value = ' '.join(str(value or '').split())
    if not value:
        raise SystemExit('The %s is empty' % label)
    if len(value) > limit:
        raise SystemExit('The %s is %d characters; the limit used here is %d' % (label, len(value), limit))
    return value


def check_name(name):
    return None if name is None else _line(name, 'title', MAX_NAME)


def _thread_ids(project, thread_id):
    latest = inbox._records(Path(project['dir'])/'messages.jsonl')
    return sorted(i for i, r in latest.items() if r.get('thread_id') == thread_id)


def _name_clash(project, thread_id, name):
    if not name:
        return None
    for other in inbox.threads(project):
        if other['thread_id'] != thread_id and not other['resolved'] and (other['thread_name'] or '').casefold() == name.casefold():
            return other['thread_id']
    return None


def _base_plan(recipe, project, thread, **extra):
    return dict({'recipe': recipe, 'thread_id': thread['thread_id'], 'project': project['name'], 'root': project['root'],
                 'subject': thread['subject']}, **extra)


def _plan(recipe, thread_id, base, **opts):
    """-> (report, {path: new text}, verify(base) -> bool). Writes nothing."""
    project, thread = _locate(thread_id, base)
    folder = Path(project['dir'])
    messages = folder/'messages.jsonl'
    latest = inbox._records(messages)

    if recipe == 'title':
        name = check_name(opts.get('name'))
        clash = _name_clash(project, thread_id, name)
        if clash:
            raise SystemExit('Another open thread of %s already has the title %r (%s)' % (project['name'], name, clash))
        ids = {i for i, r in latest.items() if r.get('thread_id') == thread_id and (i == thread_id or r.get('thread_name'))}

        def fn(item):
            if item.get('id') in ids:
                item['record']['thread_name'] = name
                return True
        report = _base_plan('title', project, thread, before=thread['thread_name'], after=name, message_ids=sorted(ids),
                            changed=thread['thread_name'] != name)
        return report, {messages: _map(messages, fn)}, \
            lambda b: inbox._records(messages).get(thread_id, {}).get('thread_name') == name

    if recipe == 'subject':
        subject = _line(opts.get('subject'), 'subject', MAX_SUBJECT)

        def fn(item):
            if item.get('id') == thread_id:
                item['record']['subject'] = subject
                return True
        report = _base_plan('subject', project, thread, before=thread['subject'], after=subject, changed=thread['subject'] != subject)
        return report, {messages: _map(messages, fn)}, lambda b: inbox._records(messages).get(thread_id, {}).get('subject') == subject

    if recipe == 'reopen':
        if not thread['resolved']:
            return _base_plan('reopen', project, thread, before=None, after=None, changed=False, note='thread is not resolved'), {}, None
        before = latest[thread_id].get('resolved_at')

        def fn(item):
            if item.get('id') == thread_id and item['record'].get('resolved_at'):
                item['record']['resolved_at'] = None
                return True
        clash = _name_clash(project, thread_id, thread['thread_name'])
        if clash:
            raise SystemExit('Reopening would give two open threads the title %r (%s); rename one first' % (thread['thread_name'], clash))
        report = _base_plan('reopen', project, thread, before=before, after=None, changed=True)
        return report, {messages: _map(messages, fn)}, lambda b: not inbox._records(messages).get(thread_id, {}).get('resolved_at')

    if recipe == 'author':
        message_id, name, host = opts.get('message'), opts.get('name'), opts.get('host')
        record = latest.get(message_id)
        if not record or record.get('thread_id') != thread_id:
            raise SystemExit('Message %s is not in thread %s' % (message_id, thread_id))
        if (record.get('author') or {}).get('kind') != 'agent':
            raise SystemExit('Only an agent message can get a different author name')
        if not name and not host:
            raise SystemExit('Give the new author name and/or host')
        new = {k: _line(v, k, 60) for k, v in (('name', name), ('host', host)) if v}
        before = {k: (record.get('author') or {}).get(k) for k in new}

        def fn(item):
            if item.get('id') == message_id and isinstance(item['record'].get('author'), dict):
                item['record']['author'].update(new)
                return True
        report = _base_plan('author', project, thread, message_id=message_id, before=before, after=new, changed=before != new)
        return report, {messages: _map(messages, fn)}, \
            lambda b: all((inbox._records(messages).get(message_id, {}).get('author') or {}).get(k) == v for k, v in new.items())

    if recipe == 'withdraw':
        question = opts.get('question') or ''
        questions = folder/'questions.jsonl'
        record = inbox._records(questions).get(question)
        if not record or record.get('message_id') not in latest or latest[record['message_id']].get('thread_id') != thread_id:
            raise SystemExit('Question %s is not in thread %s (use <message id>/<key>)' % (question, thread_id))
        if record.get('superseded_by'):
            return _base_plan('withdraw', project, thread, question=question, changed=False, note='already withdrawn'), {}, None
        if record.get('answer') is not None or record.get('sent_reply_id'):
            raise SystemExit('Question %s was answered or sent; it cannot be withdrawn' % question)
        marker = {'message_id': record['message_id'], 'key': None}

        def fn(item):
            if item.get('id') == question:
                item['record']['superseded_by'] = marker
                return True
        report = _base_plan('withdraw', project, thread, question=question, changed=True)
        return report, {questions: _map(questions, fn)}, lambda b: bool(inbox._records(questions).get(question, {}).get('superseded_by'))

    if recipe == 'move':
        wanted = str(opts.get('destination') or '')
        targets = [p for p in inbox.projects(base) if wanted in (p['id'], p['key'], p['name'], p['root']) and p['id'] != project['id']]
        if len(targets) != 1:
            raise SystemExit('Destination %r must name exactly one other Inbox project (id, key, name or root); found %d' % (wanted, len(targets)))
        target = targets[0]
        clash = _name_clash(target, thread_id, thread['thread_name'])
        if clash:
            raise SystemExit('%s already has an open thread titled %r (%s); rename this one first' % (target['name'], thread['thread_name'], clash))
        ids = set(_thread_ids(project, thread_id))
        files, moved = {}, {}
        for name in THREAD_FILES:
            keep, move = [], []
            for raw in _read(folder/(name + '.jsonl')):
                try:
                    item = json.loads(raw)
                    record = item['record']
                    hit = item.get('id') in ids or record.get('message_id') in ids or record.get('thread_id') == thread_id
                except (ValueError, KeyError, TypeError, AttributeError):
                    hit = False
                if hit:
                    record['project_id'] = target['id']
                    move.append(_dump(item))
                else:
                    keep.append(raw)
            if move:
                dest = Path(target['dir'])/(name + '.jsonl')
                head = ''.join(_read(dest))
                files[folder/(name + '.jsonl')] = ''.join(keep)
                files[dest] = head + ('' if not head or head.endswith('\n') else '\n') + ''.join(move)
                moved[name] = len(move)
        report = _base_plan('move', project, thread, destination=target['name'], destination_root=target['root'], moved=moved,
                            changed=True, note='attachments keep their absolute paths; the thread moves with its questions, steps and annotations')

        def verify(b):
            return thread_id not in {t['thread_id'] for t in inbox.threads(project)} and \
                thread_id in {t['thread_id'] for t in inbox.threads(target)}
        return report, files, verify

    raise SystemExit('Unknown recipe %s' % recipe)


def _atomic_write(path, text):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp-%d' % os.getpid())
    temp.write_text(text, encoding='utf-8')
    if path.exists():
        os.chmod(temp, path.stat().st_mode & 0o777)
    os.replace(temp, path)


def _inbox(*args, timeout=60):
    return subprocess.run([inbox.plannotator(), 'inbox', *args], capture_output=True, text=True, timeout=timeout)


def served(report, timeout=20):
    """Ask the running Inbox for the thread and check that it shows the edit. -> (ok, why)."""
    root = report.get('destination_root') or report['root']
    try:
        proc = _call_read(report['thread_id'], root, timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, 'Inbox API did not answer: %s' % exc
    if proc.returncode:
        return False, 'Inbox API refused read_thread: %s' % (proc.stderr.strip() or 'exit %d' % proc.returncode)
    try:
        thread = json.loads(proc.stdout)['thread']
    except (ValueError, KeyError, TypeError):
        return False, 'Inbox API answered something unreadable'
    recipe, after = report['recipe'], report.get('after')
    checks = {'title': lambda: thread.get('thread_name') == after,
              'subject': lambda: thread.get('subject') == after,
              'reopen': lambda: not thread.get('resolved_at'),
              'move': lambda: (thread.get('project') or {}).get('root') == report.get('destination_root')}
    ok = thread.get('thread_id') == report['thread_id'] and checks.get(recipe, lambda: True)()
    return ok, '' if ok else 'the Inbox does not show the %s change' % recipe


def _call_read(thread_id, root, timeout):
    return subprocess.run([inbox.plannotator(), 'inbox', 'call', 'read_thread', json.dumps({'thread_id': thread_id, 'limit': 1})],
                          cwd=root if Path(root).is_dir() else None, capture_output=True, text=True, timeout=timeout)


def _write_back(files, originals):
    for path in files:
        if path in originals:
            _atomic_write(path, originals[path].decode('utf-8'))
        elif path.exists():
            path.unlink()


def edit(recipe, thread_id, apply=False, base=None, backup_dir=None, restart=True, **opts):
    """Preview (default) or apply one recipe on one thread; returns a report dict.

    A Plannotator version the edits were not checked on does not stop the edit (the owner's rule): every
    touched file is backed up first, and after the restart the Inbox must be healthy and show the change
    through its own API. If not, the originals are written back, the Inbox restarted again, and the
    command fails with a warning naming the backups."""
    report, files, verify = _plan(recipe, thread_id, base, **opts)
    if not report['changed']:
        return dict(report, applied=False, note=report.get('note') or 'nothing to change')
    if not apply:
        return dict(report, applied=False, preview='add --yes to stop the Inbox, edit and restart it')
    status = inbox._status()
    version = status.get('version') or status.get('cli_version')
    unchecked = bool(version and version not in inbox.CHECKED_VERSIONS)
    folder = Path(backup_dir or inbox.data_dir()/'inbox'/'agent-utils-backups')
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    originals = {path: path.read_bytes() for path in files if path.exists()}
    backups = []
    for path, data in originals.items():
        copy = folder/('%s-%s.%s' % (path.parent.name, path.name, stamp))
        copy.write_bytes(data)
        backups.append(str(copy))
    was_running = bool(status.get('running'))
    if was_running:
        _inbox('stop')
        time.sleep(1)
        if inbox._status().get('running'):
            raise SystemExit('Inbox still running after stop; nothing was edited. Backups: %s' % ', '.join(backups))
    for path, text in files.items():
        _atomic_write(path, text)
    ok = False
    try:
        ok = bool(verify(base))
    except Exception:  # an unreadable result counts as a failed edit
        ok = False
    if not ok:
        _write_back(files, originals)
        if was_running and restart:_inbox('--background')
        raise SystemExit('\u26a0\ufe0f Edit did not read back; the originals were restored. Backups: %s' % ', '.join(backups))
    report = dict(report, applied=True, backups=backups)
    if unchecked:
        report['version_note'] = 'Plannotator %s was not checked for this edit (checked: %s); verified through the Inbox afterwards' % (
            version, ', '.join(inbox.CHECKED_VERSIONS))
    if was_running and restart:
        _inbox('--background')
        time.sleep(2)
        health = inbox.check(base)
        seen, why = served(report) if health['healthy'] else (False, '; '.join(health['problems']))
        report['doctor'] = health
        if not seen:
            _inbox('stop')
            time.sleep(1)
            _write_back(files, originals)
            _inbox('--background')
            time.sleep(2)
            after = inbox.check(base)
            raise SystemExit('\u26a0\ufe0f The %s edit failed after the restart (%s). The originals were restored and the Inbox '
                             'restarted (%s). Backups: %s' % (recipe, why, 'healthy' if after['healthy'] else
                             'still unhealthy: run `agents admin inbox doctor --fix --yes`', ', '.join(backups)))
        report['served'] = True
    elif unchecked:
        report['note'] = 'The Inbox was not running, so the change could not be checked through it; backups: ' + ', '.join(backups)
    return report


def rename(thread_id, name, apply=False, base=None, backup_dir=None, restart=True):
    """Thread title (`thread_name`); None clears it."""
    return edit('title', thread_id, apply=apply, base=base, backup_dir=backup_dir, restart=restart, name=name)

