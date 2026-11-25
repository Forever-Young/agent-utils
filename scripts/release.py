#!/usr/bin/env python3
"""Cut a release: tarball of the committed HEAD, GitHub release, Homebrew tap formula.

Preview by default. With --apply it
  1. builds dist/agent-utils-VERSION.tar.gz with `git archive` (VERSION is `version` in pyproject.toml),
  2. tags vVERSION on HEAD and creates the GitHub release with that tarball as its asset
     (`gh release create`; the tag is pushed by gh),
  3. rewrites url, version and sha256 in packaging/homebrew/agent-utils-tap.rb and in the tap
     checkout's Formula/agent-utils.rb. The tap is not committed or pushed; review it first.
The formula points at the uploaded asset, so its checksum is the checksum of the file users download.
"""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REPO = 'Forever-Young/agent-utils'
TAP = 'Forever-Young/tap'


def run(*cmd, **kw):
    return subprocess.run(cmd, check=True, text=True, **kw)


def version_of(root=ROOT):
    return re.search(r'^version\s*=\s*"([^"]+)"', (root / 'pyproject.toml').read_text(), re.M).group(1)


def asset_url(version, repo=REPO):
    return 'https://github.com/%s/releases/download/v%s/agent-utils-%s.tar.gz' % (repo, version, version)


def render(text, url, version, sha):
    """Point a formula at a release tarball; also accepts the older git-revision form."""
    text = re.sub(r'  url "[^"]*"(?:,\n\s+revision: "[^"]*")?\n', '  url "%s"\n' % url, text, count=1)
    text = re.sub(r'  version "[^"]*"\n', '  version "%s"\n' % version, text, count=1)
    text = re.sub(r'^  sha256 "[^"]*"\n', '', text, count=1, flags=re.M)
    return text.replace('  version "%s"\n' % version, '  version "%s"\n  sha256 "%s"\n' % (version, sha), 1)


def build(version, out_dir, root=ROOT):
    out_dir.mkdir(parents=True, exist_ok=True)
    tarball = out_dir / ('agent-utils-%s.tar.gz' % version)
    run('git', 'archive', '--format=tar.gz', '--prefix=agent-utils-%s/' % version, '-o', str(tarball), 'HEAD', cwd=root)
    return tarball, hashlib.sha256(tarball.read_bytes()).hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--apply', action='store_true', help='Tag, create the GitHub release and write the formulas.')
    p.add_argument('--repo', default=REPO, help='GitHub repository (default %(default)s).')
    p.add_argument('--out-dir', type=Path, default=ROOT / 'dist', help='Where the tarball is written (default dist/, ignored by git).')
    p.add_argument('--tap-dir', type=Path, help='Tap checkout (default: brew --repository %s).' % TAP)
    p.add_argument('--allow-dirty', action='store_true', help='Release HEAD even with uncommitted changes (they are not included).')
    a = p.parse_args(argv)
    dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if dirty and not a.allow_dirty:
        raise SystemExit('Uncommitted changes are not in the release. Commit first or pass --allow-dirty.')
    version = version_of()
    tag = 'v' + version
    url = asset_url(version, a.repo)
    print('Version:', version, '(from pyproject.toml)')
    print('Tag:', tag)
    print('Asset:', url)
    if not a.apply:
        print('Preview only. Add --apply to build, tag, release and write the formulas.')
        return 0
    if subprocess.run(['gh', 'release', 'view', tag, '--repo', a.repo], capture_output=True).returncode == 0:
        raise SystemExit('Release %s exists on %s. Raise `version` in pyproject.toml for a new release.' % (tag, a.repo))
    tarball, sha = build(version, a.out_dir)
    print('Tarball:', tarball, sha)
    run('gh', 'release', 'create', tag, str(tarball), '--repo', a.repo, '--target', run('git', 'rev-parse', 'HEAD', cwd=ROOT, capture_output=True).stdout.strip(),
        '--title', 'agent-utils ' + version, '--generate-notes')
    targets = [ROOT / 'packaging/homebrew/agent-utils-tap.rb']
    tap = a.tap_dir or Path(run('brew', '--repository', TAP, capture_output=True).stdout.strip())
    if (tap / 'Formula').is_dir():
        targets.append(tap / 'Formula/agent-utils.rb')
    for target in targets:
        target.write_text(render(target.read_text(), url, version, sha))
        print('Formula:', target)
    print('Commit packaging/homebrew/agent-utils-tap.rb here; review, commit and push the tap yourself.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
