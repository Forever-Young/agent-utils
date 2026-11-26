"""Release notes come from CHANGELOG.md."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release', ROOT / 'scripts' / 'release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

SAMPLE = """# Changelog

## [0.2.0] - 2026-01-02

### Added
- Second.

## [0.1.0] - 2026-01-01

- First.
"""


class ChangelogTests(unittest.TestCase):
    def root(self, text):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        if text is not None:
            (Path(d.name) / 'CHANGELOG.md').write_text(text)
        return Path(d.name)

    def test_section_of_one_version(self):
        root = self.root(SAMPLE)
        self.assertEqual(release.changelog_notes('0.2.0', root), '### Added\n- Second.\n')
        self.assertEqual(release.changelog_notes('0.1.0', root), '- First.\n')

    def test_missing_section_or_file_stops_the_release(self):
        for text in (SAMPLE, None, '## [0.3.0]\n\n## [0.2.0]\n- x\n'):
            with self.assertRaises(SystemExit):
                release.changelog_notes('0.3.0', self.root(text))

    def test_notes_link_the_previous_release(self):
        root = self.root(SAMPLE)
        notes = release.release_notes('0.2.0', 'v0.1.0', 'o/r', root)
        self.assertIn('- Second.', notes)
        self.assertIn('https://github.com/o/r/compare/v0.1.0...v0.2.0', notes)
        self.assertNotIn('compare', release.release_notes('0.2.0', None, 'o/r', root))

    def test_current_version_has_a_section(self):
        self.assertTrue(release.changelog_notes(release.version_of(ROOT), ROOT).strip())


if __name__ == '__main__':
    unittest.main()
