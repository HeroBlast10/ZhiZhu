import tempfile
import unittest
from pathlib import Path

from merge_md import merge


class MergeTests(unittest.TestCase):
    def test_output_inside_source_is_never_merged_into_itself(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a.md").write_text("# A\n", encoding="utf-8")
            output = root / "merged.md"

            merge(root, output)
            merge(root, output)

            content = output.read_text(encoding="utf-8")
            self.assertEqual(content.count("# A"), 1)
            self.assertIn("> 共 1 篇", content)

    def test_non_directory_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.md"
            source.write_text("# A", encoding="utf-8")
            with self.assertRaises(NotADirectoryError):
                merge(source, Path(temp_dir) / "out.md")


if __name__ == "__main__":
    unittest.main()
