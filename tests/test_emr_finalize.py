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
            self.assertEqual(merged["field_counts"], {"h": 5, "k": 2})
            self.assertEqual(merged["notes"]["BAD_SYNTAX"]["count"], 3)
            self.assertEqual(merged["notes"]["CC_DUP"]["count"], 7)
            self.assertEqual(merged["run_context"]["crawl_id"], "CC-A")
            self.assertIn("finalized_at", merged)

    def test_empty_dir_raises(self) -> None:
        with tempfile.TemporaryDirectory() as results:
            with self.assertRaises(SystemExit):
                merge_results(results)

    def test_csp_sizes_merge_max(self) -> None:
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
                    ("csp_sizes", {"a.example": 100, "b.example": 0}),
                ],
            )
            _write_part(
                os.path.join(results, "part-00001"),
                [
                    ("csp_sizes", {"a.example": 500, "c.example": 300}),
                ],
            )
            merged = merge_results(results)
            self.assertEqual(
                merged["csp_max_by_site"],
                {"a.example": 500, "b.example": 0, "c.example": 300},
            )

    def test_value_histograms_merged(self) -> None:
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
                    ("value_histograms", {"age": {"1-10 min": 2, "0": 1}}),
                ],
            )
            _write_part(
                os.path.join(results, "part-00001"),
                [
                    (
                        "value_histograms",
                        {"age": {"1-10 min": 3}, "hsts_max_age": {"1-10 years": 4}},
                    ),
                ],
            )
            merged = merge_results(results)
            self.assertEqual(
                merged["value_histograms"],
                {
                    "age": {"1-10 min": 5, "0": 1},
                    "hsts_max_age": {"1-10 years": 4},
                },
            )

    def test_severity_counts_summed(self) -> None:
        with tempfile.TemporaryDirectory() as results:
            _write_part(
                os.path.join(results, "part-00000"),
                [
                    (
                        "globals",
                        {
                            "total_responses": 100,
                            "field_counts": {},
                            "unprocessed_counts": {},
                            "severity_counts": {"bad": 5, "warn": 30, "clean": 65},
                        },
                    ),
                ],
            )
            _write_part(
                os.path.join(results, "part-00001"),
                [
                    (
                        "globals",
                        {
                            "total_responses": 50,
                            "field_counts": {},
                            "unprocessed_counts": {},
                            "severity_counts": {"warn": 10, "info": 20, "clean": 20},
                        },
                    ),
                ],
            )
            merged = merge_results(results)
            self.assertEqual(merged["total_responses"], 150)
            self.assertEqual(
                merged["severity_counts"],
                {"bad": 5, "warn": 40, "info": 20, "clean": 85},
            )

    def test_vary_blocks_merged(self) -> None:
        with tempfile.TemporaryDirectory() as results:
            _write_part(
                os.path.join(results, "part-00000"),
                [
                    (
                        "globals",
                        {
                            "total_responses": 5,
                            "field_counts": {},
                            "unprocessed_counts": {},
                        },
                    ),
                    (
                        "vary",
                        {
                            "responses_with_vary": 3,
                            "recipes": {"occ": {"accept-encoding": 3}, "hlls": {}},
                            "marginals": {
                                "occ": {"accept-encoding": 3},
                                "hlls": {},
                            },
                        },
                    ),
                ],
            )
            _write_part(
                os.path.join(results, "part-00001"),
                [
                    (
                        "vary",
                        {
                            "responses_with_vary": 2,
                            "recipes": {
                                "occ": {"accept-encoding": 1, "cookie": 1},
                                "hlls": {},
                            },
                            "marginals": {
                                "occ": {"accept-encoding": 1, "cookie": 1},
                                "hlls": {},
                            },
                        },
                    ),
                ],
            )
            merged = merge_results(results)
            self.assertEqual(merged["vary"]["responses_with_vary"], 5)
            self.assertEqual(
                merged["vary"]["recipes"]["occ"],
                {"accept-encoding": 4, "cookie": 1},
            )

    def test_cache_control_blocks_merged(self) -> None:
        with tempfile.TemporaryDirectory() as results:
            _write_part(
                os.path.join(results, "part-00000"),
                [
                    (
                        "globals",
                        {
                            "total_responses": 5,
                            "field_counts": {},
                            "unprocessed_counts": {},
                        },
                    ),
                    (
                        "cache_control",
                        {
                            "responses_with_cc": 3,
                            "recipes": {"occ": {"max-age=N, public": 3}, "hlls": {}},
                            "marginals": {
                                "occ": {"max-age": 3, "public": 3},
                                "hlls": {},
                            },
                        },
                    ),
                ],
            )
            _write_part(
                os.path.join(results, "part-00001"),
                [
                    (
                        "cache_control",
                        {
                            "responses_with_cc": 2,
                            "recipes": {
                                "occ": {"max-age=N, public": 1, "no-store": 1},
                                "hlls": {},
                            },
                            "marginals": {
                                "occ": {"max-age": 1, "public": 1, "no-store": 1},
                                "hlls": {},
                            },
                        },
                    ),
                ],
            )
            merged = merge_results(results)
            self.assertEqual(merged["cache_control"]["responses_with_cc"], 5)
            self.assertEqual(
                merged["cache_control"]["recipes"]["occ"],
                {"max-age=N, public": 4, "no-store": 1},
            )

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
            # stats.json is no longer emitted; finalize writes only the
            # rendered report files.
            self.assertFalse(os.path.exists(stats_json_path))


if __name__ == "__main__":
    unittest.main()
