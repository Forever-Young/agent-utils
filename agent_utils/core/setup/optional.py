"""List and apply the optional configurations, each with detection and status.
Preview by default. `--apply` applies the chosen ones (or `--detected` for every one
whose agent is found and that is not yet configured). Each item keeps its own module
and undo (`python -m agent_utils.core.setup.<module> --remove --apply`, or the
`scripts/configure-<name>.py` wrapper)."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import kiro, relay, zellij_mobile

MODULE = 'agent_utils.core.setup.'


def items(home):
    """(name, module, text, available, configured or None when unknown, why unavailable)."""
    kiro_found = bool(shutil.which('kiro-cli') or (home/'.kiro').is_dir())
    hooks = kiro.status(home)
    both = bool(shutil.which('agtermctl') and shutil.which('plannotator'))
    return [
        ('kiro-hooks', 'kiro', 'Kiro attention and Inbox hooks', kiro_found, hooks, 'Kiro not found'),
        ('inbox-relay', 'relay', 'Keep the Inbox reply relay running (launchd)',
         kiro_found and both and hooks, relay.status(home),
         'needs Kiro, agtermctl, plannotator and kiro-hooks applied first'),
        ('session-names', 'session_names', 'Shared session name hooks', True, None, ''),
        # A mobile profile is a preference, not auto-detected missing agent setup.
        ('zellij-mobile', 'zellij_mobile', 'Mobile Zellij config (separate from desktop)', True, zellij_mobile.status(home) or None, ''),
        ('zellij-status', 'zellij_status', 'Agent state on Zellij tabs (Claude, Qwen, Codex, Pi, OpenCode)', True, None, ''),
        ('zellij-adopt', 'zellij_status --adopt', 'Zellij tab status plus self-adopt of hand-started agents', True, None, ''),
        ('zsh-startup', 'zsh', 'One-shot agterm startup hook in zsh', True, None, ''),
    ]


def child_env():
    """The child imports this same package, also from an install.py copy that is not on sys.path."""
    env = dict(os.environ)
    root = str(Path(__file__).resolve().parents[3])
    env['PYTHONPATH'] = root + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    return env


def main(argv=None, runner=subprocess.run, home=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('names', nargs='*', help='items to configure; none lists them')
    p.add_argument('--apply', action='store_true')
    p.add_argument('--detected', action='store_true', help='select every available item that is not configured yet')
    p.add_argument('--home', type=Path, default=Path.home())
    a = p.parse_args(argv); home = home or a.home.expanduser()
    table = items(home); known = {t[0] for t in table}
    bad = [n for n in a.names if n not in known]
    if bad: raise SystemExit('Unknown item: %s. Known: %s' % (', '.join(bad), ', '.join(sorted(known))))
    for name, module, text, available, done, why in table:
        state = 'configured' if done else 'available' if available else 'unavailable (%s)' % why if why else 'unavailable'
        print('%-14s %-18s %s' % (name, state, text))
    chosen = [t for t in table if t[0] in a.names]
    if a.detected:
        chosen += [t for t in table if t[4] is False and t[3] and t not in chosen]
    if not chosen:
        print('Nothing selected. Name items, or use --detected. Add --apply to write.'); return 0
    # keep table order so kiro-hooks goes before inbox-relay
    for name, module, text, available, done, why in [t for t in table if t in chosen]:
        if not available:
            print('Skip %s: %s' % (name, why or 'unavailable')); continue
        module, *extra = module.split()
        cmd = [sys.executable, '-m', MODULE+module] + extra + ['--home', str(home)] + (['--apply'] if a.apply else [])
        print('\n== %s ==' % name, flush=True)
        code = runner(cmd, env=child_env()).returncode
        if code: print('%s exited %s (a module that is already up to date also exits 1)' % (name, code))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
