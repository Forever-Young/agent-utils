"""One skills store for all agents: `agents admin skills`.

The store is ~/.agents/skills, for skills meant for every agent. Codex, OpenCode,
Cursor, Pi, Kimi, Qwen, Copilot and Vibe read it directly (READS_STORE). Claude, Kiro
and Antigravity do not; they get one relative symlink per store skill in their own
folder (LINKED). A skill for only some agents stays in those agents' own folders.

Installer-owned skills (a marker such as .twg-install.json or .aws-skill-metadata, the
Plannotator installer's names) are `managed`: never linked, moved or replaced, because
the installer would later write through a link into the store. A store skill listed in
the skills CLI lock (~/.agents/.skill-lock.json, read only) keeps its store copy; a
differing agent copy is a `variant` and stays. Symlinks pointing elsewhere are
`external` and stay.

Marketplace skills (Claude, Codex and Cursor plugins, Gemini extensions) reach agents
without that marketplace through `import`: one copy in <data>/skills/<name> with a
source record, linked into the own folder of every agent that lacks it. `check`
reports imports whose source changed and skills that use Claude-only tools, which the
`skills-sync` skill adapts; `reviewed` records an adapted version as done.

Policy: <config>/skills.json. Mutating commands preview unless --apply is given.
Backups go to <state>/backups/<timestamp>/.
"""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .agents import agent_executable
from .files import atomic_write
from .paths import NAME, config_dir, state_dir

# Agents that do not read the store: (own user dir, own project dir or None).
LINKED = {'claude': ('.claude/skills', '.claude/skills'), 'kiro-cli': ('.kiro/skills', '.kiro/skills'),
          'agy': ('.gemini/config/skills', None)}
# Agents that read the store directly, with their own user dir.
READS_STORE = {'codex': '.codex/skills', 'opencode': '.config/opencode/skills', 'cursor-agent': '.cursor/skills',
               'pi': '.pi/agent/skills', 'kimi': '.kimi-code/skills', 'qwen': '.qwen/skills',
               'copilot': '.copilot/skills', 'vibe': '.vibe/skills'}
AGENT_DIRS = {a: d[0] for a, d in LINKED.items()}
ALL_DIRS = dict(AGENT_DIRS, **READS_STORE)
STORE_REL = '.agents/skills'
LOCK_REL = '.agents/.skill-lock.json'
ALIASES = {'kiro': 'kiro-cli', 'antigravity': 'agy', 'cursor': 'cursor-agent'}
IGNORED = ('.DS_Store', '.git')
IMPORT_MARK = '.agent-utils-import.json'
REVIEW_MARK = '.agent-utils-reviewed'  # project scope: the reviewed digest
MARKERS = {'.twg-install.json': 'twg', '.aws-skill-metadata': 'AWS agent toolkit',
           '.codex-system-skills.marker': 'Codex', IMPORT_MARK: NAME+' import'}
PLANNOTATOR = ('plannotator', 'plannotator-annotate', 'plannotator-inbox', 'plannotator-last', 'plannotator-review')
# Marketplace caches: (agent that has it natively or None, root, depth of the plugin dir).
MARKETS = (('claude', '.claude/plugins/cache', 3), ('codex', '.codex/plugins/cache', 3),
           ('cursor-agent', '.cursor/plugins/cache', 3), ('cursor-agent', '.cursor/plugins/local', 1),
           (None, '.gemini/extensions', 1))
# Marketplaces whose skills rely on that agent's own runtime or connectors.
AGENT_ONLY_MARKETS = ('openai-bundled', 'openai-curated', 'openai-curated-remote', 'openai-primary-runtime',
                      'claude-plugins-official')
CLAUDE_ONLY = (('AskUserQuestion', r'\bAskUserQuestion\b'), ('Task/subagent tool', r'\bTask(Create|Update)?\b\(|subagent_type|\bTask tool\b'),
               ('${CLAUDE_*} variable', r'\$\{?CLAUDE_[A-Z_]+'), ('mcp__ tool name', r'\bmcp__\w+'),
               ('TodoWrite/ExitPlanMode', r'\b(TodoWrite|ExitPlanMode|EnterWorktree)\b'))


def _now():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def data_dir():
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))/NAME


class Env:
    """Where the store, the agent folders, imports, the policy and backups are."""
    def __init__(self, home=None, project=None, policy=None, backups=None, imports=None, installed=None):
        self.home = Path(home or Path.home()); self.project = Path(project) if project else None
        base = self.project or self.home
        self.store = base/STORE_REL
        if self.project:
            self.dirs = {a: base/d[1] for a, d in LINKED.items() if d[1]}; self.readers = {}
        else:
            self.dirs = {a: base/d[0] for a, d in LINKED.items()}
            self.readers = {a: base/rel for a, rel in READS_STORE.items()}
        self.own = dict(self.dirs, **self.readers)
        self.imports = None if self.project else Path(imports or data_dir()/'skills')
        self.policy_path = None if self.project else Path(policy or config_dir()/'skills.json')
        self.backup_root = Path(backups or state_dir()/'backups'/_now())
        self.installed = installed or (lambda agent: agent_executable(agent) is not None)
        self._lock = None

    def policy(self):
        if not self.policy_path or not self.policy_path.is_file(): return {'skills': {}}
        data = json.loads(self.policy_path.read_text())
        if not isinstance(data, dict) or not isinstance(data.get('skills', {}), dict):
            raise ValueError('Invalid skills policy: '+str(self.policy_path))
        data.setdefault('skills', {}); return data

    def wants(self, name, agent, policy=None):
        entry = (policy or self.policy())['skills'].get(name, {})
        if agent in entry.get('exclude', []): return False
        return agent in entry['agents'] if 'agents' in entry else True

    def locked(self, name):
        """The skills CLI lock entry for a store skill, or None (user scope only)."""
        if self._lock is None:
            self._lock = {}
            path = self.home/LOCK_REL
            if not self.project and path.is_file():
                try: self._lock = json.loads(path.read_text()).get('skills') or {}
                except ValueError: pass
        return self._lock.get(name)


def agent_name(value, linked_only=True):
    value = ALIASES.get(value, value)
    names = AGENT_DIRS if linked_only else ALL_DIRS
    if value not in names:
        if value in READS_STORE: raise ValueError('%s reads the shared store directly; a store skill cannot be hidden from it' % value)
        raise ValueError('Unknown agent %r; expected one of %s' % (value, ', '.join(names)))
    return value


def agent_list(text, linked_only=True):
    return [agent_name(x.strip(), linked_only) for x in text.split(',') if x.strip()] if text else None


def _listed(directory):
    if not directory.is_dir(): return []
    return sorted(p.name for p in directory.iterdir()
                  if not p.name.startswith('.') and (p.is_symlink() or (p/'SKILL.md').is_file()))


def _real(path):
    return path.is_dir() and not path.is_symlink()


def tree_digest(path):
    digest = hashlib.sha256(); path = Path(path)
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED)
        for name in sorted(files):
            if name in IGNORED or name in (IMPORT_MARK, REVIEW_MARK): continue
            full = Path(root)/name
            digest.update(str(full.relative_to(path)).encode()+b'\0'); digest.update(full.read_bytes())
    return digest.hexdigest()


def newest_mtime(path):
    path = Path(path); newest = path.stat().st_mtime
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in IGNORED]
        for name in files:
            if name not in IGNORED: newest = max(newest, (Path(root)/name).stat().st_mtime)
    return newest


def front_name(path):
    """The `name:` of a skill's frontmatter, or None."""
    try: text = (Path(path)/'SKILL.md').read_text()
    except (OSError, UnicodeDecodeError): return None
    match = re.match(r'---\n(.*?)\n---', text, re.S)
    found = match and re.search(r'^name:\s*["\']?([A-Za-z0-9][\w.-]*)["\']?\s*$', match.group(1), re.M)
    return found.group(1) if found else None


def managed(name, path):
    """Which installer owns a real skill directory, or None."""
    for marker, owner in MARKERS.items():
        if (Path(path)/marker).exists(): return owner
    if name in PLANNOTATOR: return 'Plannotator installer'
    return None


def _store_owner(env, name):
    stored = env.store/name
    return managed(name, stored) if _real(stored) else ('Plannotator installer' if name in PLANNOTATOR else None)


def _ours(path, target):
    """True for a symlink whose direct target is TARGET."""
    return path.is_symlink() and os.path.abspath(path.parent/os.readlink(path)) == os.path.abspath(target)


def entry_state(env, name, agent, policy=None):
    """State of one skill in one linked agent's folder.

    ok, missing, excluded (absent, as wanted), excluded-linked (our link, unwanted),
    broken, external (a link elsewhere), managed (an installer owns it, or the store
    copy), variant (differs from a skills-CLI store copy), same / differs (a real
    copy), only-here (a real copy the store lacks), or None (nothing anywhere).
    """
    path = env.dirs[agent]/name; stored = env.store/name
    in_store = stored.exists() or stored.is_symlink()
    wanted = env.wants(name, agent, policy)
    if not path.is_symlink() and (_store_owner(env, name) or (path.is_dir() and managed(name, path))):
        return 'managed' if path.exists() or in_store else None
    if path.is_symlink():
        if not path.exists(): return 'broken' if _ours(path, stored) else 'external'
        if in_store and stored.exists() and os.path.realpath(path) == os.path.realpath(stored):
            return 'ok' if wanted else 'excluded-linked'
        return 'external'
    if path.exists():
        if not in_store: return 'only-here'
        if tree_digest(path) == tree_digest(stored): return 'same'
        return 'variant' if env.locked(name) else 'differs'
    if not in_store: return None
    return 'missing' if wanted else 'excluded'


def status(env):
    policy = env.policy(); names = set(_listed(env.store))
    for directory in env.dirs.values(): names |= set(_listed(directory))
    rows = []
    for name in sorted(names):
        stored = env.store/name
        row = {'name': name, 'store': 'yes' if stored.exists() else 'broken' if stored.is_symlink() else 'no',
               'agents': {a: entry_state(env, name, a, policy) for a in env.dirs}}
        source = policy['skills'].get(name, {}).get('source') or (env.locked(name) or {}).get('source')
        if source: row['source'] = source
        owner = _store_owner(env, name) or next((managed(name, d/name) for d in env.dirs.values()
                                                if _real(d/name) and managed(name, d/name)), None)
        if owner: row['owner'] = owner
        rows.append(row)
    return rows


def duplicates(env):
    """Entries in the own folder of an agent that also reads the store.

    A name the store also has is loaded twice (kind same, differs, link to store,
    external link); a name the store lacks (kind only-here) is not shared yet;
    `import` is a link to an imported marketplace skill.
    """
    found = []
    for agent, directory in env.readers.items():
        for name in _listed(directory):
            path = directory/name; stored = env.store/name
            if env.imports and _ours(path, env.imports/name): kind = 'import'
            elif not stored.exists(): kind = 'only-here' if not path.is_symlink() else 'external link'
            elif path.is_symlink() and os.path.realpath(path) == os.path.realpath(stored): kind = 'link to store'
            elif path.is_symlink(): kind = 'external link'
            else: kind = 'same' if tree_digest(path) == tree_digest(stored) else 'differs'
            found.append({'agent': agent, 'skill': name, 'path': str(path), 'kind': kind,
                          'owner': None if path.is_symlink() else managed(name, path)})
    return found


def _backup(env, label, name, path):
    target = env.backup_root/label/name; target.parent.mkdir(parents=True, exist_ok=True)
    n = 1
    while target.exists() or target.is_symlink(): n += 1; target = target.with_name('%s.%d' % (name, n))
    shutil.move(str(path), str(target)); return str(target)


def _copy(src, dest):
    shutil.copytree(src, dest, symlinks=True, ignore=shutil.ignore_patterns(*IGNORED))


def _symlink(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.agent-utils-link-'+path.name)
    if tmp.is_symlink(): tmp.unlink()
    tmp.symlink_to(os.path.relpath(target, path.parent)); os.replace(tmp, path)


def link(env, only=None, dry=True, names=None):
    """Create missing links for wanted agents; remove our links where unwanted or broken."""
    policy = env.policy(); actions = []
    for name in names or _listed(env.store):
        if not (env.store/name).exists(): continue
        for agent in env.dirs:
            if only and agent not in only: continue
            state = entry_state(env, name, agent, policy); path = env.dirs[agent]/name
            if state == 'missing':
                actions.append({'action': 'link', 'skill': name, 'agent': agent, 'path': str(path)})
                if not dry: _symlink(path, env.store/name)
            elif state == 'excluded-linked' and _ours(path, env.store/name):
                actions.append({'action': 'unlink', 'skill': name, 'agent': agent, 'path': str(path), 'reason': 'excluded by policy'})
                if not dry: path.unlink()
            elif state in ('same', 'differs', 'only-here', 'variant'):
                actions.append({'action': 'skip', 'skill': name, 'agent': agent, 'path': str(path),
                                'reason': 'real directory (%s)%s' % (state, '; kept' if state == 'variant' else '; use `agents admin skills sync`')})
    if not dry and not names:
        for agent, directory in env.own.items():
            for name in _listed(directory):
                path = directory/name
                if path.is_symlink() and not path.exists() and (_ours(path, env.store/name) or (env.imports and _ours(path, env.imports/name))):
                    actions.append({'action': 'unlink', 'skill': name, 'agent': agent, 'path': str(path), 'reason': 'target is gone'})
                    path.unlink()
    return actions


def _candidates(env, name, source_agent=None):
    """Unmanaged real copies of NAME in agent folders, by agent."""
    policy = env.policy(); found = []
    for agent, directory in env.own.items():
        if source_agent and agent != source_agent: continue
        path = directory/name
        if not _real(path) or not (path/'SKILL.md').is_file() or managed(name, path): continue
        if agent in env.dirs and entry_state(env, name, agent, policy) not in ('same', 'differs', 'only-here'): continue
        if agent in env.readers and _store_owner(env, name): continue
        if agent in env.readers and env.locked(name) and _real(env.store/name) and tree_digest(path) != tree_digest(env.store/name): continue
        found.append((agent, path))
    return found


def adopt(env, name, source_agent=None, dry=True):
    """Make the newest copy of NAME the store copy, back up the rest, link the linked agents.

    Folder names that differ from the frontmatter name are renamed to it in the store.
    """
    policy = env.policy(); movable = _candidates(env, name, source_agent)
    if not movable: return []
    target = name
    if not (env.store/name).exists():
        fronts = {front_name(p) for _, p in movable} - {None}
        if len(fronts) == 1 and fronts != {name}:
            target = fronts.pop()
            if (env.store/target).exists() or (env.store/target).is_symlink():
                return [{'action': 'skip', 'skill': name, 'path': str(movable[0][1]),
                         'reason': 'frontmatter name %s is already in the store' % target}]
    stored = env.store/target
    candidates = ([('store', stored)] if _real(stored) else [])+movable
    if _real(stored) and (env.locked(target) or _reviewed_digest(env, target, policy) == tree_digest(stored)): winner = ('store', stored)
    else:
        winner = max(candidates, key=lambda c: (newest_mtime(c[1]), c[0] == 'store'))
        if _real(stored) and tree_digest(stored) == tree_digest(winner[1]): winner = ('store', stored)
    actions = []; same = lambda p: tree_digest(p) == tree_digest(winner[1])
    if winner[0] != 'store':
        if stored.exists() or stored.is_symlink():
            actions.append({'action': 'backup', 'skill': target, 'from': 'store', 'path': str(stored)})
            if not dry: _backup(env, 'store', target, stored)
        actions.append({'action': 'move to store', 'skill': target, 'from': winner[0], 'path': str(winner[1]),
                        **({'reason': 'renamed from '+name} if target != name else {})})
        if not dry: env.store.mkdir(parents=True, exist_ok=True); shutil.move(str(winner[1]), str(stored))
    for label, path in movable:
        if (label, path) == winner: continue
        actions.append({'action': 'backup', 'skill': name, 'from': label, 'path': str(path),
                        'reason': 'identical copy' if same(path) else 'older content'})
        if not dry: _backup(env, label, name, path)
    gone = {label for label, _ in movable}
    for agent in env.dirs:
        path = env.dirs[agent]/target
        if _real(path) and not any(path.iterdir()):
            actions.append({'action': 'remove empty', 'skill': target, 'agent': agent, 'path': str(path)})
            gone.add(agent)
            if not dry: path.rmdir()
    for agent in env.dirs:
        if target != name and agent in gone and (env.dirs[agent]/name).is_symlink(): continue
        path = env.dirs[agent]/target
        if env.wants(target, agent, policy) and (agent in gone or not (path.exists() or path.is_symlink())):
            actions.append({'action': 'link', 'skill': target, 'agent': agent, 'path': str(path)})
            if not dry: _symlink(path, stored)
    return actions


def dedupe(env, dry=True):
    """Back up copies in a store reader's own folder that are identical to the store copy.

    Installer copies count too (an AWS toolkit copy in Pi's folder); an installer's
    per-agent variant (a different Plannotator copy in Vibe's folder) stays.
    """
    actions = []
    for item in duplicates(env):
        if item['kind'] != 'same': continue
        actions.append({'action': 'backup', 'skill': item['skill'], 'from': item['agent'], 'path': item['path'],
                        'reason': 'also loaded from the store'})
        if not dry: _backup(env, item['agent'], item['skill'], Path(item['path']))
    return actions


def sync(env, dry=True):
    """Adopt every unmanaged copy, back up duplicates, link, refresh import links."""
    names = sorted({n for d in env.own.values() for n in _listed(d)})
    actions = [a for n in names for a in adopt(env, n, dry=dry)]
    if dry:
        # Later steps depend on the moves; preview what is visible now.
        planned = {a['path'] for a in actions}
        actions += [a for a in dedupe(env, dry=True) if a['path'] not in planned]
        actions += [a for a in link(env, dry=True) if a['action'] != 'skip' and a['path'] not in planned]
    else:
        actions += dedupe(env, dry=False)
        actions += [a for a in link(env, dry=False) if a['action'] != 'skip']
    actions += relink_imports(env, dry)
    return actions


def _fetch(src):
    """A local directory path, or a git URL with an optional #subdir. Returns (dir, cleanup)."""
    local = Path(src).expanduser()
    if local.is_dir(): return local, None
    url, _, sub = src.partition('#')
    tmp = tempfile.mkdtemp(prefix='agent-utils-skill-')
    result = subprocess.run(['git', 'clone', '--depth', '1', '--quiet', url, tmp+'/repo'], capture_output=True, text=True)
    if result.returncode: shutil.rmtree(tmp, ignore_errors=True); raise ValueError('git clone failed: '+result.stderr.strip())
    return Path(tmp)/'repo'/sub, tmp


def _check_name(name):
    if not name or '/' in name or name.startswith('.'): raise ValueError('Invalid skill name %r' % name)
    return name


def add(env, src, name=None, agents=None, dry=True):
    """Copy a skill into the store (replacing one there, which is backed up) and link it."""
    path, cleanup = _fetch(src)
    try:
        if not (path/'SKILL.md').is_file(): raise ValueError('No SKILL.md in %s' % path)
        name = _check_name(name or path.resolve().name)
        stored = env.store/name; actions = []
        owner = _store_owner(env, name) if stored.exists() else None
        if owner: raise ValueError('%s is managed by %s; update it there' % (name, owner))
        if stored.exists() or stored.is_symlink():
            actions.append({'action': 'backup', 'skill': name, 'from': 'store', 'path': str(stored)})
            if not dry: _backup(env, 'store', name, stored)
        actions.append({'action': 'add', 'skill': name, 'from': src, 'path': str(stored)})
        if not dry: env.store.mkdir(parents=True, exist_ok=True); _copy(path, stored)
        if env.policy_path:
            local = Path(src).expanduser().is_dir()
            actions.append({'action': 'policy', 'skill': name, 'reason': 'record source' + (', agents '+','.join(agents) if agents else '')})
            if not dry:
                def edit(entry):
                    entry['source'] = str(Path(src).expanduser().resolve()) if local else src
                    entry.pop('reviewed', None)
                    if agents is not None: entry['agents'] = agents; entry.pop('exclude', None)
                _edit_policy(env, name, edit)
    finally:
        if cleanup: shutil.rmtree(cleanup, ignore_errors=True)
    if dry:
        policy = env.policy()
        for agent in env.dirs:
            wanted = agent in agents if agents is not None else env.wants(name, agent, policy)
            if wanted: actions.append({'action': 'link', 'skill': name, 'agent': agent, 'path': str(env.dirs[agent]/name)})
        return actions
    return actions+link(env, dry=False, names=[name])


def remove(env, name, agents=None, purge=False, dry=True):
    """Remove our links for NAME; with purge also back up the store copy, unmanaged copies and the import."""
    actions = []; targets = [env.store/name]+([env.imports/name] if env.imports else [])
    for agent, directory in env.own.items():
        if agents and agent not in agents: continue
        path = directory/name
        if any(_ours(path, t) for t in targets):
            actions.append({'action': 'unlink', 'skill': name, 'agent': agent, 'path': str(path)})
            if not dry: path.unlink()
        elif purge and _real(path) and not managed(name, path):
            actions.append({'action': 'backup and delete', 'skill': name, 'agent': agent, 'path': str(path)})
            if not dry: _backup(env, agent, name, path)
        elif path.exists() or path.is_symlink():
            actions.append({'action': 'skip', 'skill': name, 'agent': agent, 'path': str(path),
                            'reason': 'managed by '+managed(name, path) if _real(path) and managed(name, path) else 'not ours; left alone'})
    if purge and not agents:
        for label, path in (('store', env.store/name), ('imports', env.imports/name if env.imports else None)):
            if path is None or not (path.exists() or path.is_symlink()): continue
            owner = _store_owner(env, name) if label == 'store' else None
            if owner:
                actions.append({'action': 'skip', 'skill': name, 'from': label, 'path': str(path), 'reason': 'managed by '+owner}); continue
            actions.append({'action': 'backup and delete', 'skill': name, 'from': label, 'path': str(path)})
            if not dry: _backup(env, label, name, path)
    return actions


# Marketplace skills and imports

def market_skills(env):
    """Skills in marketplace caches: name -> {owners, sources: [{market, plugin, path}]}.

    Only the newest cached version of each plugin counts; nested `plugins/` copies
    (a plugin carrying variants for other agents) are skipped.
    """
    found = {}
    for owner, rel, depth in MARKETS:
        root = env.home/rel
        if not root.is_dir(): continue
        plugins = {}
        for plugin_dir in root.glob('/'.join(['*']*depth)):
            if not plugin_dir.is_dir(): continue
            key = plugin_dir.relative_to(root).parts[:2 if depth == 3 else 1]
            if key not in plugins or plugin_dir.stat().st_mtime > plugins[key].stat().st_mtime: plugins[key] = plugin_dir
        for key, plugin_dir in sorted(plugins.items()):
            market = key[0] if depth == 3 else rel.split('/')[0].lstrip('.')
            for skill_md in sorted(plugin_dir.rglob('SKILL.md')):
                inner = skill_md.parent.relative_to(plugin_dir).parts
                if len(inner) > 4 or 'plugins' in inner[:-1] or 'node_modules' in inner: continue
                name = front_name(skill_md.parent) or skill_md.parent.name
                entry = found.setdefault(name, {'owners': set(), 'sources': []})
                if owner: entry['owners'].add(owner)
                entry['sources'].append({'market': market, 'plugin': key[-1], 'owner': owner, 'path': str(skill_md.parent)})
    return found


def _source_digest(path):
    return tree_digest(path) if Path(path).is_dir() else None


def _import_record(env, name):
    path = env.imports/name/IMPORT_MARK if env.imports else None
    if not path or not path.is_file(): return None
    try: return json.loads(path.read_text())
    except ValueError: return None


def _import_targets(env, name, owners, only=None):
    agents = only or [a for a in env.own if a not in owners and env.installed(a)]
    return [(a, env.own[a]/name) for a in agents]


def import_skill(env, src, name=None, agents=None, dry=True):
    """Copy a marketplace skill to <data>/skills/NAME and link it for agents lacking it."""
    if not env.imports: raise ValueError('Imports apply to user scope only')
    path = Path(src).expanduser()
    if not (path/'SKILL.md').is_file(): raise ValueError('No SKILL.md in %s' % path)
    name = _check_name(name or front_name(path) or path.name)
    market = market_skills(env).get(name, {'owners': set(), 'sources': []})
    owners = sorted(market['owners'] | {s['owner'] for s in market['sources'] if s['path'] == str(path.resolve()) and s['owner']})
    if (env.store/name).exists(): raise ValueError('%s is already in the shared store; it reaches every agent' % name)
    target = env.imports/name; actions = []
    if target.exists():
        actions.append({'action': 'backup', 'skill': name, 'from': 'imports', 'path': str(target)})
        if not dry: _backup(env, 'imports', name, target)
    actions.append({'action': 'import', 'skill': name, 'from': str(path), 'path': str(target),
                    'reason': 'native in '+', '.join(owners) if owners else 'no agent has it natively'})
    if not dry:
        _copy(path, target)
        record = {'source': str(path.resolve()), 'source_digest': _source_digest(path), 'owners': owners,
                  'agents': agents, 'imported': _now(), 'reviewed': None}
        (target/IMPORT_MARK).write_text(json.dumps(record, indent=2)+'\n')
    for agent, dest in _import_targets(env, name, owners, agents):
        if _ours(dest, target): continue
        if dest.exists() or dest.is_symlink():
            actions.append({'action': 'skip', 'skill': name, 'agent': agent, 'path': str(dest), 'reason': 'something is already there'}); continue
        actions.append({'action': 'link', 'skill': name, 'agent': agent, 'path': str(dest)})
        if not dry: _symlink(dest, target)
    return actions


def relink_imports(env, dry=True):
    """Link every import into the agents that should have it (new agents, removed links)."""
    actions = []
    if not env.imports or not env.imports.is_dir(): return actions
    for name in _listed(env.imports):
        record = _import_record(env, name)
        if not record: continue
        for agent, dest in _import_targets(env, name, record.get('owners', []), record.get('agents')):
            if dest.exists() or dest.is_symlink(): continue
            actions.append({'action': 'link', 'skill': name, 'agent': agent, 'path': str(dest), 'reason': 'import'})
            if not dry: _symlink(dest, env.imports/name)
    return actions


def claude_only(path):
    """Claude-only constructs used in a skill's text files: [(label, count)]."""
    text = []
    for file in sorted(Path(path).rglob('*')):
        if file.is_file() and file.suffix in ('.md', '.txt', '') and file.name != IMPORT_MARK:
            try: text.append(file.read_text())
            except (OSError, UnicodeDecodeError): pass
    body = '\n'.join(text)
    return [(label, len(re.findall(rx, body))) for label, rx in CLAUDE_ONLY if re.search(rx, body)]


def check(env, all_markets=False):
    """What needs the skills-sync skill: adaptation, stale imports, marketplace skills to offer."""
    policy = env.policy(); report = {'adapt': [], 'stale': [], 'offer': []}
    for name in _listed(env.store):
        stored = env.store/name
        if not _real(stored) or _store_owner(env, name): continue
        found = claude_only(stored)
        if not found: continue
        if _reviewed_digest(env, name, policy) == tree_digest(stored): continue
        report['adapt'].append({'skill': name, 'where': 'store', 'path': str(stored), 'uses': dict(found),
                                'upstream': (env.locked(name) or {}).get('source')})
    known = set(_listed(env.store)) | {n for d in env.own.values() for n in _listed(d)}
    if env.imports and env.imports.is_dir():
        for name in _listed(env.imports):
            record = _import_record(env, name) or {}; path = env.imports/name
            if record.get('source') and _source_digest(record['source']) != record.get('source_digest'):
                report['stale'].append({'skill': name, 'path': str(path), 'source': record['source'],
                                        'reason': 'source changed' if Path(record['source']).is_dir() else 'source gone (plugin updated or removed)'})
            found = claude_only(path)
            if found and record.get('reviewed') != tree_digest(path):
                report['adapt'].append({'skill': name, 'where': 'imports', 'path': str(path), 'uses': dict(found)})
    if not env.project:
        dismissed = set(policy.get('dismissed', []))
        for name, entry in sorted(market_skills(env).items()):
            if name in known or name in dismissed: continue
            sources = [s for s in entry['sources'] if all_markets or s['market'] not in AGENT_ONLY_MARKETS]
            if not sources: continue
            best = max(sources, key=lambda s: (s['owner'] != 'claude', newest_mtime(s['path'])))
            report['offer'].append({'skill': name, 'native': sorted(entry['owners']), 'source': best['path'],
                                    'market': best['market'], 'plugin': best['plugin'], 'uses': dict(claude_only(best['path']))})
    return report


def _reviewed_digest(env, name, policy=None):
    if env.policy_path: return (policy or env.policy())['skills'].get(name, {}).get('reviewed')
    mark = env.store/name/REVIEW_MARK
    return mark.read_text().strip() if mark.is_file() else None


def reviewed(env, name, dry=True):
    """Record the current content of NAME (store or import) as reviewed for other agents."""
    imported = env.imports/name if env.imports else None
    if imported and (imported/IMPORT_MARK).is_file():
        record = _import_record(env, name); record['reviewed'] = tree_digest(imported)
        if not dry: (imported/IMPORT_MARK).write_text(json.dumps(record, indent=2)+'\n')
        return [{'action': 'reviewed', 'skill': name, 'path': str(imported)}]
    stored = env.store/name
    if not _real(stored): raise ValueError('%s is neither a store skill nor an import' % name)
    digest = tree_digest(stored)
    if not dry:
        if env.policy_path: _edit_policy(env, name, lambda e: e.__setitem__('reviewed', digest))
        else: (stored/REVIEW_MARK).write_text(digest+'\n')
    return [{'action': 'reviewed', 'skill': name, 'path': str(stored)}]


def dismiss(env, names, dry=True):
    """Stop offering these marketplace skills in `check` (placed elsewhere or not wanted)."""
    if not env.policy_path: raise ValueError('Policy applies to user scope only; do not combine with --project')
    data = env.policy(); listed = sorted(set(data.get('dismissed', [])) | set(names))
    if not dry:
        data['dismissed'] = listed
        if env.policy_path.exists(): shutil.copy2(env.policy_path, env.policy_path.with_name(env.policy_path.name+'.backup-'+_now()))
        atomic_write(env.policy_path, data)
    return [{'action': 'dismiss', 'skill': n} for n in names]


def _edit_policy(env, name, edit):
    if not env.policy_path: raise ValueError('Policy applies to user scope only; do not combine with --project')
    data = env.policy(); entry = data['skills'].setdefault(name, {}); edit(entry)
    if not entry: del data['skills'][name]
    if env.policy_path.exists():
        shutil.copy2(env.policy_path, env.policy_path.with_name(env.policy_path.name+'.backup-'+_now()))
    atomic_write(env.policy_path, data)


def set_policy(env, name, agent, exclude, dry=True):
    """Exclude or include a linked AGENT for NAME (editing the policy file; links follow from `link`)."""
    data = env.policy(); entry = data['skills'].get(name, {})
    if 'agents' in entry:
        wanted = [a for a in entry['agents'] if a != agent] if exclude else sorted(set(entry['agents'])|{agent})
        change = {'action': 'policy', 'skill': name, 'agents': wanted}
    else:
        listed = sorted(set(entry.get('exclude', []))|{agent}) if exclude else [a for a in entry.get('exclude', []) if a != agent]
        change = {'action': 'policy', 'skill': name, 'exclude': listed}
    if not dry:
        def edit(e):
            for key in ('agents', 'exclude'):
                if key in change:
                    if change[key] or key == 'agents': e[key] = change[key]
                    else: e.pop(key, None)
        _edit_policy(env, name, edit)
    return [change]


def _matrix(rows, env):
    cols = list(env.dirs)
    if env.readers: print('Read directly by: '+', '.join(env.readers))
    print('%-34s %-6s %s %s' % ('skill', 'store', ' '.join('%-10s' % c for c in cols), 'owner'))
    for row in rows:
        print('%-34s %-6s %s %s' % (row['name'], row['store'], ' '.join('%-10s' % (row['agents'][c] or '-') for c in cols), row.get('owner', '')))
    dups = duplicates(env)
    if dups:
        print('\nIn the own folder of an agent that reads the store (only-here: not shared; same/differs: loaded twice):')
        for d in dups: print('  %-12s %-34s %-13s %s' % (d['agent'], d['skill'], d['kind'], d['owner'] or ''))


def _print(actions, apply):
    for a in actions:
        extra = a.get('reason') or ('' if 'from' not in a else 'from '+str(a['from']))
        print('  %-18s %-34s %-12s %s%s' % (a['action'], a.get('skill', ''), a.get('agent', ''), a.get('path', ''), ' ('+extra+')' if extra else ''))
    if not actions: print('Nothing to do.')
    elif not apply and any(a['action'] != 'skip' for a in actions): print('Preview only; add --apply to make the changes.')
    elif apply: print('Backups (if any) are under the state folder\'s backups/. Restart agent sessions to load the change.')


def _print_check(report):
    if report['adapt']:
        print('Uses Claude-only tools (adapt with the skills-sync skill, then `agents admin skills reviewed NAME --apply`):')
        for a in report['adapt']:
            note = ' [upstream %s: an update overwrites edits]' % a['upstream'] if a.get('upstream') else ''
            print('  %-30s %-8s %s%s' % (a['skill'], a['where'], ', '.join('%s x%d' % kv for kv in a['uses'].items()), note))
    if report['stale']:
        print('Imports whose source changed (re-import, then adapt again):')
        for s in report['stale']: print('  %-30s %s (%s)' % (s['skill'], s['source'], s['reason']))
    if report['offer']:
        print('Marketplace skills other agents lack (`agents admin skills import SOURCE`):')
        for o in report['offer']:
            print('  %-30s %-26s native: %-18s %s' % (o['skill'], o['market']+'/'+o['plugin'], ','.join(o['native']) or '-',
                                                    'Claude-only: '+', '.join(o['uses']) if o['uses'] else ''))
    if not any(report.values()): print('Nothing to adapt, refresh or offer.')


def _check_command(args):
    from . import skillcheck
    if args.agent and args.no_agent: raise ValueError('--agent and --no-agent exclude each other')
    envs = ([Env(project=args.project)] if args.project else [Env()])+[Env(project=d) for d in args.also_project]
    agent = args.agent
    if agent == 'ask':
        from .ask import AGENTS, agent_executable
        agent = skillcheck.pick_agent([a for a in AGENTS if agent_executable(a)])
    elif agent:
        from .ask import resolve
        agent = resolve(agent)
    version = None
    if not args.project:
        try:
            from ..plannotator.inbox import _status
            version = _status().get('version')
        except (OSError, subprocess.SubprocessError, ValueError): pass
    result = skillcheck.run(envs, agent, args.auto, args.inbox, plannotator_version=lambda: version, all_markets=args.all_markets)
    if args.json:
        print(json.dumps({k: v for k, v in result.items() if k != 'reports'}, indent=2, default=str)); old = {}
    else:
        old = result.get('reports', {}).get('user') or (next(iter(result.get('reports', {}).values()), None) if args.project else None) or {}
        if old: _print_check(old)
        skillcheck.print_result(result, skip=('adapt', 'stale', 'offer'))
    if result['status'] != 'ok': return 0
    if args.auto or args.inbox: return 0   # scheduled use: non-zero only on tool errors
    return 1 if result['findings'] and any(f['kind'] not in ('offer',) for f in result['findings']) else 0


def command(argv=None):
    if argv and argv[0] == 'schedule':
        from .skillcheck import schedule_command
        return schedule_command(argv[1:])
    if argv and argv[0] in ('audit', 'extract', 'prune-rules'):
        from .audit import command as audit_command
        return audit_command(argv)
    p = argparse.ArgumentParser(prog='agents admin skills', description=__doc__.split('\n\n')[0],
                                epilog='Project instructions: agents admin skills audit|extract|prune-rules PROJECT (see `agents admin skills audit -h`).')
    p.add_argument('--project', help='act on DIR/.agents/skills, DIR/.claude/skills and DIR/.kiro/skills')
    sub = p.add_subparsers(dest='operation', required=True)
    def parser(name, mutating=True, **kw):
        s = sub.add_parser(name, **kw); s.add_argument('--project', default=argparse.SUPPRESS)
        if mutating: s.add_argument('--apply', action='store_true', help='make the changes (default previews)')
        return s
    parser('status', False, help='store, linked agents, owners, copies in store readers').add_argument('--json', action='store_true')
    parser('sync', help='adopt unmanaged copies, back up duplicates, link, relink imports')
    parser('link', help='create missing links per policy')
    s = parser('add', help='copy a skill (directory or git URL#subdir) into the store'); s.add_argument('src'); s.add_argument('--name'); s.add_argument('--agents')
    s = parser('remove', help='remove links; --purge also backs up every unmanaged copy'); s.add_argument('name'); s.add_argument('--agents'); s.add_argument('--purge', action='store_true')
    s = parser('adopt', help='move the newest copy into the store, link the rest'); s.add_argument('name', nargs='?'); s.add_argument('--all', action='store_true'); s.add_argument('--from', dest='source', help='only consider this agent\'s copy')
    s = parser('check', False, help='drift between store, agents and projects; skills to adapt, stale imports, marketplace skills to offer')
    s.add_argument('--json', action='store_true'); s.add_argument('--all-markets', action='store_true')
    s.add_argument('--also-project', action='append', default=[], metavar='DIR', help='also check this project (repeatable; --project checks only one project)')
    s.add_argument('--agent', help='an agent (claude, codex, kiro, ...) reviews the findings read-only; `ask` picks from a menu')
    s.add_argument('--no-agent', action='store_true', help='basic check only, no agent (the default without --agent)')
    s.add_argument('--auto', action='store_true', help='apply safe actions: create missing links, remove our dangling links')
    s.add_argument('--inbox', action='store_true', help='post one question per finding to the Inbox thread "Skills drift" and apply earlier answers')
    s = parser('import', help='copy a marketplace skill for the agents that lack it'); s.add_argument('src'); s.add_argument('--name'); s.add_argument('--agents', help='only these agents (default: every installed agent without it)')
    s = parser('dismiss', help='stop offering marketplace skills in check'); s.add_argument('names', nargs='+')
    s = parser('reviewed', help='record the current content as adapted/reviewed'); s.add_argument('name')
    for op in ('exclude', 'include'):
        s = parser(op, help=op+' a skill for Claude, Kiro or Antigravity (policy edit)'); s.add_argument('name'); s.add_argument('--agent', required=True)
    args = p.parse_args(argv); env = Env(project=args.project); dry = not getattr(args, 'apply', False)
    if args.operation == 'status':
        rows = status(env)
        if args.json: print(json.dumps({'store': str(env.store), 'skills': rows, 'readers': duplicates(env)}, indent=2))
        else: print('Store:', env.store); _matrix(rows, env)
        fine = ('ok', 'excluded', 'external', 'managed', 'variant', None)
        return 0 if all(s in fine for r in rows for s in r['agents'].values()) and not any(d['kind'] in ('same', 'only-here') for d in duplicates(env)) else 1
    if args.operation == 'check': return _check_command(args)
    if args.operation == 'sync': actions = sync(env, dry)
    elif args.operation == 'link': actions = link(env, dry=dry)
    elif args.operation == 'add': actions = add(env, args.src, args.name, agent_list(args.agents), dry)
    elif args.operation == 'remove': actions = remove(env, args.name, agent_list(args.agents, False), args.purge, dry)
    elif args.operation == 'import': actions = import_skill(env, args.src, args.name, agent_list(args.agents, False), dry)
    elif args.operation == 'dismiss': actions = dismiss(env, args.names, dry)
    elif args.operation == 'reviewed': actions = reviewed(env, args.name, dry)
    elif args.operation == 'adopt':
        if args.all == bool(args.name): raise ValueError('Give a skill name or --all')
        names = sorted({n for d in env.own.values() for n in _listed(d)}) if args.all else [args.name]
        actions = [a for n in names for a in adopt(env, n, agent_name(args.source, False) if args.source else None, dry)]
    else: actions = set_policy(env, args.name, agent_name(args.agent), args.operation == 'exclude', dry)
    _print(actions, not dry)
    return 1 if any(a['action'] == 'skip' for a in actions) else 0
