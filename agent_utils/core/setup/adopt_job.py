"""Opt-in launchd job that runs `agents admin inbox adopt --auto` every few minutes, so sessions
Plannotator starts from the Inbox get an agterm row. The period is `inbox.adopt_interval_minutes`
in config.json (default 5); the job exits at once when `inbox.auto_adopt` is off or agtermctl
is missing. Preview by default; --apply writes and loads it; --remove unloads and deletes it."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

LABEL = 'agent-utils.inbox-adopt'
DEFAULT_MINUTES = 5


def status(home):
    """True when the launchd plist exists."""
    return (Path(home)/'Library/LaunchAgents'/(LABEL+'.plist')).exists()


def minutes(home):
    """Period from the config's `inbox.adopt_interval_minutes` (default 5)."""
    path = Path(home)/'.config/agent-utils/config.json'
    try:
        value = json.loads(path.read_text()).get('inbox', {}).get('adopt_interval_minutes', DEFAULT_MINUTES)
    except (OSError, ValueError, AttributeError):
        return DEFAULT_MINUTES
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 1440 else DEFAULT_MINUTES


def build(home, agents, path_dirs, log, period):
    return {'Label': LABEL, 'ProgramArguments': [str(agents), 'admin', 'inbox', 'adopt', '--auto'],
            'RunAtLoad': True, 'StartInterval': period*60,
            'WorkingDirectory': str(home), 'ProcessType': 'Background', 'LowPriorityIO': True,
            'EnvironmentVariables': {'PATH': ':'.join(path_dirs)},
            'StandardOutPath': str(log), 'StandardErrorPath': str(log)}


def main(argv=None, runner=subprocess.run):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true'); p.add_argument('--remove', action='store_true')
    p.add_argument('--home', type=Path, default=Path.home())
    a = p.parse_args(argv); home = a.home.expanduser()
    plist = home/'Library/LaunchAgents'/(LABEL+'.plist')
    domain = 'gui/%d' % os.getuid()
    if a.remove:
        print('Remove:', plist)
        if not a.apply: return 0
        runner(['launchctl', 'bootout', '%s/%s' % (domain, LABEL)], capture_output=True)
        plist.unlink(missing_ok=True); print('Removed.'); return 0
    agents = home/'.bin/agents'
    if not agents.exists(): agents = Path(shutil.which('agents') or agents)
    dirs = []
    for tool in ('agtermctl', 'plannotator', 'claude', 'codex'):
        found = shutil.which(tool)
        if found and str(Path(found).parent) not in dirs: dirs.append(str(Path(found).parent))
    dirs += [d for d in ('/usr/local/bin', '/usr/bin', '/bin') if d not in dirs]
    log = home/'.local/state/agent-utils/inbox-adopt.log'
    period = minutes(home)
    data = build(home, agents, dirs, log, period)
    print('Write:', plist, '(every %d min)' % period); print(plistlib.dumps(data).decode(), end='')
    if not agents.exists(): print('Warning: agents not found; run scripts/install.py --apply first.')
    if not (shutil.which('agtermctl') and shutil.which('plannotator')):
        print('Warning: agtermctl or plannotator not on PATH; the job does nothing until both exist.')
    if not a.apply: print('Preview only; add --apply to write and load it.'); return 0
    plist.parent.mkdir(parents=True, exist_ok=True); log.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps(data))
    runner(['launchctl', 'bootout', '%s/%s' % (domain, LABEL)], capture_output=True)
    done = runner(['launchctl', 'bootstrap', domain, str(plist)], capture_output=True, text=True)
    if done.returncode:
        print('launchctl bootstrap failed: ' + (done.stderr or '').strip()[:300], file=sys.stderr); return 1
    print('Loaded %s. Log: %s. Undo: configure-adopt.py --remove --apply' % (LABEL, log)); return 0


if __name__ == '__main__':
    raise SystemExit(main())
