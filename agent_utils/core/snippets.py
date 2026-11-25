"""Instruction snippets: a library of small rules and a composer for AGENTS.md files.

By default the composer edits the shared source common.md (or a per-agent part with
--part) and then runs `agents admin instructions apply`, so every installed agent gets the
result through the existing links and copies. --project and --target edit one file.

Snippets are markdown files with a short frontmatter (id, title, summary, applies-to,
default, conflicts, variables). Generic ones live in the package's `snippet_library/` folder; private
or work-specific ones go in <config>/snippets/, which wins on the same id.

A snippet is copied into the target between markers that carry a checksum of the
text as written:

    <!-- agent-utils-snippet:start id=ID sha256=... -->
    ...text...
    <!-- agent-utils-snippet:end id=ID -->

`status` tells apart: ok, stale (the library text changed, the copy was not edited),
edited (the copy was changed by hand) and missing. `update` refreshes stale copies and
keeps edited ones unless --force (a backup is kept either way). Text outside the
markers is never touched.

Variables: `{{name}}` in a snippet is replaced from <config>/snippets.json
({"variables": {"name": "value"}}), so private values stay out of the repository. A
snippet with an unset variable is reported and not written.
"""
import difflib
import hashlib
import json
import re
from pathlib import Path

from .instructions import _backup, _write
from .paths import config_dir

LIBRARY = Path(__file__).with_name('snippet_library')
START = '<!-- agent-utils-snippet:start id=%s sha256=%s -->'
END = '<!-- agent-utils-snippet:end id=%s -->'
BLOCK = re.compile(r'<!-- agent-utils-snippet:start id=([\w.-]+) sha256=([0-9a-f]+) -->\n(.*?)\n<!-- agent-utils-snippet:end id=\1 -->\n?', re.S)
VAR = re.compile(r'\{\{\s*([\w.-]+)\s*\}\}')

# Per-agent source parts of `agents admin instructions` (common.md goes to every agent).
from .instructions import PARTS


def user_dir():
    return config_dir()/'snippets'


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _split(text):
    """(meta, body); a file without frontmatter has no meta."""
    match = re.match(r'\A---\n(.*?)\n---\n(.*)\Z', text, re.S)
    if not match: return {}, text.strip()
    meta = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(':')
        if key.strip(): meta[key.strip()] = value.strip()
    return meta, match.group(2).strip()


def _list(value):
    return [x.strip() for x in (value or '').split(',') if x.strip()]


def _described(value):
    """`variables: name=meaning; other=meaning` in the frontmatter, as {name: meaning}."""
    found = {}
    for part in str(value).split(';'):
        name, _, meaning = part.partition('=')
        if name.strip():
            found[name.strip()] = meaning.strip()
    return found


def used_variables(lib):
    """{name: {'meaning': text, 'rules': [titles]}} for every {{name}} the library's rules use or declare."""
    found = {}
    for snip in lib.values():
        names = set(VAR.findall(snip['body'])) | set(snip.get('variables', {}))
        for name in sorted(names):
            entry = found.setdefault(name, {'meaning': '', 'rules': []})
            entry['meaning'] = entry['meaning'] or snip.get('variables', {}).get(name, '')
            entry['rules'].append(snip['title'])
    return found


def library(dirs=None):
    """{id: snippet} from the repository folder, then the private folder."""
    found = {}
    for folder in dirs if dirs is not None else (LIBRARY, user_dir()):
        for path in sorted(Path(folder).glob('*.md')):
            meta, body = _split(path.read_text())
            sid = meta.get('id') or path.stem
            found[sid] = {'id': sid, 'title': meta.get('title', sid), 'summary': meta.get('summary', ''),
                          'applies_to': _list(meta.get('applies-to')) or ['all'],
                          'default': meta.get('default', 'off') == 'on',
                          'conflicts': _list(meta.get('conflicts')), 'body': body, 'path': str(path),
                          'variables': _described(meta.get('variables', ''))}
    return found


def variables(path=None):
    path = Path(path or config_dir()/'snippets.json')
    if not path.is_file(): return {}
    data = json.loads(path.read_text())
    return {str(k): str(v) for k, v in data.get('variables', {}).items()}


def render(snippet, values):
    """Snippet text with variables filled; raises KeyError(name) for an unset one."""
    def fill(match):
        if match.group(1) not in values: raise KeyError(match.group(1))
        return values[match.group(1)]
    return VAR.sub(fill, snippet['body'])


def target_path(target=None, part=None, project=None, src=None):
    """A file to compose. Default: the shared source common.md, which
    `agents admin instructions apply` loads into every installed agent."""
    if target: return Path(target).expanduser()
    if project: return Path(project).expanduser()/'AGENTS.md'
    if part and part not in PARTS: raise ValueError('unknown part %s (%s)' % (part, ', '.join(PARTS)))
    from .instructions import source_dir
    return Path(src or source_dir())/((part or 'common')+'.md')


def inspect(text, lib, values):
    """State of every library snippet and of unknown blocks in one target text."""
    present = {m.group(1): (m.group(2), m.group(3)) for m in BLOCK.finditer(text)}
    rows = []
    for sid, snip in lib.items():
        if sid not in present:
            rows.append({'id': sid, 'state': 'missing'}); continue
        digest, body = present[sid]
        try: want = render(snip, values)
        except KeyError as error:
            rows.append({'id': sid, 'state': 'unset', 'variable': error.args[0]}); continue
        state = 'edited' if _digest(body) != digest else 'ok' if body == want else 'stale'
        rows.append({'id': sid, 'state': state})
    for sid in present:
        if sid not in lib: rows.append({'id': sid, 'state': 'unknown'})
    return rows


def _block(sid, body):
    return START % (sid, _digest(body))+'\n'+body+'\n'+END % sid+'\n'


def change(text, lib, values, add=(), remove=(), update=False, force=False, replace=False):
    """(new text, actions). Pure: nothing is written.

    replace drops all text outside snippet markers and the snippets not in add, so
    the file becomes exactly the added snippets in the given order (used to move a
    hand-written file onto the library; the caller keeps a backup)."""
    actions = []
    if replace:
        kept = BLOCK.findall(text)
        if text.strip(): actions.append({'id': '-', 'action': 'replace', 'reason': 'text outside the chosen snippets is dropped (backup kept)'})
        text = ''
        for sid, digest, body in kept:
            if sid not in add and _digest(body) != digest and not force:
                raise ValueError('%s was edited by hand; add it, or use --force' % sid)
    present = {m.group(1): m for m in BLOCK.finditer(text)}
    for sid in list(add)+list(remove):
        if sid not in lib and sid not in present:
            raise ValueError('unknown snippet: '+sid)
    chosen = set(present)-set(remove)|set(add)
    for sid in add:
        clash = [c for c in chosen if c != sid and (c in lib[sid]['conflicts'] or (c in lib and sid in lib[c]['conflicts']))]
        if clash: raise ValueError('%s conflicts with %s' % (sid, ', '.join(clash)))
    for sid in remove:
        if sid not in present: actions.append({'id': sid, 'action': 'skip', 'reason': 'not present'}); continue
        if _digest(present[sid].group(3)) != present[sid].group(2) and not force:
            actions.append({'id': sid, 'action': 'skip', 'reason': 'edited by hand; use --force'}); continue
        text = text.replace(present[sid].group(0), '', 1); actions.append({'id': sid, 'action': 'remove'})
        text = re.sub(r'\n{3,}', '\n\n', text)
    wanted = [(sid, 'add') for sid in add]
    if update: wanted += [(sid, 'update') for sid in present if sid in lib and sid not in add]
    for sid, verb in wanted:
        try: body = render(lib[sid], values)
        except KeyError as error:
            actions.append({'id': sid, 'action': 'skip', 'reason': 'variable %s is not set in snippets.json' % error.args[0]}); continue
        match = BLOCK.search(text) and {m.group(1): m for m in BLOCK.finditer(text)}.get(sid)
        if match:
            edited = _digest(match.group(3)) != match.group(2)
            if match.group(3) == body and not edited: continue
            if edited and not force:
                actions.append({'id': sid, 'action': 'skip', 'reason': 'edited by hand; use --force'}); continue
            text = text.replace(match.group(0), _block(sid, body), 1); actions.append({'id': sid, 'action': 'update'})
        else:
            text = text+('\n' if text and not text.endswith('\n') else '')+('\n' if text.strip() else '')+_block(sid, body)
            actions.append({'id': sid, 'action': 'add'})
    return text, actions


def command(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog='agents admin instructions compose', description=__doc__.split('\n\n')[0])
    p.add_argument('--target', help='instruction file to edit')
    p.add_argument('--project', help='project directory; edits DIR/AGENTS.md')
    p.add_argument('--part', choices=PARTS, help='a shared source part (default common: every agent)')
    p.add_argument('--list', action='store_true', help='list library snippets')
    p.add_argument('--status', action='store_true', help='state of each snippet in the target (default)')
    p.add_argument('--add', action='append', default=[], metavar='ID')
    p.add_argument('--remove', action='append', default=[], metavar='ID')
    p.add_argument('--defaults', action='store_true', help='add the snippets marked default: on')
    p.add_argument('--replace', action='store_true', help='the file becomes exactly the added snippets, in order (backup kept)')
    p.add_argument('--no-sync', action='store_true', help='after writing a shared source, do not run `agents admin instructions apply`')
    p.add_argument('--update', action='store_true', help='refresh stale copies')
    p.add_argument('--force', action='store_true', help='overwrite or remove hand-edited copies (backed up)')
    p.add_argument('--yes', action='store_true', help='write the change (default previews a diff)')
    p.add_argument('--json', action='store_true')
    args = p.parse_args(argv)
    lib = library()
    if args.list:
        if args.json: print(json.dumps(list(lib.values()), indent=2))
        else:
            for s in lib.values():
                print('%-22s %-3s %-18s %s' % (s['id'], 'on' if s['default'] else 'off', ','.join(s['applies_to']), s['summary'] or s['title']))
        return 0
    path = target_path(args.target, args.part, args.project)
    text = path.read_text() if path.is_file() else ''
    values = variables()
    add = list(args.add)
    if args.defaults:
        add += [s['id'] for s in lib.values() if s['default'] and s['id'] not in add]
    part = path.stem if not (args.target or args.project) else None
    for sid in add:
        to = lib.get(sid, {}).get('applies_to', ['all'])
        if part and 'all' not in to and part not in to and part != 'common':
            print('Note: %s applies to %s, not %s' % (sid, ', '.join(to), part))
    if not (add or args.remove or args.update or args.replace):
        rows = inspect(text, lib, values)
        if args.json: print(json.dumps({'target': str(path), 'snippets': rows}, indent=2))
        else:
            print('Target:', path)
            for r in rows: print('  %-22s %s' % (r['id'], r['state']+(' ('+r['variable']+' unset)' if 'variable' in r else '')))
        return 0
    new, actions = change(text, lib, values, add, args.remove, args.update, args.force, args.replace)
    if args.json: print(json.dumps(actions, indent=2))
    else:
        for a in actions: print('  %-7s %-22s %s' % (a['action'], a['id'], a.get('reason', '')))
        if not actions: print('Nothing to do.')
    if new != text:
        if args.yes:
            if path.is_file(): _backup(path)
            _write(path, new); print('Wrote', path, '(backup beside it)' if text else '')
            if part and not args.no_sync:
                from . import instructions
                print('Syncing to every installed agent:')
                for a in instructions.apply(src=path.parent):
                    print('  %-12s %-12s %s%s' % (a['action'], a.get('agent', ''), a['path'], ' ('+a['reason']+')' if 'reason' in a else ''))
                bad = [t for t in instructions.targets(src=path.parent) if t['state'] != 'ok']
                for t in bad: print('  not ok: %s %s %s' % (t['agent'], t['state'], t['path']))
                if not bad: print('All agent targets ok.')
        else:
            sys_diff = difflib.unified_diff(text.splitlines(True), new.splitlines(True), str(path), str(path)+' (new)')
            print(''.join(sys_diff)+'Preview only; add --yes to write.')
    return 1 if any(a['action'] == 'skip' for a in actions) else 0
