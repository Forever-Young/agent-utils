#!/usr/bin/env python3
"""Build and install agent-utils through Homebrew from the local repository only.

Preview by default. Builds a tarball of the committed HEAD (git archive), renders
packaging/homebrew/agent-utils.rb with a file:// URL into a local tap (local/agent-utils,
no remote), and with --apply runs brew install or upgrade. Nothing is published or pushed.
"""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TAP = 'local/agent-utils'


def run(*cmd, **kw):
    return subprocess.run(cmd, check=True, text=True, **kw)


def render(template, url, version, sha):
    return template.replace('@URL@', url).replace('@VERSION@', version).replace('@SHA256@', sha)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--cache-dir', type=Path, default=Path.home() / '.cache/agent-utils/brew')
    p.add_argument('--allow-dirty', action='store_true', help='Build from HEAD even with uncommitted changes (they are not included)')
    a = p.parse_args(argv)
    dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if dirty and not a.allow_dirty:
        raise SystemExit('Uncommitted changes are not in the build. Commit first or pass --allow-dirty.')
    head = run('git', 'rev-parse', '--short', 'HEAD', cwd=ROOT, capture_output=True).stdout.strip()
    base = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / 'pyproject.toml').read_text(), re.M).group(1)
    version = '%s-%s' % (base, head)
    tarball = a.cache_dir / ('agent-utils-%s.tar.gz' % version)
    print('Version:', version);print('Tarball:', tarball)
    print('Tap:', TAP, '(local, no remote)')
    if not a.apply:
        print('Preview only. Add --apply to build, write the formula and run brew install/upgrade.')
        return 0
    a.cache_dir.mkdir(parents=True, exist_ok=True)
    run('git', 'archive', '--format=tar.gz', '--prefix=agent-utils-%s/' % version, '-o', str(tarball), 'HEAD', cwd=ROOT)
    sha = hashlib.sha256(tarball.read_bytes()).hexdigest()
    taps = run('brew', 'tap', capture_output=True).stdout.split()
    if TAP not in taps:
        run('brew', 'tap-new', '--no-git', TAP)
    repo = Path(run('brew', '--repo', TAP, capture_output=True).stdout.strip())
    formula = repo / 'Formula/agent-utils.rb'
    formula.parent.mkdir(parents=True, exist_ok=True)
    formula.write_text(render((ROOT / 'packaging/homebrew/agent-utils.rb').read_text(), tarball.as_uri(), version, sha))
    print('Formula:', formula)
    installed = subprocess.run(['brew', 'list', '--formula', 'agent-utils'], capture_output=True).returncode == 0
    run('brew', 'reinstall' if installed else 'install', '--build-from-source', TAP + '/agent-utils')
    print('Installed. Verify: brew test agent-utils. Undo: brew uninstall agent-utils; brew untap', TAP)
    return 0


if __name__ == '__main__':
    sys.exit(main())
