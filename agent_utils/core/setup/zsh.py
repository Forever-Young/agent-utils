"""Opt-in zsh startup hook. Preview by default; preserve unrelated shell settings."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil

SOURCE = Path(__file__).resolve().parent/'agterm-startup.zsh'
START = '# BEGIN agterm-utils zsh startup'
END = '# END agterm-utils zsh startup'


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--home', type=Path, default=Path.home())
    a = p.parse_args(argv); home = a.home.expanduser()
    helper = home/'.bin/agterm-tools/agterm-startup.zsh'
    rc = home/'.zshrc'; text = rc.read_text() if rc.exists() else ''
    base = re.sub(re.escape(START)+r'.*?'+re.escape(END)+r'\n?', '', text, flags=re.S)
    block = START+'\n[[ -r "$HOME/.bin/agterm-tools/agterm-startup.zsh" ]] && source "$HOME/.bin/agterm-tools/agterm-startup.zsh"\n'+END+'\n'
    # Prefix so existing early returns cannot skip the hook registration.
    updated = block+'\n'+base.lstrip('\n')
    ghost = home/'.config/agterm/ghostty.conf'
    gtext = ghost.read_text() if ghost.exists() else ''
    gnew = re.sub(r'\n?# BEGIN agterm-utils startup.*?# END agterm-utils startup\n?', '', gtext, flags=re.S)
    if re.search(r'^\s*command\s*=.*agents\s+agterm\s+startup', gnew, re.M):
        raise SystemExit('Unmanaged startup command exists in ghostty.conf; review it first.')
    print('Install:', helper); print('Update:', rc)
    if gtext != gnew: print('Remove previous managed default-command block:', ghost)
    print(block)
    if not a.apply: return 0
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    for target in [rc, helper]+([ghost] if gtext != gnew else []):
        if target.exists(): shutil.copy2(target, target.with_name(target.name+'.backup-'+stamp))
    helper.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(SOURCE, helper)
    rc.write_text(updated)
    if gtext != gnew: ghost.write_text(gnew)
    print('Applied. Test a new agterm session; existing shells are unchanged.'); return 0


if __name__ == '__main__':
    raise SystemExit(main())
