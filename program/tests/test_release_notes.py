"""Release notes must belong only to the requested tag and preserve Markdown."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / '.github' / 'scripts' / 'release_notes.py'
spec = importlib.util.spec_from_file_location('release_notes', SCRIPT)
release_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_notes)


class ReleaseNotesTests(unittest.TestCase):
    def test_exact_tag_preserves_markdown_and_excludes_other_versions(self):
        changelog = '# Changelog\n\n## [Unreleased]\n\n- 未发布。\n\n## [v1.2.10] - 2026-11-17\n\n- 其他版本。\n\n## [v1.2.1] - 2026-11-16\n\n### 修复\n\n- **停止**与[说明](https://example.com)。\n\n## [v1.2.0]\n\n- 旧版。\n'
        self.assertEqual('### 修复\n\n- **停止**与[说明](https://example.com)。\n',
                         release_notes.extract_notes(changelog, 'v1.2.1'))

    def test_missing_duplicate_and_empty_versions_fail(self):
        for changelog in ('## [v1.2.10]\n- Other\n',
                          '## [v1.2.1]\n- First\n## [v1.2.1] - 2026-11-17\n- Second\n',
                          '## [v1.2.1]\n \n## [v1.2.0]\n- Older\n'):
            with self.subTest(changelog=changelog), self.assertRaises(ValueError):
                release_notes.extract_notes(changelog, 'v1.2.1')

    def test_heading_inside_fenced_example_does_not_end_section(self):
        for fence in ('```', '~~~~'):
            body = f'- Example\n\n{fence}markdown\n## [v2]\n{fence}\n\n- End\n'
            self.assertEqual(body, release_notes.extract_notes(f'## [v1]\n\n{body}\n## Other\nExcluded\n', 'v1'))

    def test_cli_reads_utf8_bom_and_writes_only_matching_body(self):
        with tempfile.TemporaryDirectory() as folder:
            changelog = Path(folder) / 'CHANGELOG.md'
            output = Path(folder) / 'notes.md'
            changelog.write_text('## [vTest]\n\n- 中文。\n', encoding='utf-8-sig')
            command = [sys.executable, str(SCRIPT), '--changelog', str(changelog), '--output', str(output)]
            result = subprocess.run(command + ['--tag', 'missing'], capture_output=True)
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(output.exists())
            result = subprocess.run(command + ['--tag', 'vTest'], capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual('- 中文。\n', output.read_text(encoding='utf-8'))

    def test_existing_release_tags_have_notes(self):
        changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
        for tag in ('v2026.11.15.2', 'v2026.11.15.1', 'v2026.11.14.1', 'v2026.11.14', 'v2026.11.13'):
            with self.subTest(tag=tag):
                self.assertTrue(release_notes.extract_notes(changelog, tag).strip())


if __name__ == '__main__':
    unittest.main()
