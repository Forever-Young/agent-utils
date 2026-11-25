"""Opt-in launchd job that keeps `agents admin inbox relay` running.
Preview by default; --apply writes and loads it; --remove unloads and deletes it."""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

LABEL = 'agent-utils.inbox-relay'


def status(home):
    """True when the launchd plist exists."""
    return (Path(home)/'Library/LaunchAgents'/(LABEL+'.plist')).exists()


def build(home, agents, path_dirs, log):
    return {'Label': LABEL, 'ProgramArguments': [str(agents), 'admin', 'inbox', 'relay'],
            'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 30,
            'WorkingDirectory': str(home), 'ProcessType': 'Background',
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
    for tool in ('agtermctl', 'plannotator'):
        found = shutil.which(tool)
        if found and str(Path(found).parent) not in dirs: dirs.append(str(Path(found).parent))
    dirs += [d for d in ('/usr/local/bin', '/usr/bin', '/bin') if d not in dirs]
    log = home/'.local/state/agent-utils/inbox-relay.log'
    data = build(home, agents, dirs, log)
    print('Write:', plist); print(plistlib.dumps(data).decode(), end='')
    if not agents.exists(): print('Warning: agents not found; run scripts/install.py --apply first.')
    if not (shutil.which('agtermctl') and shutil.which('plannotator')):
        print('Warning: agtermctl or plannotator not on PATH; the relay exits until both exist.')
    if not a.apply: print('Preview only; add --apply to write and load it.'); return 0
    plist.parent.mkdir(parents=True, exist_ok=True); log.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps(data))
    runner(['launchctl', 'bootout', '%s/%s' % (domain, LABEL)], capture_output=True)
    done = runner(['launchctl', 'bootstrap', domain, str(plist)], capture_output=True, text=True)
    if done.returncode:
        print('launchctl bootstrap failed: ' + (done.stderr or '').strip()[:300], file=sys.stderr); return 1
    print('Loaded %s. Log: %s. Undo: configure-relay.py --remove --apply' % (LABEL, log)); return 0


if __name__ == '__main__':
    raise SystemExit(main())
