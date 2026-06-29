"""Behavioural tests for cc_lint.stats.StatsCollector."""

# pylint: disable=disallowed-name,invalid-name,attribute-defined-outside-init
import unittest
from typing import Any, cast

from httplint.message import HttpResponseLinter
from httplint.note import Note, categories, levels

from cc_lint.hll import hll_estimate
from cc_lint.stats import StatsCollector


def _attach_note(linter: HttpResponseLinter, note: Note) -> None:
    """Push a Note instance directly onto the linter's notes list.

    HttpResponseLinter exposes notes via a Protocol that doesn't promise the
    underlying list interface; the runtime object is a UserList. Cast to Any
    so this test helper isn't tied to the protocol shape.
    """
    cast(Any, linter.notes).data.append(note)


class _WarnNote(Note):
    category = categories.GENERAL
    level = levels.WARN
    summary = ""
    text = ""


class _InfoNote(Note):
    category = categories.GENERAL
    level = levels.INFO
    summary = ""
    text = ""


def _linter_for(url: str) -> HttpResponseLinter:
    linter = HttpResponseLinter()
    linter.base_uri = url
    return linter


class TestStatsCollectorAccumulation(unittest.TestCase):
    def test_warn_and_info_notes_both_captured(self) -> None:
        # As of Phase 6 the collector captures every fired note level
        # (BAD/WARN/INFO/GOOD); the report decides which to surface.
        stats = StatsCollector()
        for note_cls, url in [
            (_WarnNote, "http://a.example/"),
            (_WarnNote, "http://b.example/"),
            (_InfoNote, "http://c.example/"),
        ]:
            linter = _linter_for(url)
            note: Any = note_cls("s")
            _attach_note(linter, note)
            stats.process_linter(linter)
        self.assertEqual(stats.total_responses, 3)
        self.assertEqual(stats.note_data[_WarnNote.__name__]["count"], 2)
        self.assertEqual(stats.note_data[_InfoNote.__name__]["count"], 1)
        # Per-response severity rollup: 2 responses bucketed warn, 1 info.
        self.assertEqual(stats.severity_counts.get("warn"), 2)
        self.assertEqual(stats.severity_counts.get("info"), 1)

    def test_clean_response_buckets_to_clean(self) -> None:
        # A response with no warn/bad/info/good notes still increments the
        # 'clean' bucket so the population totals add to total_responses.
        stats = StatsCollector()
        linter = _linter_for("http://nothing.example/")
        stats.process_linter(linter)
        self.assertEqual(stats.total_responses, 1)
        self.assertEqual(stats.severity_counts.get("clean"), 1)

    def test_samples_deduped_by_site(self) -> None:
        stats = StatsCollector()
        for url in [
            "http://example.com/a",
            "http://www.example.com/b",  # same site after www-strip
            "http://other.com/c",
        ]:
            linter = _linter_for(url)
            _attach_note(linter, _WarnNote("s"))
            stats.process_linter(linter)
        samples = stats.note_data[_WarnNote.__name__]["samples"]
        sites = {s.get("site") for s in samples}
        self.assertEqual(sites, {"example.com", "other.com"})

    def test_sample_sites_gate_blocks_other_sites(self) -> None:
        # Only "big.example" is in the sample ceiling; small.example responses
        # still contribute to counts but not to the sample list.
        stats = StatsCollector(sample_sites={"big.example"})
        for url in [
            "http://big.example/x",
            "http://small.example/y",
            "http://small.example/z",
        ]:
            linter = _linter_for(url)
            _attach_note(linter, _WarnNote("s"))
            stats.process_linter(linter)
        data = stats.note_data[_WarnNote.__name__]
        self.assertEqual(data["count"], 3)
        sites = {s.get("site") for s in data["samples"]}
        self.assertEqual(sites, {"big.example"})

    def test_per_note_hll_grows_with_distinct_sites(self) -> None:
        stats = StatsCollector()
        for i in range(50):
            linter = _linter_for(f"http://site-{i}.example/")
            _attach_note(linter, _WarnNote("s"))
            stats.process_linter(linter)
        note = stats.note_data[_WarnNote.__name__]
        self.assertIn("sites_hll", note)
        # Coarse-signal HLL: just verify the estimate is in the same order of
        # magnitude as 50.
        estimate = hll_estimate(note["sites_hll"])
        self.assertGreater(estimate, 20)
        self.assertLess(estimate, 200)

    def test_csp_size_recorded_per_site_max(self) -> None:
        # Three responses, two sites:
        # a.example sees a small CSP then a big one -> stored as the big one
        # b.example sees no CSP -> stored as 0
        stats = StatsCollector()
        big_csp = (
            b"default-src 'self'; script-src 'self' https://*.cdn.example; "
            b"frame-ancestors 'none'"
        )
        for url, csp_value in [
            ("http://a.example/", b"default-src 'self'"),
            ("http://a.example/2", big_csp),
            ("http://b.example/", None),
        ]:
            linter = _linter_for(url)
            linter.process_response_topline(b"HTTP/1.1", b"200", b"OK")
            headers = []
            if csp_value is not None:
                headers.append((b"content-security-policy", csp_value))
            linter.process_headers(headers)
            linter.finish_content(True)
            stats.process_linter(linter)
        self.assertIn("a.example", stats.csp_max_by_site)
        self.assertIn("b.example", stats.csp_max_by_site)
        # The max of two CSP sizes from a.example should be the larger one.
        self.assertGreater(stats.csp_max_by_site["a.example"], 20)
        self.assertEqual(stats.csp_max_by_site["b.example"], 0)

    def test_subnotes_are_counted(self) -> None:
        # httplint attaches strength/quality findings as children via
        # Note.add_child (appended to note.subnotes), not to linter.notes.
        # The collector must recurse into them; otherwise the entire
        # strength layer (CSP_UNSAFE_INLINE, HSTS_NO_SUBDOMAINS, ...) is
        # silently dropped. Regression test for #5 / #2.
        stats = StatsCollector()
        linter = _linter_for("http://a.example/")
        parent: Any = _InfoNote("s")
        parent.add_child(_WarnNote)
        _attach_note(linter, parent)
        stats.process_linter(linter)
        self.assertEqual(stats.note_data[_InfoNote.__name__]["count"], 1)
        self.assertEqual(stats.note_data[_WarnNote.__name__]["count"], 1)
        # The child's WARN should bump the response's max severity above the
        # parent's INFO.
        self.assertEqual(stats.severity_counts.get("warn"), 1)
        self.assertIsNone(stats.severity_counts.get("info"))

    def test_nested_subnotes_are_counted(self) -> None:
        # add_child can be called on a child, producing grandchildren; the
        # walk is depth-first, not single-level.
        stats = StatsCollector()
        linter = _linter_for("http://a.example/")
        parent: Any = _InfoNote("s")
        child = parent.add_child(_InfoNote)
        child.add_child(_WarnNote)
        _attach_note(linter, parent)
        stats.process_linter(linter)
        self.assertEqual(stats.note_data[_InfoNote.__name__]["count"], 2)
        self.assertEqual(stats.note_data[_WarnNote.__name__]["count"], 1)

    def test_hsts_and_csp_strength_subnotes_fire(self) -> None:
        # End-to-end over real headers: a weak HSTS (no includeSubDomains,
        # short max-age) and a CSP with 'unsafe-inline' must surface their
        # strength sub-notes, which httplint emits as subnotes of HSTS_VALID
        # and CONTENT_SECURITY_POLICY. Regression test for #5 / #2.
        stats = StatsCollector()
        linter = _linter_for("https://example.com/")
        linter.process_response_topline(b"HTTP/1.1", b"200", b"OK")
        linter.process_headers(
            [
                (b"strict-transport-security", b"max-age=600"),
                (b"content-security-policy", b"script-src 'unsafe-inline'"),
            ]
        )
        linter.finish_content(True)
        stats.process_linter(linter)
        for note_id in (
            "HSTS_NO_SUBDOMAINS",
            "HSTS_SHORT_MAX_AGE",
            "CSP_UNSAFE_INLINE",
        ):
            self.assertIn(note_id, stats.note_data, f"{note_id} was dropped")
            self.assertEqual(stats.note_data[note_id]["count"], 1)

    def test_to_dict_carries_sites_hll_and_note_data(self) -> None:
        stats = StatsCollector()
        linter = _linter_for("http://a.example/")
        _attach_note(linter, _WarnNote("s"))
        stats.process_linter(linter)
        data = stats.to_dict()
        self.assertEqual(data["total_responses"], 1)
        self.assertIn("sites_hll", data)
        self.assertIn(_WarnNote.__name__, data["notes"])


if __name__ == "__main__":
    unittest.main()
