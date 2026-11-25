"""Shared agent instructions: one source directory, loaded by every installed agent.

Sources live in <config>/instructions/: common.md for every agent, and an optional
<part>.md for one agent where that agent can load a second file (see TARGETS).
Agents load the sources by reference where they can, so editing a source needs no
resync:

- link: a symlink to the source (Claude rules, Kiro steering, Copilot, Kimi, Qwen,
  Vibe, Antigravity). Kiro refuses #[[file:...]] references outside its steering
  folder (outsideRoot) but loads a symlinked file.
- json: absolute source paths in OpenCode's `instructions` array.
- block: a copy between agent-utils markers in a file another tool also writes
  (Codex and Pi AGENTS.md, which hold Plannotator's block). Text outside the
  markers is never touched.
- file: a whole managed copy (Cursor .mdc, which needs frontmatter).

Copies go stale when a source changes; `status` reports it and `apply` refreshes
them. A checksum in the marker tells a stale copy from one edited by hand. Agents
whose executable is not found are skipped unless a target already exists.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import tempfile

from .agents import agent_executable
from .paths import config_dir

NOTE = 'managed by `agents admin instructions`; edit %s instead'
START = '<!-- agent-utils:start sha256=%s · '+NOTE+' -->'
END = '<!-- agent-utils:end -->'
BLOCK = re.compile(r'<!-- agent-utils:start sha256=([0-9a-f]+)[^\n]*-->\n(.*?)\n<!-- agent-utils:end -->\n?', re.S)
FILE_HEAD = re.compile(r'\A(---\n.*?\n---\n)<!-- agent-utils sha256=([0-9a-f]+)[^\n]*-->\n(.*)\n\Z', re.S)
CURSOR_FRONT = '---\ndescription: agent-utils shared instructions\nalwaysApply: true\n---\n'

# (agent, kind, path under home, parts). A link target loads exactly one part.
TARGETS = (
    ('claude', 'link', '.claude/rules/agent-utils.md', ('common',)),
    ('claude', 'link', '.claude/rules/agent-utils-claude.md', ('claude',)),
    ('kiro-cli', 'link', '.kiro/steering/agent-utils.md', ('common',)),
    ('kiro-cli', 'link', '.kiro/steering/agent-utils-kiro.md', ('kiro',)),
    ('codex', 'block', '.codex/AGENTS.md', ('common', 'codex')),
    ('pi', 'block', '.pi/agent/AGENTS.md', ('common', 'pi')),
    ('opencode', 'json', '.config/opencode/opencode.json', ('common', 'opencode')),
    ('cursor-agent', 'file', '.cursor/rules/agent-utils.mdc', ('common', 'cursor')),
    ('copilot', 'link', '.copilot/copilot-instructions.md', ('common',)),
    ('kimi', 'link', '.kimi-code/AGENTS.md', ('common',)),
    ('qwen', 'link', '.qwen/AGENTS.md', ('common',)),
    ('vibe', 'link', '.vibe/AGENTS.md', ('common',)),
    ('agy', 'link', '.gemini/config/AGENTS.md', ('common',)),
)
PARTS = tuple(dict.fromkeys(p for t in TARGETS for p in t[3]))


def starter():
    """The first common.md: the bundled snippets marked default: on, with markers."""
    from .snippets import LIBRARY, change, library
    lib = library([LIBRARY])
    return change('', lib, {}, add=[sid for sid, s in lib.items() if s['default']])[0]


def source_dir():
    return config_dir()/'instructions'


def _sources(src):
    return {part: src/(part+'.md') for part in PARTS if (src/(part+'.md')).is_file()}


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _body(sources, parts):
    return '\n\n'.join(sources[p].read_text().strip() for p in parts if p in sources)


def _ours(entry, src):
    return isinstance(entry, str) and Path(entry).parent == src


def _state(kind, path, src, sources, parts):
    """ok, missing, stale, edited, foreign, orphan, or None (nothing wanted or found)."""
    wanted = 'common' in sources and any(p in sources for p in parts)
    if kind == 'link':
        want = sources.get(parts[0])
        if path.is_symlink():
            linked = Path(os.readlink(path))
            if linked.parent != src: return 'foreign'
            return 'ok' if want and linked == want else 'stale' if want else 'orphan'
        if path.exists(): return 'foreign'
        return 'missing' if want else None
    if kind == 'json':
        if not path.exists(): return 'missing' if wanted else None
        try: data = json.loads(path.read_text())
        except ValueError: return 'foreign'
        listed = data.get('instructions', []) if isinstance(data, dict) else None
        if not isinstance(listed, list): return 'foreign'
        mine = [x for x in listed if _ours(x, src)]
        want = [str(sources[p]) for p in parts if p in sources] if wanted else []
        if not mine: return 'missing' if want else None
        return 'orphan' if not want else 'ok' if mine == want else 'stale'
    text = path.read_text() if path.is_file() else ''
    match = BLOCK.search(text) if kind == 'block' else FILE_HEAD.match(text)
    if not match:
        if kind == 'file' and path.exists(): return 'foreign'
        return 'missing' if wanted else None
    if not wanted: return 'orphan'
    digest, body = match.group(1, 2) if kind == 'block' else match.group(2, 3)
    if _digest(body) != digest: return 'edited'
    return 'ok' if body == _body(sources, parts) else 'stale'


def targets(home=None, src=None, installed=None):
    """Each target of an installed agent (or an existing one) with its state."""
    home = Path(home or Path.home()); src = Path(src or source_dir()); sources = _sources(src)
    installed = installed or (lambda agent: agent_executable(agent) is not None)
    found = []
    for agent, kind, rel, parts in TARGETS:
        path = home/rel
        state = _state(kind, path, src, sources, parts)
        if state is None or (state == 'missing' and not installed(agent)): continue
        item = {'agent': agent, 'kind': kind, 'path': str(path), 'parts': list(parts), 'state': state}
        if kind == 'link': item['source'] = str(src/(parts[0]+'.md'))
        found.append(item)
    return found


def _backup(path):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    shutil.copy2(path, path.with_name(path.name+'.backup-'+stamp), follow_symlinks=False)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.agent-utils-')
    try:
        with os.fdopen(fd, 'w') as stream: stream.write(text)
        if path.exists(): shutil.copymode(path, tmp)
        else: os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def _link(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.agent-utils-link-'+path.name)
    if tmp.is_symlink() or tmp.exists(): tmp.unlink()
    tmp.symlink_to(target); os.replace(tmp, path)


def _render(kind, path, src, sources, parts, drop):
    """New text for a json, block or file target; None deletes the file."""
    text = path.read_text() if path.is_file() else ''
    body = _body(sources, parts)
    if kind == 'json':
        data = json.loads(text) if text else {}
        listed = [x for x in data.get('instructions', []) if not _ours(x, src)]
        if not drop: listed += [str(sources[p]) for p in parts if p in sources]
        if listed: data['instructions'] = listed
        else: data.pop('instructions', None)
        return json.dumps(data, indent=2, ensure_ascii=False)+'\n'
    if kind == 'file':
        return None if drop else CURSOR_FRONT+'<!-- agent-utils sha256=%s · %s -->\n%s\n' % (_digest(body), NOTE % src, body)
    if drop: return BLOCK.sub('', text, count=1).rstrip('\n')+'\n'
    block = START % (_digest(body), src)+'\n'+body+'\n'+END+'\n'
    if BLOCK.search(text): return BLOCK.sub(lambda m: block, text, count=1)
    return text+('\n' if text and not text.endswith('\n') else '')+('\n' if text.strip() else '')+block


def apply(home=None, src=None, remove=False, force=False, dry=False, installed=None):
    """Bring every target to its wanted state; returns the list of actions.

    A file we did not create (foreign) or a hand-edited copy is kept unless force
    is set; replaced or removed files are backed up beside themselves first.
    """
    home = Path(home or Path.home()); src = Path(src or source_dir()); actions = []
    if not remove and not (src/'common.md').exists():
        actions.append({'action': 'create', 'path': str(src/'common.md'), 'from': 'template'})
        if not dry: src.mkdir(parents=True, exist_ok=True); (src/'common.md').write_text(starter())
    sources = _sources(src)
    found = _preview(home, src, installed) if 'common' not in sources and dry and not remove else targets(home, src, installed)
    for item in found:
        path = Path(item['path']); state = item['state']; kind = item['kind']
        if (state == 'ok' and not remove) or (state == 'missing' and remove): continue
        if state in ('foreign', 'edited') and not force:
            actions.append({'action': 'skip', 'agent': item['agent'], 'path': str(path), 'state': state,
                            'reason': 'not managed here or edited by hand; use --force (a backup is kept)'})
            continue
        drop = remove or state == 'orphan'
        verb = {'link': 'link', 'json': 'list paths', 'block': 'write block', 'file': 'write'}[kind]
        actions.append({'action': ('remove' if kind in ('link', 'file') else 'unlist' if kind == 'json' else 'remove block') if drop else verb,
                        'agent': item['agent'], 'path': str(path), 'state': state})
        if dry: continue
        if path.exists() or path.is_symlink():
            if kind != 'link' or state == 'foreign': _backup(path)
        if kind == 'link':
            if drop: path.unlink()
            else: _link(path, Path(item['source']))
            continue
        text = _render(kind, path, src, sources, item['parts'], drop) if state != 'foreign' or kind != 'json' else None
        if text is None:
            if kind == 'json': actions[-1].update(action='skip', reason='not valid JSON; edit it by hand')
            elif path.exists(): path.unlink()
            continue
        _write(path, text)
    return actions


def _preview(home, src, installed):
    """Targets as they would be once the template is copied (dry run, no sources yet)."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp)/'common.md').write_text(starter())
        found = targets(home, tmp, installed)
    for item in found:
        if item.get('source'): item['source'] = str(src/Path(item['source']).name)
    return found


def command(argv=None):
    import argparse
    if argv and argv[0] == 'compose':
        from .snippets import command as compose_command
        return compose_command(argv[1:])
    p = argparse.ArgumentParser(prog='agents admin instructions', description=__doc__.split('\n\n')[0])
    p.add_argument('operation', nargs='?', default='status', choices=['status', 'apply', 'remove'])
    p.add_argument('--yes', action='store_true', help='apply/remove: make the changes (default previews)')
    p.add_argument('--force', action='store_true', help='replace a foreign file or a hand-edited copy (backed up first)')
    p.add_argument('--json', action='store_true')
    args = p.parse_args(argv); src = source_dir()
    if args.operation == 'status':
        found = targets(src=src)
        report = {'sources': str(src), 'parts': sorted(_sources(src)), 'targets': found}
        if args.json: print(json.dumps(report, indent=2))
        else:
            print('Sources:', src, '(%s)' % (', '.join(report['parts']) or 'none; run `agents admin instructions apply`'))
            for item in found: print('  %-12s %-8s %s' % (item['agent'], item['state'], item['path']))
        return 0 if found and all(item['state'] == 'ok' for item in found) else 1
    actions = apply(src=src, remove=args.operation == 'remove', force=args.force, dry=not args.yes)
    if args.json: print(json.dumps(actions, indent=2))
    else:
        for item in actions:
            print('  %-12s %-12s %s%s' % (item['action'], item.get('agent', ''), item['path'], ' ('+item['reason']+')' if 'reason' in item else ''))
        if not actions: print('Nothing to do.')
        if not args.yes and any(a['action'] != 'skip' for a in actions): print('Preview only; add --yes to apply.')
        elif args.yes and actions: print('Restart agent sessions to load the change. Codex, Pi and Cursor keep a copy: rerun after editing sources.')
    return 1 if any(a['action'] == 'skip' for a in actions) else 0
