"""Opt-in Kiro CLI hooks for agterm attention. Preview by default; --remove undoes."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import shutil
import sys

from .paths import package_dir

TARGET = '.kiro/hooks/agterm-utils-attention.json'


def build(script):
    def hook(name, trigger, action):
        return {'name': name, 'trigger': trigger, 'action': {'type': 'command', 'command': shlex.join([sys.executable, str(script), action])}, 'timeout': 10}
    return {'version': 'v1', 'hooks': [hook('agterm-attention', 'PermissionRequest', 'attention'),
        hook('agterm-active-prompt', 'UserPromptSubmit', 'active'), hook('agterm-active-tool', 'PostToolUse', 'active'),
        hook('agterm-completed', 'Stop', 'completed'),
        hook('agterm-inbox-start', 'SessionStart', 'session-start'), hook('agterm-inbox-end', 'SessionEnd', 'session-end'),
        hook('agterm-inbox-learn', 'PostToolUse', 'inbox-learn'), hook('agterm-inbox-prompt', 'UserPromptSubmit', 'inbox-check'),
        hook('agterm-inbox-stop', 'Stop', 'inbox-check')]}


def status(home):
    """True when the hooks file exists."""
    return (Path(home)/TARGET).exists()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true'); p.add_argument('--remove', action='store_true')
    p.add_argument('--home', type=Path, default=Path.home())
    a = p.parse_args(argv); home = a.home.expanduser()
    script = package_dir(home)/'agterm/kiro_hook.py'
    target = home/TARGET
    text = json.dumps(build(script), indent=2)+'\n'
    if a.remove:
        print('Remove:', target)
    else:
        print('Write:', target); print('Hook script (from scripts/install.py or a Homebrew install):', script); print(text, end='')
        print('Note: PermissionRequest needs Kiro CLI 3.4.0+; older versions only get active/completed marking.')
        print('Inbox hooks connect a thread to the session that sends into it (Post Tool Use) and print a pointer line')
        print('for unread replies in connected threads on Prompt Submit and Stop.')
        print('Sessions with --trust-all-tools never prompt, so they never raise attention.')
        if not script.exists(): print('Warning: hook script not found; run scripts/install.py --apply first.')
    if not a.apply: return 0
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    if target.exists():
        if target.read_text() == text and not a.remove: raise SystemExit('Already up to date.')
        shutil.copy2(target, target.with_name(target.name+'.backup-'+stamp))
    if a.remove:
        target.unlink(missing_ok=True)
        print('Removed. Backups, if any, remain beside it.'); return 0
    target.parent.mkdir(parents=True, exist_ok=True); target.write_text(text)
    print('Applied. Restart Kiro sessions to load the hooks.'); return 0


if __name__ == '__main__':
    raise SystemExit(main())
