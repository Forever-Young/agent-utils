"""Opt-in hooks that show agent state (working, blocked, done) on the Zellij tab.

Preview by default; --apply writes, --remove undoes. Each changed file is backed up beside itself.
Only entries that run zellij_status.py are added or removed. Outside Zellij the hooks do nothing.

- Claude, Qwen: ~/.claude/settings.json, ~/.qwen/settings.json. Event names are Claude's; Qwen is untested.
- Codex: ~/.codex/hooks.json; trust the hooks once with /hooks.
- Pi: ~/.pi/agent/extensions/agent-utils-zellij-status.ts, a symlink to the installed extension.
- OpenCode v2: ~/.config/opencode/plugins/agent-utils-zellij-status/tui.js, a symlink to the installed TUI plugin.
Kiro uses its own hook file (scripts/configure-kiro.py) for status.

--adopt also adds a SessionStart hook that adopts an agent you started by hand in a Zellij pane
(`agents zellij adopt --self`) for Claude, Codex and Kiro (~/.kiro/hooks/agent-utils-zellij-adopt.json).
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import sys

from ..agents import agent_executable
from .paths import package_dir

MARK = 'zellij_status.py'
EVENTS = {'UserPromptSubmit': ('working', None), 'PostToolUse': ('working', None),
          'PermissionRequest': ('blocked', None), 'Stop': ('done', None)}
CLAUDE = {**EVENTS, 'Notification': ('blocked', None)}


def command(script, state, app):
    return shlex.join([sys.executable, str(script), state, app])


def groups(script, app, events):
    return {event: {'hooks': [{'type': 'command', 'command': command(script, state, app), 'timeout': 10}]}
            for event, (state, _) in events.items()}


def merge(path, new_groups, remove):
    data = json.loads(path.read_text()) if path.exists() else {}
    hooks = data.setdefault('hooks', {})
    for event in list(hooks):
        hooks[event] = [g for g in hooks[event] if not any(MARK in h.get('command', '') for h in g.get('hooks', []))]
        if not hooks[event]:del hooks[event]
    if not remove:
        for event, group in new_groups.items():hooks.setdefault(event, []).append(group)
    if not hooks:data.pop('hooks')
    return json.dumps(data, indent=2)+'\n'


ADOPT = {'SessionStart': ('adopt', None)}
KIRO_ADOPT = '.kiro/hooks/agent-utils-zellij-adopt.json'


def groups_for(script, agent, events, adopt):
    found = groups(script, agent, events)
    if adopt and agent in ('claude', 'codex'):
        start = groups(script, agent, ADOPT)['SessionStart']
        if agent == 'codex':start = {'matcher': 'startup|resume', **start}
        found['SessionStart'] = start
    return found


def kiro_adopt_text(script):
    return json.dumps({'version': 'v1', 'hooks': [{'name': 'agent-utils-zellij-adopt', 'trigger': 'SessionStart',
                       'action': {'type': 'command', 'command': command(script, 'adopt', 'kiro-cli')}, 'timeout': 10}]}, indent=2)+'\n'


LINKS = (('pi', '.pi/agent/extensions/agent-utils-zellij-status.ts', 'pi_zellij_status.ts'),
         ('opencode', '.config/opencode/plugins/agent-utils-zellij-status/tui.js', 'opencode_zellij_status.js'))


def link_plan(home, package, remove, every):
    plan = []
    for agent, rel, source in LINKS:
        path, target = home/rel, package/source
        present = path.is_symlink() or path.exists()
        if remove:
            if present:plan.append((agent, path, None, 'link'))
        elif (every or agent_executable(agent) or present) and not (path.is_symlink() and Path(os.readlink(path)) == target):
            plan.append((agent, path, str(target), 'link'))
    return plan


def our_events(text):
    """Events whose hook runs zellij_status.py, with its state, e.g. Stop (done)."""
    hooks = json.loads(text).get('hooks')
    entries = hooks.items() if isinstance(hooks, dict) else [(h.get('trigger'), [{'hooks': [h['action']]}]) for h in hooks or []]
    found = []
    for event, groups in entries:
        for group in groups:
            for hook in group.get('hooks', []):
                words = hook.get('command', '').split()
                if any(w.endswith(MARK) for w in words):
                    found.append('%s (%s)' % (event, words[words.index(next(w for w in words if w.endswith(MARK))) + 1]))
    return found


def make_plan(home, script, remove, every=False, adopt=False):
    """(agent, path, new text or None to delete[, 'link'])."""
    plan = []
    for agent, path, events in (('claude', home/'.claude/settings.json', CLAUDE), ('qwen', home/'.qwen/settings.json', EVENTS),
                                ('codex', home/'.codex/hooks.json', EVENTS)):
        if not (every or agent_executable(agent) or path.exists()):continue
        if remove and not path.exists():continue
        new = merge(path, groups_for(script, agent, events, adopt), remove)
        if path.exists() and path.read_text() == new:continue
        plan.append((agent, path, new))
    kiro = home/KIRO_ADOPT
    if remove:
        if kiro.exists():plan.append(('kiro-cli', kiro, None))
    elif adopt and (every or agent_executable('kiro-cli') or kiro.exists()):
        if not kiro.exists() or kiro.read_text() != kiro_adopt_text(script):plan.append(('kiro-cli', kiro, kiro_adopt_text(script)))
    return plan + link_plan(home, script.parent, remove, every)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--apply', action='store_true');p.add_argument('--remove', action='store_true')
    p.add_argument('--home', type=Path, default=Path.home())
    p.add_argument('--all', action='store_true', help='configure agents even when their executable is not found')
    p.add_argument('--adopt', action='store_true', help='also adopt agents started by hand in Zellij (SessionStart)')
    a = p.parse_args(argv);home = a.home.expanduser()
    script = package_dir(home)/'core/zellij_status.py'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    plan = make_plan(home, script, a.remove, a.all, a.adopt)
    if not plan:
        print('Already up to date.' if not a.remove else 'Nothing to remove.');return 0
    for agent, path, new, *kind in plan:
        print(('Remove ' if new is None else 'Link   ' if kind else 'Write  ')+'%-8s %s' % (agent, path)+(' -> '+new if kind and new else ''))
        if new and not kind:print('         our hooks: ' + ', '.join(our_events(new)))
    if not script.exists():print('Warning: %s not found; run scripts/install.py --apply first.' % script)
    print('Codex skips new hooks until you trust them: run /hooks in Codex once.')
    if not a.apply:
        print('Preview only; rerun with --apply.');return 0
    for agent, path, new, *kind in plan:
        if path.exists() and not path.is_symlink():shutil.copy2(path, path.with_name(path.name+'.backup-'+stamp))
        if new is None:
            path.unlink()
            if kind and path.parent.name == 'agent-utils-zellij-status' and not any(path.parent.iterdir()):path.parent.rmdir()
            continue
        if kind:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink() or path.exists():path.unlink()
            path.symlink_to(new);continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name('.'+path.name+'.tmp');tmp.write_text(new);tmp.replace(path)
    print('Applied. Restart agent sessions to load the hooks.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
