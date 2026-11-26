#!/usr/bin/env python3
"""Cut a release: tarball of the committed HEAD, GitHub release, Homebrew tap formula.

Preview by default. With --apply it
  1. builds dist/agent-utils-VERSION.tar.gz with `git archive` (VERSION is `version` in pyproject.toml),
  2. tags vVERSION on HEAD and creates the GitHub release with that tarball as its asset
     (`gh release create`; the tag is pushed by gh). The release notes are the VERSION section of
     CHANGELOG.md plus a link comparing it with the previous release; no such section, no release,
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


def changelog_notes(version, root=ROOT):
    """The `## [VERSION]` section of CHANGELOG.md (heading excluded), or SystemExit when there is none."""
    path = root / 'CHANGELOG.md'
    text = path.read_text() if path.exists() else ''
    match = re.search(r'^## \[%s\][^\n]*\n(.*?)(?=^## \[|\Z)' % re.escape(version), text, re.M | re.S)
    if not match or not match.group(1).strip():
        raise SystemExit('CHANGELOG.md has no section "## [%s]". Add one before releasing.' % version)
    return match.group(1).strip() + '\n'


def release_notes(version, previous, repo=REPO, root=ROOT):
    notes = changelog_notes(version, root)
    if previous:
        notes += '\n**Full changelog**: https://github.com/%s/compare/%s...v%s\n' % (repo, previous, version)
    return notes


def previous_tag(repo, tag):
    """Tag of the newest release other than `tag`, or None (no releases, or gh not available)."""
    out = subprocess.run(['gh', 'release', 'list', '--repo', repo, '--limit', '5', '--json', 'tagName', '-q', '.[].tagName'],
                         capture_output=True, text=True)
    tags = [t for t in out.stdout.split() if t != tag] if out.returncode == 0 else []
    return tags[0] if tags else None


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
    changelog_notes(version)  # fail early, also in the preview
    if not a.apply:
        print('Notes: CHANGELOG.md section %s' % version)
        print('Preview only. Add --apply to build, tag, release and write the formulas.')
        return 0
    if subprocess.run(['gh', 'release', 'view', tag, '--repo', a.repo], capture_output=True).returncode == 0:
        raise SystemExit('Release %s exists on %s. Raise `version` in pyproject.toml for a new release.' % (tag, a.repo))
    tarball, sha = build(version, a.out_dir)
    print('Tarball:', tarball, sha)
    notes = a.out_dir / ('release-notes-%s.md' % version)
    notes.write_text(release_notes(version, previous_tag(a.repo, tag), a.repo))
    run('gh', 'release', 'create', tag, str(tarball), '--repo', a.repo, '--target', run('git', 'rev-parse', 'HEAD', cwd=ROOT, capture_output=True).stdout.strip(),
        '--title', 'agent-utils ' + version, '--notes-file', str(notes))
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
