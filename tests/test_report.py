"""Smoke tests for cc_lint.report's HTML renderer."""

import json
import os
import tempfile
import unittest
from typing import Any, Dict

from cc_lint.report import generate_report


SAMPLE_STATS = {
    "total_responses": 1234,
    "field_counts": {"content-type": 1234, "x-custom": 7, "server": 1100},
    "unprocessed_counts": {"x-cdn-cache": 42, "x-frame-options-foo": 3},
    "notes": {
        "BAD_SYNTAX": {
            "count": 9,
            "samples": [
                {"url": "http://a.example/", "vars": {"field_name": "link"}},
                {"url": "http://b.example/", "vars": {"field_name": "via"}},
            ],
            "vars": {"field_name": {"link": 6, "via": 3}},
            "var_samples": {
                "field_name": {
                    "link": [{"url": "http://a.example/", "vars": {}}],
                    "via": [{"url": "http://b.example/", "vars": {}}],
                }
            },
        },
        "CC_DUP": {
            "count": 4,
            "samples": [{"url": "http://c.example/", "vars": {"directive": "no-cache"}}],
            "vars": {"directive": {"no-cache": 4}},
        },
    },
}


class TestRenderer(unittest.TestCase):
    def _render(self, data: Dict[str, Any]) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            stats_path = os.path.join(tmp, "stats.json")
            html_path = os.path.join(tmp, "report.html")
            with open(stats_path, "w", encoding="utf-8") as stats_file:
                json.dump(data, stats_file)
            generate_report(stats_path, html_path)
            with open(html_path, "r", encoding="utf-8") as html_file:
                return html_file.read()

    def test_renders_with_full_data(self) -> None:
        html = self._render(SAMPLE_STATS)
        self.assertIn("<!doctype html>", html)
        self.assertIn("1,234", html)  # total_responses formatting
        self.assertIn("BAD_SYNTAX", html)
        self.assertIn("CC_DUP", html)
        self.assertIn("badge-bad", html)  # BAD_SYNTAX is severity BAD
        self.assertIn("http://a.example/", html)  # sample URL
        self.assertIn("redbot.org/check", html)  # redbot link
        self.assertIn("content-type", html)  # field counts surface somewhere

    def test_renders_empty_stats(self) -> None:
        html = self._render({"total_responses": 0, "notes": {}})
        self.assertIn("<!doctype html>", html)
        self.assertIn("Responses analyzed", html)

    def test_unseen_notes_bucketed(self) -> None:
        html = self._render(SAMPLE_STATS)
        # The three subgroup headings should be present (each one is a
        # subsection of the Unseen Notes section).
        self.assertIn("Reachable but not triggered", html)
        self.assertIn("Body-only", html)
        self.assertIn("Request-only", html)
        # Specific request-only and body-only notes should not appear in the
        # reachable bucket -- they should be classified into their own buckets.
        self.assertIn("MISSING_USER_AGENT", html)
        self.assertIn("CHARSET_MISMATCH", html)

    def test_truncation_flags_show(self) -> None:
        data = {
            "total_responses": 10,
            "field_counts": {"content-type": 10},
            "unprocessed_counts": {"x-cdn-cache": 5},
            "_truncated_field_counts": True,
            "_truncated_unprocessed_counts": True,
            "notes": {
                "BAD_SYNTAX": {
                    "count": 5,
                    "samples": [],
                    "vars": {"field_name": {"link": 5}},
                    "_truncated_vars": {"field_name": True},
                }
            },
        }
        html = self._render(data)
        # The warning appears at least once per affected section (headers,
        # unprocessed, and per-note vars).
        self.assertGreaterEqual(html.count("long tail"), 3)
        self.assertIn("class=\"muted truncated\"", html)

    def test_url_escaping(self) -> None:
        bad = {
            "total_responses": 1,
            "notes": {
                "BAD_SYNTAX": {
                    "count": 1,
                    "samples": [{"url": "http://x/?<script>alert(1)</script>", "vars": {}}],
                    "vars": {},
                }
            },
        }
        html = self._render(bad)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
