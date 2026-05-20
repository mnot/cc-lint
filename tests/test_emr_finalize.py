"""Tests for cc_lint.emr.finalize part-* assembly and merge."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from typing import Any, List, Tuple

from cc_lint.emr.finalize import merge_results


def _write_part(path: str, lines: List[Tuple[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as part_file:
        for key, value in lines:
            part_file.write(json.dumps(key) + "\t" + json.dumps(value) + "\n")


class TestMergeResults(unittest.TestCase):
    def test_globals_and_notes_merged(self) -> None:
        with tempfile.TemporaryDirectory() as results:
            _write_part(
                os.path.join(results, "part-00000"),
                [
                    (
                        "globals",
                        {
                            "total_responses": 5,
                            "field_counts": {"h": 5},
                            "unprocessed_counts": {},
                            "run_context": {"crawl_id": "CC-A"},
                        },
                    ),
                    (
                        "note:BAD_SYNTAX",
                        {"count": 2, "samples": [], "vars": {}},
                    ),
                ],
            )
            _write_part(
                os.path.join(results, "part-00001"),
                [
                    (
                        "globals",
                        {
                            "total_responses": 3,
                            "field_counts": {"k": 2},
                            "unprocessed_counts": {},
                        },
                    ),
                    (
                        "note:BAD_SYNTAX",
                        {"count": 1, "samples": [], "vars": {}},
                    ),
                    (
                        "note:CC_DUP",
                        {"count": 7, "samples": [], "vars": {}},
                    ),
                ],
            )
            merged = merge_results(results)
            self.assertEqual(merged["total_responses"], 8)
            self.assertEqual(
                merged["field_counts"], {"h": 5, "k": 2}
            )
            self.assertEqual(merged["notes"]["BAD_SYNTAX"]["count"], 3)
            self.assertEqual(merged["notes"]["CC_DUP"]["count"], 7)
            self.assertEqual(merged["run_context"]["crawl_id"], "CC-A")
            self.assertIn("finalized_at", merged)

    def test_empty_dir_raises(self) -> None:
        with tempfile.TemporaryDirectory() as results:
            with self.assertRaises(SystemExit):
                merge_results(results)

    def test_skips_malformed_lines(self) -> None:
        with tempfile.TemporaryDirectory() as results:
            with open(
                os.path.join(results, "part-00000"), "w", encoding="utf-8"
            ) as part_file:
                part_file.write("not-json-no-tab\n")
                part_file.write("\n")  # blank
                part_file.write(
                    json.dumps("globals")
                    + "\t"
                    + json.dumps(
                        {
                            "total_responses": 1,
                            "field_counts": {},
                            "unprocessed_counts": {},
                        }
                    )
                    + "\n"
                )
            merged = merge_results(results)
            self.assertEqual(merged["total_responses"], 1)


class TestFinalizeCli(unittest.TestCase):
    def test_writes_html_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as results:
            _write_part(
                os.path.join(results, "part-00000"),
                [
                    (
                        "globals",
                        {
                            "total_responses": 3,
                            "field_counts": {"content-type": 3},
                            "unprocessed_counts": {},
                        },
                    ),
                    (
                        "note:BAD_SYNTAX",
                        {"count": 1, "samples": [], "vars": {}},
                    ),
                ],
            )
            html_path = os.path.join(results, "report.html")
            md_path = os.path.join(results, "report.md")
            stats_json_path = os.path.join(results, "stats.json")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "cc_lint.emr.finalize",
                    results,
                    html_path,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(os.path.exists(html_path))
            self.assertTrue(os.path.exists(md_path))
            # stats.json should NOT be persisted unless requested.
            self.assertFalse(os.path.exists(stats_json_path))

    def test_stats_json_writes_dump(self) -> None:
        with tempfile.TemporaryDirectory() as results:
            _write_part(
                os.path.join(results, "part-00000"),
                [
                    (
                        "globals",
                        {
                            "total_responses": 1,
                            "field_counts": {},
                            "unprocessed_counts": {},
                        },
                    ),
                ],
            )
            html_path = os.path.join(results, "report.html")
            dump_path = os.path.join(results, "debug.json")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "cc_lint.emr.finalize",
                    results,
                    html_path,
                    "--stats-json",
                    dump_path,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(os.path.exists(dump_path))
            with open(dump_path, "r", encoding="utf-8") as dump_file:
                data = json.load(dump_file)
            self.assertEqual(data["total_responses"], 1)


if __name__ == "__main__":
    unittest.main()
