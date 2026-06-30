"""Smoke tests for cc_lint.report's HTML + Markdown renderer."""

import os
import re
import tempfile
import unittest
from typing import Any, Dict

from cc_lint.hll import HLL_P_GLOBAL, HLL_P_PER_NOTE, hll_add, make_registers
from cc_lint.report import render_report
from cc_lint.report.markdown import _md_inline_code
from cc_lint.report.severity import (
    build_severity_index,
    classify_unseen,
    possible_note_ids,
)

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
            "samples": [
                {"url": "http://c.example/", "vars": {"directive": "no-cache"}}
            ],
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

    def test_toc_navigation(self) -> None:
        html = self._render(SAMPLE_STATS)
        # The nav, a top-level section link, a nested note-category link, and
        # the scrollspy script should all be present, and every TOC anchor
        # must point at an id that actually exists in the document.
        self.assertIn('<nav class="toc"', html)
        self.assertIn('<a href="#notes">Notes</a>', html)
        self.assertIn('class="toc-sub"', html)
        self.assertIn("IntersectionObserver", html)
        for anchor in re.findall(r'<a href="#([^"]+)">', html):
            if anchor.startswith(("http", "//")):
                continue
            self.assertIn(f'id="{anchor}"', html, f"TOC anchor #{anchor} has no target")

    def test_infra_section(self) -> None:
        data = {
            "total_responses": 100,
            "notes": {
                "FIELD_DEPRECATED": {
                    "count": 30,
                    "samples": [],
                    "vars": {},
                    "by_layer": {"cloudflare": 20, "nginx": 10},
                }
            },
            "field_counts": {"server": 100, "x-xss-protection": 30},
            "layer_counts": {"cloudflare": 60, "nginx": 25, "__unmatched__": 15},
            "field_counts_by_layer": {
                "x-xss-protection": {"cloudflare": 20, "nginx": 10},
                "server": {"cloudflare": 60, "nginx": 25},
            },
            "asn_counts": {"13335": 50, "16509": 20},
        }
        html, md = self._render_both(data)
        # Section + coverage line + a layer row + role label.
        self.assertIn('id="infrastructure"', html)
        self.assertIn("Fingerprinted 85.0% of responses", html)
        self.assertIn("cloudflare", html)
        self.assertIn("Headers by infrastructure", html)
        # Per-note breakdown rendered on the note card, including the
        # "of layer's traffic" rate: cloudflare fired 20× over its 60
        # responses = 33.3%, nginx 10× over 25 = 40.0%.
        self.assertIn("By infrastructure", html)
        self.assertIn("Of layer's traffic", html)
        self.assertIn("<td>33.3%</td>", html)
        self.assertIn("<td>40.0%</td>", html)
        # Top-ASN section: AS number, operator label (issue #28), header.
        self.assertIn('id="asn"', html)
        self.assertIn("AS13335", html)
        self.assertIn("Top networks (ASN)", html)
        self.assertIn("<th>Operator</th>", html)
        self.assertIn("Amazon (AWS)", html)  # AS16509 -> operator label
        # Markdown parity.
        self.assertIn("## Infrastructure", md)
        self.assertIn("matched no known layer", md)
        self.assertIn("By infrastructure (fires, % of this note, ", md)
        self.assertIn("cloudflare (20, 67%, 33.3%)", md)
        self.assertIn("nginx (10, 33%, 40.0%)", md)
        self.assertIn("## Top networks (ASN)", md)
        self.assertIn("AS13335", md)
        self.assertIn("| Operator |", md)
        self.assertIn("Amazon (AWS)", md)

    def test_census_section(self) -> None:
        data = {
            "total_responses": 1000,
            "notes": {},
            "field_counts": {"content-type": 1000},
            "unprocessed_counts": {
                "cf-ray": 900,
                "x-amz-cf-id": 700,
                "x-forwarded-for": 230,
                "x-acme-novel": 12,
            },
            "field_bytes": {"cf-ray": 27000, "x-amz-cf-id": 35000},
        }
        html, md = self._render_both(data)
        # Section heading + vendor attribution + class labels, both formats.
        self.assertIn('id="header-census"', html)
        self.assertIn("Non-Standard Header Census", html)
        self.assertIn("cloudflare", html)
        self.assertIn("cloudfront", html)
        self.assertIn("By inferred vendor", html)
        self.assertIn("de-facto", html)  # x-forwarded-for is well-known
        self.assertIn("## Non-Standard Header Census", md)
        self.assertIn("By inferred vendor", md)
        self.assertIn("cf-ray", md)

    def test_infra_truncation_notes_sync(self) -> None:
        # When the per-layer / ASN tables were trimmed during shuffle, BOTH
        # the HTML and Markdown reports must footnote the elision (CLAUDE.md
        # HTML/Markdown sync rule).
        data = {
            "total_responses": 100,
            "notes": {},
            "field_counts": {"server": 100},
            "layer_counts": {"cloudflare": 60, "__unmatched__": 40},
            "field_counts_by_layer": {"server": {"cloudflare": 60}},
            "asn_counts": {"13335": 60},
            "truncated_field_counts_by_layer": True,
            "truncated_asn_counts": True,
        }
        html, md = self._render_both(data)
        # HTML uses the shared TRUNCATED_NOTE; Markdown its elision line.
        self.assertIn("long tail", html.lower())
        self.assertEqual(md.lower().count("long tail elided"), 2)

    def test_unseen_notes_bucketed(self) -> None:
        html = self._render(SAMPLE_STATS)
        # The three subgroup headings should be present (each one is a
        # subsection of the Unseen Notes section).
        self.assertIn("Reachable but not triggered", html)
        self.assertIn("Body-only", html)
        self.assertIn("Request-only", html)
        # A representative request-only and body-only note should be routed to
        # its own bucket and surface in the rendered page. Derive the names
        # from the installed httplint wheel rather than pinning literals, so an
        # upstream rename doesn't break this test (CLAUDE.md httplint-pin note).
        possible = possible_note_ids(build_severity_index())
        seen = set(SAMPLE_STATS.get("notes", {}).keys())
        _reachable, request_only, body_only = classify_unseen(possible, seen)
        self.assertTrue(request_only, "no request-only note in installed httplint")
        self.assertTrue(body_only, "no body-only note in installed httplint")
        self.assertIn(request_only[0], html)
        self.assertIn(body_only[0], html)

    def test_unseen_caveat_on_small_run(self) -> None:
        # A small run (SAMPLE_STATS has 1,234 responses) must carry the
        # sample-size caveat so the Unseen list isn't misread (issue #28).
        html, md = self._render_both(SAMPLE_STATS)
        self.assertIn("too few responses", html)
        self.assertIn("too few responses", md)

    def test_unseen_caveat_absent_on_full_run(self) -> None:
        data = dict(SAMPLE_STATS)
        data["total_responses"] = 123_000_000
        html, md = self._render_both(data)
        self.assertNotIn("too few responses", html)
        self.assertNotIn("too few responses", md)

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
        # The warning appears at least once per affected section (top headers
        # by count, the non-standard header census, and per-note vars).
        self.assertGreaterEqual(html.count("long tail"), 3)
        self.assertIn('class="muted truncated"', html)

    def test_per_field_samples(self) -> None:
        # A field_name-keyed note carries per-field var_samples; each field row
        # in the field_name breakdown table should attach that field's sample
        # URLs and captured header values.
        data = {
            "total_responses": 100,
            "field_counts": {"cross-origin-embedder-policy": 50, "via": 50},
            "unprocessed_counts": {},
            "notes": {
                "STRUCTURED_FIELD_PARSE_ERROR": {
                    "count": 12,
                    "samples": [],
                    "vars": {
                        "field_name": {
                            "cross-origin-embedder-policy": 8,
                            "via": 4,
                        },
                    },
                    "var_samples": {
                        "field_name": {
                            "cross-origin-embedder-policy": [
                                {
                                    "url": "http://coep.example/",
                                    "vars": {"field_values": "['require-corp; foo']"},
                                }
                            ],
                            "via": [{"url": "http://via.example/", "vars": {}}],
                        }
                    },
                }
            },
        }
        html, md = self._render_both(data)
        self.assertIn("field-samples", html)
        # COEP sample URL and its captured malformed value both surface.
        self.assertIn("http://coep.example/", html)
        self.assertIn("require-corp; foo", html)
        # The via sample (no captured value) still renders its URL.
        self.assertIn("http://via.example/", html)
        # Markdown carries the same per-field samples (LLM-facing parity),
        # including the per-value sample count HTML shows as "Samples (N)".
        self.assertIn("Samples by value:", md)
        self.assertIn("Samples (1)", md)
        self.assertIn("http://coep.example/", md)
        self.assertIn("require-corp; foo", md)
        self.assertIn("http://via.example/", md)

    def test_field_error_grouping_renders_in_both(self) -> None:
        # The field_error var carries "field: error" composite keys. Both
        # renderers must group them by field (CLAUDE.md HTML/Markdown sync),
        # not show the raw composite strings.
        data = {
            "total_responses": 100,
            "field_counts": {},
            "unprocessed_counts": {},
            "notes": {
                "STRUCTURED_FIELD_PARSE_ERROR": {
                    "count": 9,
                    "samples": [],
                    "vars": {
                        "field_error": {
                            "cache-control: bad token": 5,
                            "cache-control: trailing comma": 2,
                            "via: bad token": 2,
                        },
                    },
                }
            },
        }
        html, md = self._render_both(data)
        # HTML groups by field with a per-field total in the row header.
        self.assertIn("field-error", html)
        # Markdown must group too: the field name appears once with both of
        # its errors, and the raw composite key must NOT appear verbatim.
        self.assertIn("| cache-control |", md)
        self.assertIn("bad token (5)", md)
        self.assertIn("trailing comma (2)", md)
        self.assertNotIn("cache-control: bad token", md)

    def test_md_inline_code_survives_backticks(self) -> None:
        # No backticks: single-backtick span.
        self.assertEqual(_md_inline_code("require-corp"), "`require-corp`")
        # Embedded backtick: fence must be longer than the longest run, so
        # the value's backtick can't terminate the span early.
        self.assertEqual(_md_inline_code("a`b"), "``a`b``")
        self.assertEqual(_md_inline_code("a``b"), "```a``b```")
        # Leading/trailing backtick needs space padding (CommonMark).
        self.assertEqual(_md_inline_code("`x`"), "`` `x` ``")

    def test_captured_value_with_backtick_renders_safely(self) -> None:
        data = {
            "total_responses": 10,
            "field_counts": {"via": 10},
            "unprocessed_counts": {},
            "notes": {
                "STRUCTURED_FIELD_PARSE_ERROR": {
                    "count": 1,
                    "samples": [],
                    "vars": {"field_name": {"via": 1}},
                    "var_samples": {
                        "field_name": {
                            "via": [
                                {
                                    "url": "http://bt.example/",
                                    "vars": {"field_values": "weird`value"},
                                }
                            ]
                        }
                    },
                }
            },
        }
        _, md = self._render_both(data)
        # The value surfaces inside a multi-backtick fence, not a broken span.
        self.assertIn("``weird`value``", md)

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
        self.assertIn("sites (40.0%)", html)
        self.assertIn(
            'class="visually-hidden"> (HyperLogLog estimate of '
            "distinct sites where this note fired)</span>",
            html,
        )

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

    def test_header_bytes_section_renders(self) -> None:
        data = dict(SAMPLE_STATS)
        data["field_bytes"] = {
            "set-cookie": 4_000_000,
            "content-security-policy": 2_500_000,
            "content-type": 40_000,
            "pragma": 18_000,
            "cf-ray": 1_200_000,
        }
        data["header_block_hist"] = {"<256 B": 120, "256-1023 B": 540, "1-4 KB": 280}
        data["total_header_bytes"] = 9_200_000
        html, md = self._render_both(data)
        self.assertIn("Header byte economics", html)
        self.assertIn("Header byte economics", md)
        # The category table is byte-valued, so it must use byte-size
        # formatting in BOTH renderers (regression: HTML used _format_count,
        # printing a raw 2,500,000 under a "Bytes" column). 2.4 MB == 2.5e6 B.
        self.assertIn("2.4 MB", html)
        self.assertIn("2.4 MB", md)
        # pragma is deprecated, cf-ray is proprietary -> both categories appear.
        self.assertIn("Deprecated", html)
        self.assertIn("Proprietary", html)
        # Mean headline renders before the first subsection in both.
        self.assertIn("Mean header block", html)
        self.assertIn("Mean header block", md)

    def test_no_header_bytes_section_absent(self) -> None:
        html, md = self._render_both(dict(SAMPLE_STATS))
        self.assertNotIn("Header byte economics", html)
        self.assertNotIn("Header byte economics", md)

    def test_value_histograms_render(self) -> None:
        data = dict(SAMPLE_STATS)
        data["value_histograms"] = {
            "cache_control_max_age": {"0": 5, "1-24 hours": 20, ">10 years": 1},
            "hsts_max_age": {"1-10 years": 8, "negative": 1},
        }
        html, md = self._render_both(data)
        self.assertIn("Numeric header value distributions", html)
        self.assertIn("Cache-Control: max-age", html)
        self.assertIn("Strict-Transport-Security: max-age", html)
        self.assertIn("&gt;10 years", html)  # bucket label HTML-escaped
        # Same content in markdown, with the histograms as subsections.
        self.assertIn("## Numeric header value distributions", md)
        self.assertIn("### Cache-Control: max-age", md)
        self.assertIn("| 1-24 hours |", md)

    def test_no_value_histograms_section_absent(self) -> None:
        html, md = self._render_both(dict(SAMPLE_STATS))
        self.assertNotIn("Numeric header value distributions", html)
        self.assertNotIn("Numeric header value distributions", md)

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

    def test_vary_section_rendered(self) -> None:
        data: Dict[str, Any] = {
            "total_responses": 100,
            "notes": {},
            "vary": {
                "responses_with_vary": 10,
                "recipes": {
                    "occ": {
                        "accept-encoding, cookie, x-ab-bucket": 6,
                        "user-agent": 4,
                    },
                    "hlls": {},
                },
                "marginals": {
                    "occ": {
                        "accept-encoding": 6,
                        "cookie": 6,
                        "x-ab-bucket": 6,
                        "user-agent": 4,
                    },
                    "hlls": {},
                },
            },
        }
        html, md = self._render_both(data)
        self.assertIn('id="vary"', html)
        self.assertIn("Vary composition", html)
        self.assertIn("## Vary composition", md)
        # Synthetic token flagged in HTML; recipe surfaces in both.
        self.assertIn("vary-synthetic", html)
        self.assertIn("x-ab-bucket", html)
        self.assertIn("Accept-Encoding factored out", html)
        self.assertIn("Non-standard Vary tokens", md)
        self.assertIn("x-ab-bucket", md)

    def test_no_vary_section_absent(self) -> None:
        html = self._render({"total_responses": 5, "notes": {}})
        self.assertNotIn('id="vary"', html)

    def test_high_interest_axes_sorted_by_frequency(self) -> None:
        # The high-interest axes table sorts by occurrence, not configured
        # order (cookie, accept-language, accept-encoding, user-agent). #28.
        data: Dict[str, Any] = {
            "total_responses": 100,
            "notes": {},
            "vary": {
                "responses_with_vary": 100,
                "recipes": {"occ": {"accept-encoding": 90}, "hlls": {}},
                "marginals": {
                    "occ": {
                        "accept-encoding": 90,
                        "user-agent": 50,
                        "cookie": 30,
                        "accept-language": 10,
                    },
                    "hlls": {},
                },
            },
        }
        _html, md = self._render_both(data)
        section = md.split("### High-interest axes", 1)[1].split("###", 1)[0]
        positions = [
            section.index(axis)
            for axis in ("accept-encoding", "user-agent", "cookie", "accept-language")
        ]
        self.assertEqual(positions, sorted(positions))

    def test_cache_control_section_rendered(self) -> None:
        data: Dict[str, Any] = {
            "total_responses": 100,
            "notes": {},
            "cache_control": {
                "responses_with_cc": 10,
                "recipes": {
                    "occ": {
                        "max-age=N, must-revalidate, no-cache, no-store, private": 6,
                        "max-age=N, surrogate-control=N": 4,
                    },
                    "hlls": {},
                },
                "marginals": {
                    "occ": {
                        "max-age": 10,
                        "no-store": 6,
                        "private": 6,
                        "surrogate-control": 4,
                    },
                    "hlls": {},
                },
            },
        }
        html, md = self._render_both(data)
        self.assertIn('id="cache-control"', html)
        self.assertIn("Cache-Control recipes", html)
        self.assertIn("## Cache-Control recipes", md)
        # The kitchen-sink recipe surfaces in both views.
        self.assertIn("no-store", html)
        self.assertIn("no-store", md)
        # Non-standard directive flagged in HTML and listed in markdown.
        self.assertIn("cc-synthetic", html)
        self.assertIn("surrogate-control", html)
        self.assertIn("Non-standard directives", md)
        self.assertIn("surrogate-control", md)

    def test_no_cache_control_section_absent(self) -> None:
        html = self._render({"total_responses": 5, "notes": {}})
        self.assertNotIn('id="cache-control"', html)

    def test_cooccur_section_rendered(self) -> None:
        bundle = "strict-transport-security, x-content-type-options"
        data: Dict[str, Any] = {
            "total_responses": 100,
            "notes": {},
            "cooccur": {
                "responses": 100,
                "bundles": {
                    "occ": {bundle: 60, "(none)": 30, "x-content-type-options": 10},
                    "hlls": {},
                },
                "marginals": {
                    "occ": {
                        "strict-transport-security": 60,
                        "x-content-type-options": 70,
                    },
                    "hlls": {},
                },
                "pairs": {"occ": {bundle: 60}, "hlls": {}},
                "by_layer": {bundle: {"cloudflare": 60}, "(none)": {"nginx": 30}},
            },
        }
        html, md = self._render_both(data)
        self.assertIn('id="cooccur"', html)
        self.assertIn("Header co-occurrence", html)
        self.assertIn("## Header co-occurrence", md)
        # The no-security-headers headline and the modal-by-layer view appear.
        self.assertIn("no</strong> security header", html)
        self.assertIn("Default header set by infrastructure", html)
        self.assertIn("Default header set by infrastructure", md)
        # The conditional-lift table surfaces the bundled pair in both.
        self.assertIn("Conditional lifts", html)
        self.assertIn("strict-transport-security", md)

    def test_no_cooccur_section_absent(self) -> None:
        html = self._render({"total_responses": 5, "notes": {}})
        self.assertNotIn('id="cooccur"', html)

    def test_note_cooccur_section_rendered(self) -> None:
        cluster = "CC_CONFLICTING, FIELD_DEPRECATED"
        data: Dict[str, Any] = {
            "total_responses": 100,
            "notes": {},
            "note_cooccur": {
                "responses": 100,
                "bundles": {
                    "occ": {cluster: 40, "(none)": 50, "CC_CONFLICTING": 10},
                    "hlls": {},
                },
                "marginals": {
                    "occ": {"CC_CONFLICTING": 50, "FIELD_DEPRECATED": 40},
                    "hlls": {},
                },
                "pairs": {"occ": {cluster: 40}, "hlls": {}},
            },
        }
        html, md = self._render_both(data)
        self.assertIn('id="note-cooccur"', html)
        self.assertIn("Finding co-occurrence", html)
        self.assertIn("## Finding co-occurrence", md)
        # The no-defect headline and the finding-clusters view appear.
        self.assertIn("no</strong> <code>bad</code>", html)
        self.assertIn("Top finding clusters", html)
        self.assertIn("Top finding clusters", md)
        # The conditional-lift table surfaces the co-occurring pair in both.
        self.assertIn("Conditional lifts", html)
        self.assertIn("Finding A", html)
        self.assertIn("CC_CONFLICTING", md)

    def test_no_note_cooccur_section_absent(self) -> None:
        html = self._render({"total_responses": 5, "notes": {}})
        self.assertNotIn('id="note-cooccur"', html)

    def test_url_escaping(self) -> None:
        bad = {
            "total_responses": 1,
            "notes": {
                "BAD_SYNTAX": {
                    "count": 1,
                    "samples": [
                        {"url": "http://x/?<script>alert(1)</script>", "vars": {}}
                    ],
                    "vars": {},
                }
            },
        }
        html = self._render(bad)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
