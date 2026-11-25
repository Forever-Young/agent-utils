"""Opt-in agterm default-command integration; refuses to change Live mode."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import subprocess

START = '# BEGIN agterm-utils startup'
END = '# END agterm-utils startup'


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--config-dir', type=Path, default=Path.home()/'.config/agterm')
    p.add_argument('--launcher', type=Path, default=Path.home()/'.bin/agents')
    a = p.parse_args(argv)
    path = a.config_dir/'ghostty.conf'
    text = path.read_text() if path.exists() else ''
    base = re.sub(re.escape(START)+r'.*?'+re.escape(END)+r'\n?', '', text, flags=re.S)
    if re.search(r'^\s*command\s*=', base, re.M):
        raise SystemExit('An unmanaged command setting exists. Review it before replacing.')
    # Quote the path for Ghostty command parsing; no shell interpolation.
    command = 'direct:'+str(a.launcher.expanduser())+' agterm startup'
    if any(c in str(a.launcher) for c in ' \t\n\r'):
        raise SystemExit('Launcher path must not contain whitespace for this integration.')
    block = '\n'+START+'\ncommand = '+command+'\n'+END+'\n'
    print('Target:', path); print(block)
    print('This custom-command integration is not compatible with verified Live-session persistence.')
    if not a.apply: return 0
    if not a.launcher.is_file(): raise SystemExit('Install the agents agterm launcher first.')
    ctl = shutil.which('agtermctl')
    if not ctl: raise SystemExit('agtermctl is unavailable')
    try:
        mode = subprocess.check_output([ctl, 'restore', 'mode'], text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as exc:
        raise SystemExit('Cannot establish restore mode. No config changed.\n'+exc.output)
    if 'live' in mode.lower():
        raise SystemExit('Live restore is enabled. No change made: keep the shell-based integration until a compatible startup hook is validated.')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name('ghostty.conf.backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        backup.write_bytes(path.read_bytes())
    path.write_text(base.rstrip()+'\n'+block)
    print('Applied. Reload agterm config and test one new session. Existing sessions are unchanged.'); return 0


if __name__ == '__main__':
    raise SystemExit(main())
