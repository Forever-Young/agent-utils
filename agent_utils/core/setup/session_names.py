"""Opt-in session-name hooks: one name for agterm and each agent's own session list.

Preview by default; --apply writes, --remove undoes. Each changed file is backed up
beside itself first. Only agents whose executable is found are configured, and only
our own entries (those that run names.py) are added or removed.

- Claude: ~/.claude/settings.json SessionStart + UserPromptSubmit hooks.
- Qwen: ~/.qwen/settings.json, the same hooks (untested on this Qwen version).
- Codex: ~/.codex/hooks.json SessionStart + UserPromptSubmit; trust them once with /hooks.
- Kiro: ~/.kiro/hooks/agent-utils-session-names.json SessionStart + UserPromptSubmit.
- Pi: ~/.pi/agent/extensions/agent-utils-session-name.ts, a symlink to the installed copy.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import shutil
import sys

from ..agents import agent_executable
from .paths import package_dir

MARK = 'agent_utils/agterm/names.py'
SESSION = {'SessionStart': ('start', None), 'UserPromptSubmit': ('prompt', None)}
CODEX = {'SessionStart': ('start', 'startup|resume|fork|clear'), 'UserPromptSubmit': ('prompt', None)}


def command(script, agent, event):
    return shlex.join([sys.executable, str(script), 'hook', agent, event])


def claude_like(script, agent, events):
    """settings.json style: {"hooks": {Event: [{"matcher"?, "hooks": [{type, command}]}]}}."""
    return {event: {**({'matcher': matcher} if matcher else {}),
                    'hooks': [{'type': 'command', 'command': command(script, agent, kind), 'timeout': 10}]}
            for event, (kind, matcher) in events.items()}


def merge_settings(path, groups, remove):
    data = json.loads(path.read_text()) if path.exists() else {}
    hooks = data.setdefault('hooks', {})
    for event in list(hooks):
        hooks[event] = [g for g in hooks[event] if not any(MARK in h.get('command', '') for h in g.get('hooks', []))]
        if not hooks[event]:del hooks[event]
    if not remove:
        for event, group in groups.items():hooks.setdefault(event, []).append(group)
    if not hooks:data.pop('hooks')
    return json.dumps(data, indent=2)+'\n'


def kiro_text(script):
    def hook(name, trigger, kind):
        return {'name': name, 'trigger': trigger, 'action': {'type': 'command', 'command': command(script, 'kiro-cli', kind)}, 'timeout': 10}
    return json.dumps({'version': 'v1', 'hooks': [hook('agent-utils-name-start', 'SessionStart', 'start'),
                                                   hook('agent-utils-name-prompt', 'UserPromptSubmit', 'prompt')]}, indent=2)+'\n'


def make_plan(home, script, package, remove, every=False):
    """Files to write or remove: (agent, path, new text or None to delete, kind)."""
    plan = []
    for agent, path, groups in (('claude', home/'.claude/settings.json', SESSION), ('qwen', home/'.qwen/settings.json', SESSION),
                                ('codex', home/'.codex/hooks.json', CODEX)):
        if not (every or agent_executable(agent) or path.exists()):continue
        if not path.exists() and remove:continue
        new = merge_settings(path, claude_like(script, agent, groups), remove)
        if path.exists() and path.read_text() == new:continue
        if remove and not path.exists():continue
        plan.append((agent, path, new, 'json'))
    kiro = home/'.kiro/hooks/agent-utils-session-names.json'
    if every or agent_executable('kiro-cli') or kiro.exists():
        if remove:
            if kiro.exists():plan.append(('kiro-cli', kiro, None, 'file'))
        elif not kiro.exists() or kiro.read_text() != kiro_text(script):plan.append(('kiro-cli', kiro, kiro_text(script), 'file'))
    pi = home/'.pi/agent/extensions/agent-utils-session-name.ts'
    pi_source = package/'pi_session_name.ts'
    if every or agent_executable('pi') or pi.is_symlink() or pi.exists():
        if remove:
            if pi.is_symlink() or pi.exists():plan.append(('pi', pi, None, 'link'))
        elif not (pi.is_symlink() and Path(pi.readlink()) == pi_source):plan.append(('pi', pi, str(pi_source), 'link'))
    return plan


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--apply', action='store_true'); p.add_argument('--remove', action='store_true')
    p.add_argument('--home', type=Path, default=Path.home())
    p.add_argument('--all', action='store_true', help='configure agents even when their executable is not found')
    a = p.parse_args(argv); home = a.home.expanduser()
    package = package_dir(home)/'agterm'
    script = package/'names.py'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    plan = make_plan(home, script, package, a.remove, a.all)
    if not plan:
        print('Already up to date.' if not a.remove else 'Nothing to remove.'); return 0
    for agent, path, new, kind in plan:
        print(('Remove ' if new is None else 'Write  ')+'%-9s %s' % (agent, path)+(' -> '+new if kind == 'link' and new else ''))
    if not script.exists():print('Warning: %s not found; run scripts/install.py --apply first.' % script)
    print('Codex skips new hooks until you trust them: run /hooks in Codex once.')
    if not a.apply:
        print('Preview only; rerun with --apply.'); return 0
    for agent, path, new, kind in plan:
        if path.exists() and not path.is_symlink():shutil.copy2(path, path.with_name(path.name+'.backup-'+stamp))
        if new is None:path.unlink();continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == 'link':
            if path.is_symlink() or path.exists():path.unlink()
            path.symlink_to(new);continue
        tmp = path.with_name('.'+path.name+'.tmp');tmp.write_text(new);tmp.replace(path)
    print('Applied. Restart agent sessions to load the hooks. Backups, if any, are beside each file.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
