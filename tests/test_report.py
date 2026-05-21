"""Smoke tests for cc_lint.report's HTML + Markdown renderer."""

import os
import tempfile
import unittest
from typing import Any, Dict

from cc_lint.hll import HLL_P_GLOBAL, HLL_P_PER_NOTE, hll_add, make_registers
from cc_lint.report import render_report


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
            html_path = os.path.join(tmp, "report.html")
            render_report(data, html_path)
            with open(html_path, "r", encoding="utf-8") as html_file:
                return html_file.read()

    def _render_both(self, data: Dict[str, Any]) -> tuple[str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            md_path = os.path.join(tmp, "report.md")
            render_report(data, html_path)
            with open(html_path, "r", encoding="utf-8") as html_file:
                html_text = html_file.read()
            with open(md_path, "r", encoding="utf-8") as md_file:
                md_text = md_file.read()
            return html_text, md_text

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
        self.assertIn("BAD_GZIP", html)

    def test_truncation_flags_show(self) -> None:
        data = {
            "total_responses": 10,
            "field_counts": {"content-type": 10},
            "unprocessed_counts": {"x-cdn-cache": 5},
            "truncated_field_counts": True,
            "truncated_unprocessed_counts": True,
            "notes": {
                "BAD_SYNTAX": {
                    "count": 5,
                    "samples": [],
                    "vars": {"field_name": {"link": 5}},
                    "truncated_vars": {"field_name": True},
                }
            },
        }
        html = self._render(data)
        # The warning appears at least once per affected section (headers,
        # unprocessed, and per-note vars).
        self.assertGreaterEqual(html.count("long tail"), 3)
        self.assertIn("class=\"muted truncated\"", html)

    def test_sites_hll_surfaces(self) -> None:
        global_hll = make_registers(HLL_P_GLOBAL)
        for i in range(50):
            hll_add(global_hll, HLL_P_GLOBAL, f"site-{i}.example")
        note_hll = make_registers(HLL_P_PER_NOTE)
        for i in range(20):
            hll_add(note_hll, HLL_P_PER_NOTE, f"site-{i}.example")
        data = {
            "total_responses": 100,
            "sites_hll": global_hll,
            "field_counts": {},
            "unprocessed_counts": {},
            "notes": {
                "BAD_SYNTAX": {
                    "count": 30,
                    "samples": [],
                    "vars": {},
                    "sites_hll": note_hll,
                }
            },
        }
        html = self._render(data)
        self.assertIn("Distinct sites analyzed", html)
        self.assertIn("HLL estimate", html)
        self.assertIn("note-sites", html)
        self.assertIn("sites (40.0%)</span>", html)

    def test_run_context_pills_render(self) -> None:
        data = {
            "total_responses": 100,
            "field_counts": {},
            "unprocessed_counts": {},
            "notes": {},
            "run_context": {
                "crawl_id": "CC-MAIN-2026-12",
                "top_sites": 50000,
                "sample_top_sites": 10000,
                "record_limit": 0,
                "warc_limit": 0,
                "warc_timeout_s": 900,
                "cc_lint_version": "0.0.1",
            },
            "finalized_at": "2026-05-21T12:34:56Z",
        }
        html = self._render(data)
        self.assertIn("CC-MAIN-2026-12", html)
        self.assertIn("Tranco top 50,000", html)
        self.assertIn("Tranco top 10,000", html)
        self.assertIn("v0.0.1", html)
        self.assertIn("2026-05-21T12:34:56Z", html)
        self.assertIn("Percentages describe this Common Crawl result set", html)

    def test_markdown_sibling_written(self) -> None:
        html, md = self._render_both(SAMPLE_STATS)
        self.assertIn("<!doctype html>", html)
        # Markdown should contain the report heading, a severity tag for each
        # note, and the same note ids as the HTML.
        self.assertIn("# Common Crawl Response Lint", md)
        self.assertIn("`WARN`", md)  # both BAD_SYNTAX and CC_DUP are WARN
        self.assertIn("BAD_SYNTAX", md)
        self.assertIn("CC_DUP", md)
        self.assertIn("Top Response Headers", md)
        # Tables should have separator rows.
        self.assertIn("| --- |", md)

    def test_markdown_run_context_block(self) -> None:
        data = dict(SAMPLE_STATS)
        data["run_context"] = {
            "crawl_id": "CC-MAIN-2026-12",
            "top_sites": 50000,
            "sample_top_sites": 10000,
            "record_limit": 0,
            "warc_limit": 0,
            "warc_timeout_s": 900,
            "cc_lint_version": "0.0.1",
        }
        data["finalized_at"] = "2026-05-21T12:34:56Z"
        _, md = self._render_both(data)
        self.assertIn("**Crawl:** CC-MAIN-2026-12", md)
        self.assertIn("**cc-lint:** v0.0.1", md)
        self.assertIn("Tranco top 50,000", md)
        self.assertIn("Tranco top 10,000", md)
        self.assertIn("2026-05-21T12:34:56Z", md)

    def test_csp_histogram_renders(self) -> None:
        data = dict(SAMPLE_STATS)
        data["csp_max_by_site"] = {
            "no-csp.example": 0,
            "tiny.example": 50,
            "small.example": 200,
            "small2.example": 450,
            "medium.example": 1200,
            "big.example": 4500,
            "huge.example": 15000,
        }
        html, md = self._render_both(data)
        self.assertIn("Content-Security-Policy size by site", html)
        self.assertIn("No CSP header", html)
        self.assertIn("100-499 B", html)
        self.assertIn("10000+ B", html)
        self.assertIn("csp-bar", html)
        # Same content in markdown.
        self.assertIn("Content-Security-Policy size by site", md)
        self.assertIn("| 1-99 B |", md)

    def test_health_summary_renders(self) -> None:
        data = dict(SAMPLE_STATS)
        data["severity_counts"] = {
            "bad": 30,
            "warn": 200,
            "info": 400,
            "good": 50,
            "clean": 320,
        }
        html, md = self._render_both(data)
        self.assertIn("Response health", html)
        self.assertIn("health-bar", html)
        self.assertIn("badge-clean", html)
        self.assertIn("Response health", md)
        self.assertIn("`BAD`", md)
        self.assertIn("`Clean`", md)

    def test_category_grouping(self) -> None:
        html, md = self._render_both(SAMPLE_STATS)
        # BAD_SYNTAX and CC_DUP are both real httplint notes that have a
        # category attribute (GENERAL and CACHING respectively). The report
        # should surface category section headings.
        self.assertIn("Findings by category", html)
        self.assertIn("note-category", html)
        # CC_DUP belongs to httplint's CACHING category.
        self.assertIn("Caching", html)
        # Markdown should contain a category overview table and a per-category
        # h3 heading.
        self.assertIn("Findings by category", md)
        self.assertIn("### Caching", md)

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
