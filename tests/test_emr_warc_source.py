"""Tests for cc_lint.emr.warc_source path helpers."""

import unittest

from cc_lint.emr.warc_source import warc_path_to_wat


class TestWarcPathToWat(unittest.TestCase):
    def test_rewrites_warc_to_wat(self) -> None:
        warc = (
            "crawl-data/CC-MAIN-2024-18/segments/1712296943562.50/"
            "warc/CC-MAIN-20240420232712-20240421022712-00000.warc.gz"
        )
        wat = (
            "crawl-data/CC-MAIN-2024-18/segments/1712296943562.50/"
            "wat/CC-MAIN-20240420232712-20240421022712-00000.warc.wat.gz"
        )
        self.assertEqual(warc_path_to_wat(warc), wat)

    def test_idempotent_on_wat_path(self) -> None:
        wat = (
            "crawl-data/CC-MAIN-2024-18/segments/x/"
            "wat/CC-MAIN-1234.warc.wat.gz"
        )
        # Should not double-rewrite.
        self.assertEqual(warc_path_to_wat(wat), wat)

    def test_unrelated_path_unchanged(self) -> None:
        self.assertEqual(warc_path_to_wat("just/a/file.txt"), "just/a/file.txt")


if __name__ == "__main__":
    unittest.main()
