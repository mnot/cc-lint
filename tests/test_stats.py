"""Behavioural tests for cc_lint.stats.StatsCollector."""

# pylint: disable=disallowed-name,invalid-name,attribute-defined-outside-init
import ipaddress
import unittest
from typing import Any, cast

from httplint.message import HttpResponseLinter
from httplint.note import Note, categories, levels

from cc_lint.hll import hll_estimate
from cc_lint.ipasn import IpAsnTable
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


def _linter_with_headers(
    url: str, headers: list[tuple[str, str]]
) -> HttpResponseLinter:
    linter = HttpResponseLinter(no_content=True)
    linter.base_uri = url
    linter.process_response_topline(b"HTTP/1.1", b"200", b"OK")
    linter.process_headers(
        [(name.encode("latin1"), value.encode("latin1")) for name, value in headers]
    )
    linter.finish_content(True)
    return linter


class TestFingerprintAccumulation(unittest.TestCase):
    def test_layer_counts_and_fields(self) -> None:
        stats = StatsCollector()
        # Cloudflare in front of nginx, plus a bare unmatched server.
        stats.process_linter(
            _linter_with_headers(
                "http://a.example/",
                [
                    ("cf-ray", "abc-LHR"),
                    ("server", "nginx"),
                    ("x-xss-protection", "0"),
                ],
            )
        )
        stats.process_linter(
            _linter_with_headers("http://b.example/", [("server", "CoolServer/9")])
        )
        d = stats.to_dict()
        # Layers overlap: response a counts under both cloudflare and nginx.
        self.assertEqual(d["layer_counts"].get("cloudflare"), 1)
        self.assertEqual(d["layer_counts"].get("nginx"), 1)
        # Response b matched nothing.
        self.assertEqual(d["layer_counts"].get("__unmatched__"), 1)
        # The motivating slice: x-xss-protection attributed to the layer(s)
        # of the response that carried it.
        self.assertEqual(
            d["field_counts_by_layer"]["x-xss-protection"],
            {"cloudflare": 1, "nginx": 1},
        )

    def test_note_by_layer(self) -> None:
        stats = StatsCollector()
        # x-xss-protection is deprecated; httplint fires FIELD_DEPRECATED,
        # which should carry a by_layer breakdown.
        stats.process_linter(
            _linter_with_headers(
                "http://a.example/",
                [("cf-ray", "z"), ("x-xss-protection", "1; mode=block")],
            )
        )
        d = stats.to_dict()
        layered = [nid for nid, nd in d["notes"].items() if nd.get("by_layer")]
        self.assertTrue(layered, "expected at least one note with a by_layer map")
        for nid in layered:
            self.assertIn("cloudflare", d["notes"][nid]["by_layer"])

    def test_asn_match_and_counts(self) -> None:
        # An IP that resolves to Cloudflare's ASN should match the cloudflare
        # layer even with no Cloudflare header, and feed the ASN histogram.
        table = IpAsnTable()
        table.add(int(ipaddress.ip_address("203.0.113.0")), 24, 4, 13335)
        table.finalize()
        stats = StatsCollector(ipasn=table)
        linter = _linter_with_headers("http://a.example/", [("server", "Apache")])
        setattr(linter, "ip_address", "203.0.113.5")
        stats.process_linter(linter)
        d = stats.to_dict()
        # cloudflare (by ASN) and apache (by header) both present.
        self.assertEqual(d["layer_counts"].get("cloudflare"), 1)
        self.assertEqual(d["layer_counts"].get("apache"), 1)
        self.assertEqual(d["asn_counts"], {"13335": 1})

    def test_asn_unresolved_is_unmatched(self) -> None:
        table = IpAsnTable()
        table.add(int(ipaddress.ip_address("203.0.113.0")), 24, 4, 13335)
        table.finalize()
        stats = StatsCollector(ipasn=table)
        linter = _linter_with_headers("http://a.example/", [("x-foo", "bar")])
        setattr(linter, "ip_address", "198.51.100.7")  # not in the table
        stats.process_linter(linter)
        d = stats.to_dict()
        self.assertEqual(d["layer_counts"].get("__unmatched__"), 1)
        self.assertEqual(d["asn_counts"], {})


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
