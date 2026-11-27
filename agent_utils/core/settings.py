"""One view of every agent-utils setting and action: `agents admin config`.

The registry below is the settings schema. Each editable setting names its file, its
place in that file, a type and a default. Settings that other commands own (projects,
skills policy, instructions, hooks, schedules) are listed with their state and the
command that changes them. Files that belong to agterm or a provider are never edited.

Every change is a preview until `--yes`: it shows a diff, takes the file's lock, checks
the whole file, backs it up and writes it atomically. `undo FILE` restores the newest
backup. Actions run the existing commands as child processes of this Python, with their
output shown, so the CLI, the menu and scripts share one implementation.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import difflib
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

from .agents import AGENTS
from .files import atomic_write
from .paths import config_dir, config_path, state_dir
from .setup import adopt_job, kiro, relay

SECTIONS = ('providers', 'placement', 'inbox', 'notify', 'projects', 'snippets', 'skills', 'instructions',
            'schedules', 'hooks')
SECTION_TITLES = {
    'providers': 'Agents',
    'placement': 'agterm settings',
    'inbox': 'Plannotator Inbox',
    'notify': 'Notifications',
    'projects': 'Projects and windows',
    'snippets': 'Personalization',
    'skills': 'Skills',
    'instructions': 'Shared rules',
    'schedules': 'Scheduled jobs',
    'hooks': 'Hooks',
}
NAME_RE = re.compile(r'^[\w.-]{1,64}$')


# ---------------------------------------------------------------- files

def _validate_config(data):
    from ..cli import validate
    validate(data)


def _validate_notify(data):
    url = data.get('url', '')
    if url:
        from .notify import valid_url
        if not valid_url(url):
            raise ValueError('notify.url must look like https://host/topic')
    if data.get('enabled') and not url:
        raise ValueError('notify.enabled needs notify.url first')


FILES = {
    # id: (path function, empty document, whole-file check)
    'config': (lambda: config_path(), lambda: {'version': 1, 'windows': []}, _validate_config),
    'notify': (lambda: config_dir()/'notify.json', lambda: {'version': 1}, _validate_notify),
    'snippets': (lambda: config_dir()/'snippets.json', lambda: {}, lambda data: None),
}


def file_path(file_id):
    if file_id not in FILES:
        raise ValueError('Unknown file %r. Files: %s' % (file_id, ', '.join(FILES)))
    return Path(FILES[file_id][0]())


def read(file_id):
    path = file_path(file_id)
    if not path.exists():
        return FILES[file_id][1]()
    try:
        data = json.loads(path.read_text())
    except ValueError as exc:
        raise ValueError('%s is not valid JSON: %s' % (path, exc))
    if not isinstance(data, dict):
        raise ValueError('%s must hold a JSON object' % path)
    return data


# ---------------------------------------------------------------- schema

class Setting:
    """kind: bool, int (seconds, or the (min, max) in choices), choice, text, url, agents (comma list), args (shell words).
    A key ending in `.*` is a family; the last part is an agent or a variable name."""
    def __init__(self, key, section, file, path, kind, default, help, choices=None):
        self.key, self.section, self.file, self.path = key, section, file, tuple(path)
        self.kind, self.default, self.help, self.choices = kind, default, help, choices

    @property
    def family(self):
        return self.key.endswith('.*')

    def concrete(self, name):
        """The setting for one member of a family (agent or variable name)."""
        if self.key.endswith('.launch_args.*') and name not in AGENTS:
            raise ValueError('Unknown agent %r. Agents: %s' % (name, ', '.join(AGENTS)))
        if not NAME_RE.match(name):
            raise ValueError('Invalid name %r' % name)
        help = self.help.replace('one agent', name).replace('you choose a name, snippets write it as {{name}}', 'snippets write it as {{%s}}' % name)
        return Setting(self.key[:-1]+name, self.section, self.file, self.path[:-1]+(name,),
                       self.kind, self.default, help, self.choices)

    def parse(self, text):
        text = str(text)
        if self.kind == 'bool':
            value = {'on': True, 'true': True, 'yes': True, '1': True,
                     'off': False, 'false': False, 'no': False, '0': False}.get(text.strip().lower())
            if value is None:
                raise ValueError('%s takes on or off' % self.key)
            return value
        if self.kind == 'int':
            low, high = self.choices or (0, 86400)
            try:
                value = int(text.strip())
            except ValueError:
                value = None
            if value is None or not low <= value <= high:
                raise ValueError('%s takes a whole number from %d to %d' % (self.key, low, high))
            return value
        if self.kind == 'choice':
            if text not in self.choices:
                raise ValueError('%s takes one of: %s' % (self.key, ', '.join(self.choices)))
            return text
        if self.kind == 'agents':
            values = list(dict.fromkeys(x.strip() for x in text.split(',') if x.strip()))
            bad = [x for x in values if x not in AGENTS]
            if bad:
                raise ValueError('Unknown agent(s) %s. Agents: %s' % (', '.join(bad), ', '.join(AGENTS)))
            return values
        if self.kind == 'args':
            return shlex.split(text)
        if '\x00' in text or len(text) > 2000:
            raise ValueError('%s: value too long or has a NUL byte' % self.key)
        return text

    def show(self, value):
        if self.kind == 'bool':
            return 'on' if value else 'off'
        if self.kind == 'int':
            return str(value)
        if self.kind == 'agents':
            return ','.join(value) or '(none)'
        if self.kind == 'args':
            return shlex.join(value) or '(none)'
        return value if value != '' else '(empty)'


SETTINGS = [
    Setting('zellij.config_path', 'projects', 'config', ['zellij', 'config_path'], 'text', '',
            'Zellij UI config path for local clients (empty: Zellij default)'),
    Setting('zellij.ssh_config_path', 'projects', 'config', ['zellij', 'ssh_config_path'], 'text', '~/.config/zellij/config-ssh.kdl',
            'Zellij UI config path for SSH clients (empty: Zellij default)'),
    Setting('zellij.session', 'projects', 'config', ['zellij', 'session'], 'text', 'agents',
            'Default session for agents zellij run outside Zellij'),
    Setting('providers.auto_detect', 'providers', 'config', ['providers', 'auto_detect'], 'bool', True,
            'Offer every installed agent in menus (off: only providers.enabled)'),
    Setting('providers.hidden', 'providers', 'config', ['providers', 'hidden'], 'agents', [],
            'Agents left out of menus while auto-detect is on'),
    Setting('providers.enabled', 'providers', 'config', ['providers', 'enabled'], 'agents', [],
            'Agents offered while auto-detect is off'),
    Setting('providers.launch_args.*', 'providers', 'config', ['providers', 'launch_args', '*'], 'args', [],
            'Extra arguments for one agent at launch'),
    Setting('new_session_position', 'placement', 'config', ['new_session_position'], 'choice', 'default',
            'Where new agterm rows go: default or top (once per row)', ('default', 'top')),
    Setting('coordinator_name', 'placement', 'config', ['coordinator_name'], 'text', 'Main',
            'Name of the coordinator Claude session (row title and --name)'),
    Setting('inbox.auto_adopt', 'inbox', 'config', ['inbox', 'auto_adopt'], 'bool', True,
            'Open an agterm row for each session Plannotator starts from the Inbox'),
    Setting('inbox.adopt_interval_minutes', 'inbox', 'config', ['inbox', 'adopt_interval_minutes'], 'int', 5,
            'Minutes between auto-adopt checks (run the inbox-adopt action again to apply)', (1, 1440)),
    Setting('notify.enabled', 'notify', 'notify', ['enabled'], 'bool', False, 'Send agent notifications'),
    Setting('notify.url', 'notify', 'notify', ['url'], 'url', '', 'ntfy URL, https://host/topic'),
    Setting('notify.away_after_seconds', 'notify', 'notify', ['away_after_seconds'], 'int', 120,
            'Seconds without keyboard or mouse input before a push is sent (0: always send; macOS)'),
    Setting('notify.away_when_locked', 'notify', 'notify', ['away_when_locked'], 'bool', True,
            'Count a locked screen as away and send the push at once (macOS)'),
    Setting('notify.title', 'notify', 'notify', ['title'], 'text', '{session}', 'Notification title template'),
    Setting('notify.body', 'notify', 'notify', ['body'], 'text', '{event_text}{detail_suffix}',
            'Notification body template'),
    Setting('snippets.variables.*', 'snippets', 'snippets', ['variables', '*'], 'text', '',
            'Snippet variable: you choose a name, snippets write it as {{name}} and it is replaced by your value'),
]


def find(key):
    for setting in SETTINGS:
        if setting.key == key:
            return setting
        if setting.family and key.startswith(setting.key[:-1]) and len(key) > len(setting.key)-1:
            return setting.concrete(key[len(setting.key)-1:])
    raise ValueError('Unknown setting %r. Run `agents admin config list`.' % key)


def _get(data, path, default):
    for part in path:
        if not isinstance(data, dict) or part not in data:
            return default
        data = data[part]
    return data


def _members(setting, data):
    found = _get(data, setting.path[:-1], {})
    names = sorted(found) if isinstance(found, dict) else []
    if setting.key.endswith('.launch_args.*'):  # every supported agent, set or not
        names = list(AGENTS) + [n for n in names if n not in AGENTS]
    return names


def values(section=None):
    """Every setting with its value: [{key, section, value, shown, default, set, help, file}]."""
    cache, rows = {}, []
    for setting in SETTINGS:
        if section and setting.section != section:
            continue
        if setting.file not in cache:
            cache[setting.file] = read(setting.file)
        data = cache[setting.file]
        items = [setting.concrete(n) for n in _members(setting, data)] if setting.family else [setting]
        if setting.family and not items:
            rows.append({'key': setting.key, 'section': setting.section, 'value': None, 'shown': '(none set)',
                         'default': setting.default, 'set': False, 'help': setting.help, 'file': setting.file})
        for item in items:
            missing = object()
            value = _get(data, item.path, missing)
            current = item.default if value is missing else value
            rows.append({'key': item.key, 'section': item.section, 'value': current, 'shown': item.show(current),
                         'default': item.default, 'set': value is not missing, 'help': item.help, 'file': item.file})
    return rows


# ---------------------------------------------------------------- managed elsewhere

def _optional_items(home):
    zsh = home/'.zshrc'
    zsh_on = zsh.is_file() and '# BEGIN agterm-utils zsh startup' in zsh.read_text(errors='replace')
    return [
        ('kiro-hooks', 'Kiro attention and Inbox hooks', kiro.status(home)),
        ('inbox-relay', 'Inbox reply relay (launchd)', relay.status(home)),
        ('inbox-adopt', 'Inbox auto-adopt job (launchd)', adopt_job.status(home)),
        ('zsh-startup', 'One-shot agterm startup hook in zsh', zsh_on),
    ]


def managed(home=None):
    """Settings owned by other commands or by agterm/providers: shown, not edited here."""
    home = Path(home or Path.home())
    rows = []
    try:
        data = read('config')
        windows = data.get('windows', [])
        projects = sum(len(w.get('projects', [])) for w in windows)
        ordered = sum(1 for w in windows for x in [w]+w.get('projects', []) if x.get('agent_order'))
        rows += [('projects', 'Windows and projects', '%d windows, %d projects' % (len(windows), projects),
                  'agents admin project add|edit|enable|disable|move|order'),
                 ('projects', 'Agent order per window/project', '%d set' % ordered,
                  'agents admin settings --window W|--project P --agent-order a,b'),
                 ('projects', 'SSH remotes', '%d' % len(data.get('remotes', [])), 'edit config.json "remotes"'),
                 ('projects', 'Zellij layout and session per window/project',
                  '%d set' % sum(1 for w in windows for x in [w]+w.get('projects', []) if x.get('zellij')),
                  'agents zellij layout set (menu), or agents admin settings --project P --zellij-layout L --zellij-session S')]
    except ValueError as exc:
        rows.append(('projects', 'config.json', 'unreadable: %s' % exc, 'agents agterm validate'))
    policy = config_dir()/'skills.json'
    rows.append(('skills', 'Skills policy', 'present' if policy.exists() else 'not created',
                 'agents admin skills exclude|include|reviewed|dismiss'))
    sources = config_dir()/'instructions'
    parts = sorted(p.name for p in sources.glob('*.md')) if sources.is_dir() else []
    rows.append(('instructions', 'Shared rule sources', ', '.join(parts) or 'none',
                 'edit the files, then agents admin instructions apply --yes'))
    plist = home/'Library/LaunchAgents/agent-utils.skills-check.plist'
    rows.append(('schedules', 'Skills drift check', 'installed' if plist.exists() else 'not installed',
                 'agents admin skills schedule install|remove'))
    for name, text, done in _optional_items(home):
        rows.append(('hooks', text, 'configured' if done else 'not configured',
                     'agents admin config run optional %s --yes' % name))
    return rows


# ---------------------------------------------------------------- changes

def _stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


@contextmanager
def _locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix+'.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _dump(data):
    return json.dumps(data, indent=2).splitlines(keepends=True)


def edit(file_id, mutate, label, apply=False, out=print):
    """Apply mutate(document) to one file: diff, lock, whole-file check, backup, atomic write.
    Returns True when the file would change; writes only with apply."""
    path = file_path(file_id)
    with _locked(path):
        before = read(file_id)
        after = json.loads(json.dumps(before))
        mutate(after)
        FILES[file_id][2](after)
        diff = list(difflib.unified_diff(_dump(before) if path.exists() else [], _dump(after),
                                         str(path), str(path)+' (new)'))
        if not diff:
            out('%s: no change' % label)
            return False
        out(''.join(diff).rstrip('\n'))
        if not apply:
            out('Preview only; add --yes to write %s (a backup is kept).' % path)
            return True
        if path.exists():
            shutil.copy2(path, path.with_name(path.name+'.backup-'+_stamp()))
        atomic_write(path, after)
        out('Wrote %s. Undo: agents admin config undo %s --yes' % (path, file_id))
        return True


def change(key, value=None, remove=False, apply=False, out=print):
    """Set (value is text) or unset one setting. Returns True when the file would change."""
    setting = find(key)
    if setting.family:
        raise ValueError('Name one member, e.g. %s' % setting.key.replace('*', 'NAME'))
    path = file_path(setting.file)

    def mutate(after):
        parent = after
        for part in setting.path[:-1]:
            parent = parent.setdefault(part, {})
            if not isinstance(parent, dict):
                raise ValueError('%s: %s is not an object in %s' % (key, part, path))
        if remove:
            parent.pop(setting.path[-1], None)
        else:
            parent[setting.path[-1]] = setting.parse(value)
    return edit(setting.file, mutate, key, apply, out)


def backups(file_id):
    path = file_path(file_id)
    return sorted(path.parent.glob(path.name+'.backup-*'))


def undo(file_id, apply=False, out=print):
    """Restore the newest backup. The current file is backed up first, so undo can be undone."""
    path = file_path(file_id)
    with _locked(path):
        found = backups(file_id)
        if not found:
            raise ValueError('No backup of %s' % path)
        newest = found[-1]
        restored = json.loads(newest.read_text())
        FILES[file_id][2](restored)
        current = _dump(read(file_id)) if path.exists() else []
        out(''.join(difflib.unified_diff(current, _dump(restored), str(path), newest.name)).rstrip('\n') or 'Same content')
        if not apply:
            out('Preview only; add --yes to restore %s.' % newest.name)
            return newest
        if path.exists():
            shutil.copy2(path, path.with_name(path.name+'.backup-'+_stamp()))
        atomic_write(path, restored)
        newest.unlink()
        out('Restored %s from %s.' % (path, newest.name))
        return newest


# ---------------------------------------------------------------- actions

class Action:
    """One runnable task. `apply` is the extra argument that turns the preview into the change.
    Without it the action either only reads, or (direct=True) acts at once and needs --yes."""
    def __init__(self, id, section, help, argv, apply=None, module=False, takes=None, direct=False):
        self.id, self.section, self.help, self.argv = id, section, help, list(argv)
        self.apply, self.module, self.takes, self.direct = apply, module, takes, direct

    @property
    def mode(self):
        return 'preview, --yes applies' if self.apply else 'needs --yes' if self.direct else 'read-only'

    def command(self, arg=None, apply=False, home=None):
        if self.takes and not arg:
            raise ValueError('%s needs %s' % (self.id, self.takes))
        if arg and not self.takes:
            raise ValueError('%s takes no argument' % self.id)
        if self.module:
            argv = [sys.executable, '-m', 'agent_utils.core.setup.' + self.argv[0]] + self.argv[1:]
        else:
            argv = [sys.executable, '-m', 'agent_utils.entry'] + self.argv
        if arg:
            argv.append(arg)
        if apply and self.apply:
            argv.append(self.apply)
        return argv


ACTIONS = [
    Action('sync-windows', 'projects', 'Create missing agterm windows and workspaces for enabled projects (no agent starts)', ['agterm', 'bootstrap'], direct=True),
    Action('zellij-mobile-install', 'projects', 'Install the contributed mobile Zellij config', ['zellij_mobile'], '--apply', module=True),
    Action('zellij-mobile-remove', 'projects', 'Remove an unchanged contributed mobile Zellij config', ['zellij_mobile', '--remove'], '--apply', module=True),
    Action('cheatsheet', 'placement', 'Write the agterm hotkey cheatsheet and open it', ['agterm', 'cheatsheet', '--open'], direct=True),
    Action('skills-status', 'skills', 'Skills store and links per agent', ['admin', 'skills', 'status']),
    Action('skills-sync', 'skills', 'Adopt copies, link skills per policy', ['admin', 'skills', 'sync'], '--apply'),
    Action('skills-check', 'skills', 'Check skills for drift', ['admin', 'skills', 'check']),
    Action('instructions-status', 'instructions', 'Shared rules per agent', ['admin', 'instructions', 'status']),
    Action('instructions-apply', 'instructions', 'Render shared rules into every agent', ['admin', 'instructions', 'apply'], '--yes'),
    Action('instructions-update', 'instructions', 'Refresh stale snippet copies in the shared rules', ['admin', 'instructions', 'compose', '--update'], '--yes'),
    Action('schedule-install', 'schedules', 'Daily skills check (launchd)', ['admin', 'skills', 'schedule', 'install'], '--apply'),
    Action('schedule-remove', 'schedules', 'Remove the skills check schedule', ['admin', 'skills', 'schedule', 'remove'], '--apply'),
    Action('notify-show', 'notify', 'Notification configuration', ['admin', 'notify', 'show']),
    Action('notify-test', 'notify', 'Send a test notification now', ['admin', 'notify', 'test'], direct=True),
    Action('zellij-settings', 'projects', 'Choose a Zellij layout and session for a window or project', ['zellij', 'layout', 'set']),
    Action('zellij-layouts', 'projects', 'Zellij layouts: presets and your own files', ['zellij', 'layout', 'list']),
    Action('inbox-doctor', 'hooks', 'Plannotator Inbox health check', ['admin', 'inbox', 'doctor']),
    Action('optional', 'hooks', 'List or apply optional configurations (kiro-hooks, inbox-relay, inbox-adopt, ...)',
           ['optional'], '--apply', module=True, takes='an item name'),
    Action('remove-kiro-hooks', 'hooks', 'Remove the Kiro hooks', ['kiro', '--remove'], '--apply', module=True),
    Action('remove-inbox-relay', 'hooks', 'Remove the Inbox reply relay job', ['relay', '--remove'], '--apply', module=True),
    Action('remove-inbox-adopt', 'hooks', 'Remove the Inbox auto-adopt job', ['adopt_job', '--remove'], '--apply', module=True),
    Action('remove-session-names', 'hooks', 'Remove the session name hooks', ['session_names', '--remove'], '--apply', module=True),
    Action('remove-zellij-status', 'hooks', 'Remove the Zellij tab status hooks', ['zellij_status', '--remove'], '--apply', module=True),
    Action('optional-list', 'hooks', 'Optional configurations and their state', ['optional'], module=True),
    Action('install', 'install', 'Install this code with the TUI runtime', ['installer', '--with-tui'], '--apply', module=True),
    Action('install-backups', 'install', 'Code snapshots available for rollback', ['installer', '--list-backups'], module=True),
    Action('rollback', 'install', 'Restore the latest code snapshot', ['installer', '--rollback'], '--apply', module=True),
]


def action(name):
    for item in ACTIONS:
        if item.id == name:
            return item
    raise ValueError('Unknown action %r. Actions: %s' % (name, ', '.join(a.id for a in ACTIONS)))


def run(name, arg=None, apply=False, runner=subprocess.run, out=print, home=None):
    item = name if isinstance(name, Action) else action(name)
    name = item.id
    if item.direct and not apply:
        out('%s acts at once; add --yes to run it.' % name)
        return 0
    argv = item.command(arg, apply, home)
    shown = ['agents'] + argv[3:] if argv[1:3] == ['-m', 'agent_utils.entry'] else argv[1:]
    out('$ ' + shlex.join(shown))
    sys.stdout.flush()
    return runner(argv, env=child_env()).returncode


def child_env():
    """The child imports this same package, also from an install.py copy that is not on sys.path."""
    env = dict(os.environ)
    root = str(Path(__file__).resolve().parents[2])
    env['PYTHONPATH'] = root + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    return env


# ---------------------------------------------------------------- menu

def open_ui(ui='auto', runner=subprocess.run, out=print):
    """`ui`: auto (full-screen Textual app when it is installed and both ends are terminals,
    else the list menu), textual (error when unavailable) or menu (the list menu)."""
    if ui in ('auto', 'textual'):
        if sys.stdin.isatty() and sys.stdout.isatty():
            try:
                from .configure_tui import run_app
            except ImportError:
                if ui == 'textual':
                    raise ValueError('Textual is not installed; run scripts/install.py --with-tui --apply')
            else:
                return run_app()
        elif ui == 'textual':
            raise ValueError('The Textual screen needs a terminal')
    return menu(runner=runner, out=out)


def menu(pick=None, ask=input, runner=subprocess.run, out=print):
    """Sections, then entries. Each entry is a flow (configure_flows) that shows a diff or a
    preview and asks before it writes."""
    from . import configure_flows as flows
    if pick is None:
        from ..cli import pick
    from ..cli import BACK
    while True:
        section = pick('Configuration', [(SECTION_TITLES.get(s, s), s) for s in SECTIONS] + [('Done', None)])
        if section is None:
            return 0
        while True:
            entries = flows.entries(section)
            if not entries:
                out('Nothing in this section.')
                break
            choice = pick(SECTION_TITLES.get(section, section), [(label, entry) for label, entry in entries], back=True)
            if choice is None:
                return 0
            if choice == BACK:
                break
            flows.drive_sync(choice, pick, ask, runner, out)


# ---------------------------------------------------------------- CLI

def command(argv, runner=subprocess.run, out=print):
    p = argparse.ArgumentParser(prog='agents admin config', description=__doc__.split('\n\n')[0])
    sub = p.add_subparsers(dest='op')
    s = sub.add_parser('list', help='settings with values, then settings other commands own')
    s.add_argument('--section', choices=SECTIONS); s.add_argument('--json', action='store_true')
    s = sub.add_parser('get', help='print one value'); s.add_argument('key')
    s = sub.add_parser('set', help='change one value (preview unless --yes)')
    s.add_argument('key'); s.add_argument('value'); s.add_argument('--yes', action='store_true')
    s = sub.add_parser('unset', help='remove one value, back to its default (preview unless --yes)')
    s.add_argument('key'); s.add_argument('--yes', action='store_true')
    s = sub.add_parser('undo', help='restore the newest backup of a file (preview unless --yes)')
    s.add_argument('file', choices=sorted(FILES)); s.add_argument('--yes', action='store_true')
    sub.add_parser('actions', help='runnable actions')
    s = sub.add_parser('run', help='run an action; with --yes the change, else its preview')
    s.add_argument('action'); s.add_argument('arg', nargs='?'); s.add_argument('--yes', action='store_true')
    s = sub.add_parser('tui', help='open the interactive screen or menu (same as `agents configure`)')
    s.add_argument('--ui', choices=('auto', 'textual', 'menu'), default='auto',
                   help='textual: full-screen app; menu: list menu (fzf or numbered); auto: Textual when available')
    args = p.parse_args(argv)
    if args.op is None:
        p.print_help()
        return 0
    if args.op == 'tui':
        return open_ui(args.ui, runner=runner, out=out)
    if args.op == 'list':
        rows, other = values(args.section), [m for m in managed() if not args.section or m[0] == args.section]
        if args.json:
            out(json.dumps({'settings': rows, 'managed': [dict(zip(('section', 'name', 'state', 'change'), m))
                                                          for m in other]}, indent=2))
            return 0
        for r in rows:
            out('%-32s %-24s %s%s' % (r['key'], r['shown'], r['help'], '' if r['set'] else ' [default]'))
        if other:
            out('\nOwned by other commands:')
            for section, name, state, how in other:
                out('  %-14s %-34s %-24s %s' % (section, name, state, how))
        return 0
    if args.op == 'get':
        setting = find(args.key)
        row = next((r for r in values(setting.section) if r['key'] == args.key), None)
        if row is None:
            out(setting.show(setting.default)); return 0
        out(json.dumps(row['value']) if setting.kind in ('agents', 'args') else row['shown']); return 0
    if args.op in ('set', 'unset'):
        change(args.key, getattr(args, 'value', None), remove=args.op == 'unset', apply=args.yes, out=out); return 0
    if args.op == 'undo':
        undo(args.file, args.yes, out); return 0
    if args.op == 'actions':
        for a in ACTIONS:
            out('%-20s %-13s %-24s %s' % (a.id, a.section, a.mode, a.help))
        return 0
    return run(args.action, args.arg, args.yes, runner, out)
