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

    def test_cooccur_bundles_and_layers(self) -> None:
        stats = StatsCollector()
        # Two cloudflare responses carrying the same security bundle, plus a
        # bare response with no security header (the empty bundle).
        for site in ("a.example", "b.example"):
            stats.process_linter(
                _linter_with_headers(
                    f"http://{site}/",
                    [
                        ("cf-ray", "z"),
                        ("strict-transport-security", "max-age=1"),
                        ("x-content-type-options", "nosniff"),
                    ],
                )
            )
        stats.process_linter(
            _linter_with_headers("http://c.example/", [("server", "nginx")])
        )
        co = stats.to_dict()["cooccur"]
        self.assertEqual(co["responses"], 3)
        bundle = "strict-transport-security, x-content-type-options"
        self.assertEqual(co["bundles"]["occ"][bundle], 2)
        self.assertEqual(co["bundles"]["occ"]["(none)"], 1)
        # Pairs and marginals cover only the present headers.
        self.assertEqual(co["pairs"]["occ"][bundle], 2)
        self.assertEqual(co["marginals"]["occ"]["strict-transport-security"], 2)
        # The bundle is attributed to cloudflare; the empty bundle to nginx.
        self.assertEqual(co["by_layer"][bundle]["cloudflare"], 2)
        self.assertEqual(co["by_layer"]["(none)"].get("nginx"), 1)

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

    def test_vary_composition_collected(self) -> None:
        # accept-encoding,cookie on a.example (twice); user-agent on b.example;
        # * on c.example; no Vary on d.example.
        stats = StatsCollector()
        for url, vary in [
            ("http://a.example/1", b"Accept-Encoding, Cookie"),
            ("http://a.example/2", b"Cookie, Accept-Encoding"),  # same recipe
            ("http://b.example/", b"User-Agent"),
            ("http://c.example/", b"*"),
            ("http://d.example/", None),
        ]:
            linter = _linter_for(url)
            linter.process_response_topline(b"HTTP/1.1", b"200", b"OK")
            headers = [(b"vary", vary)] if vary is not None else []
            linter.process_headers(headers)
            linter.finish_content(True)
            stats.process_linter(linter)
        data = stats.to_dict()
        self.assertIn("vary", data)
        vary = data["vary"]
        self.assertEqual(vary["responses_with_vary"], 4)
        # Recipe key is sorted+deduped, so both a.example responses collapse.
        self.assertEqual(vary["recipes"]["occ"]["accept-encoding, cookie"], 2)
        self.assertEqual(vary["recipes"]["occ"]["user-agent"], 1)
        self.assertEqual(vary["recipes"]["occ"]["*"], 1)
        # Marginals count each field-name; the wildcard is excluded.
        self.assertEqual(vary["marginals"]["occ"]["cookie"], 2)
        self.assertEqual(vary["marginals"]["occ"]["accept-encoding"], 2)
        self.assertNotIn("*", vary["marginals"]["occ"])

    def test_no_vary_key_when_absent(self) -> None:
        stats = StatsCollector()
        linter = _linter_for("http://nothing.example/")
        linter.process_response_topline(b"HTTP/1.1", b"200", b"OK")
        linter.process_headers([(b"content-type", b"text/html")])
        linter.finish_content(True)
        stats.process_linter(linter)
        self.assertNotIn("vary", stats.to_dict())

    def test_cache_control_composition_collected(self) -> None:
        # The kitchen-sink recipe twice on a.example (different max-age values
        # collapse to one recipe); public,max-age on b.example; no CC elsewhere.
        stats = StatsCollector()
        for url, cc in [
            ("http://a.example/1", b"no-store, no-cache, max-age=0, private"),
            ("http://a.example/2", b"private, max-age=60, no-cache, no-store"),
            ("http://b.example/", b"public, max-age=3600"),
            ("http://d.example/", None),
        ]:
            linter = _linter_for(url)
            linter.process_response_topline(b"HTTP/1.1", b"200", b"OK")
            headers = [(b"cache-control", cc)] if cc is not None else []
            linter.process_headers(headers)
            linter.finish_content(True)
            stats.process_linter(linter)
        data = stats.to_dict()
        self.assertIn("cache_control", data)
        cc = data["cache_control"]
        self.assertEqual(cc["responses_with_cc"], 3)
        # Values collapse to =N, so both a.example responses share one recipe.
        self.assertEqual(
            cc["recipes"]["occ"]["max-age=N, no-cache, no-store, private"], 2
        )
        self.assertEqual(cc["recipes"]["occ"]["max-age=N, public"], 1)
        # Marginals count each directive once per response.
        self.assertEqual(cc["marginals"]["occ"]["max-age"], 3)
        self.assertEqual(cc["marginals"]["occ"]["no-store"], 2)
        self.assertEqual(cc["marginals"]["occ"]["public"], 1)

    def test_no_cache_control_key_when_absent(self) -> None:
        stats = StatsCollector()
        linter = _linter_for("http://nothing.example/")
        linter.process_response_topline(b"HTTP/1.1", b"200", b"OK")
        linter.process_headers([(b"content-type", b"text/html")])
        linter.finish_content(True)
        stats.process_linter(linter)
        self.assertNotIn("cache_control", stats.to_dict())

    def test_structured_field_parse_error_populates_field_error(self) -> None:
        # A malformed structured-field header fires STRUCTURED_FIELD_PARSE_ERROR,
        # whose vars are field_name/problem/context (not `error`). The derived
        # `field_error` var must compose `<field_name>: <problem>` so the
        # report's "Field → parse error" breakdown has data to render.
        stats = StatsCollector()
        linter = _linter_for("http://x.example/")
        linter.process_response_topline(b"HTTP/1.1", b"200", b"OK")
        # Accept-CH is an sf-list; trailing garbage makes it unparseable.
        linter.process_headers([(b"Accept-CH", b'"""')])
        linter.finish_content(True)
        stats.process_linter(linter)

        note = stats.note_data["STRUCTURED_FIELD_PARSE_ERROR"]
        field_error = note["vars"]["field_error"]
        self.assertEqual(len(field_error), 1)
        key = next(iter(field_error))
        self.assertTrue(key.startswith("Accept-CH: "))
        self.assertNotIn("\n", key)
        self.assertEqual(field_error[key], 1)

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


class TestValueHistograms(unittest.TestCase):
    """Corpus-wide numeric-header histograms (issue #8)."""

    def _linter(
        self, headers: list[tuple[str, str]], start_time: float = 1782728294.0
    ) -> HttpResponseLinter:
        # 1782728294 == Mon, 29 Jun 2026 10:18:14 GMT (the crawl reference
        # time). start_time must be set before the headers are processed: the
        # cache checker (which computes freshness) runs during finish_content.
        linter = HttpResponseLinter(no_content=True)
        linter.base_uri = "http://a.example/"
        linter.start_time = start_time
        linter.process_response_topline(b"HTTP/1.1", b"200", b"OK")
        linter.process_headers(
            [(n.encode("latin1"), v.encode("latin1")) for n, v in headers]
        )
        linter.finish_content(True)
        return linter

    def test_max_age_and_s_maxage_bucketed(self) -> None:
        stats = StatsCollector()
        stats.process_linter(
            self._linter([("Cache-Control", "max-age=3600, s-maxage=600")])
        )
        hist = stats.to_dict()["value_histograms"]
        self.assertEqual(hist["cache_control_max_age"], {"1-24 hours": 1})
        self.assertEqual(hist["cache_control_s_maxage"], {"10-60 min": 1})

    def test_zero_max_age_is_its_own_bucket(self) -> None:
        stats = StatsCollector()
        stats.process_linter(self._linter([("Cache-Control", "max-age=0")]))
        self.assertEqual(
            stats.to_dict()["value_histograms"]["cache_control_max_age"], {"0": 1}
        )

    def test_age_bucketed(self) -> None:
        stats = StatsCollector()
        stats.process_linter(self._linter([("Age", "120")]))
        self.assertEqual(stats.to_dict()["value_histograms"]["age"], {"1-10 min": 1})

    def test_hsts_max_age_bucketed(self) -> None:
        stats = StatsCollector()
        stats.process_linter(
            self._linter(
                [("Strict-Transport-Security", "max-age=31536000; preload")]
            )
        )
        self.assertEqual(
            stats.to_dict()["value_histograms"]["hsts_max_age"], {"1-10 years": 1}
        )

    def test_cookie_lifetime_max_age_and_expires(self) -> None:
        stats = StatsCollector()
        stats.process_linter(
            self._linter(
                [
                    ("Set-Cookie", "a=b; Max-Age=86400"),
                    ("Set-Cookie", "c=d; Expires=Wed, 09 Jun 2027 10:18:14 GMT"),
                    ("Set-Cookie", "sess=1"),  # session cookie: no lifetime
                ]
            )
        )
        # One cookie ~1 day (Max-Age), one ~345 days (Expires - crawl time);
        # the session cookie contributes nothing.
        self.assertEqual(
            stats.to_dict()["value_histograms"]["cookie_lifetime"],
            {"1-7 days": 1, "30-365 days": 1},
        )

    def test_expires_date_delta_and_freshness(self) -> None:
        stats = StatsCollector()
        stats.process_linter(
            self._linter(
                [
                    ("Date", "Mon, 29 Jun 2026 10:18:14 GMT"),
                    ("Expires", "Wed, 09 Jun 2027 10:18:14 GMT"),
                ]
            )
        )
        hist = stats.to_dict()["value_histograms"]
        self.assertEqual(hist["expires_date_delta"], {"30-365 days": 1})
        # Expires-only freshness: httplint derives the same ~345-day lifetime.
        self.assertEqual(hist["freshness_lifetime"], {"30-365 days": 1})

    def test_expires_before_date_is_negative_bucket(self) -> None:
        stats = StatsCollector()
        stats.process_linter(
            self._linter(
                [
                    ("Date", "Mon, 29 Jun 2026 10:18:14 GMT"),
                    ("Expires", "Sun, 28 Jun 2026 10:18:14 GMT"),
                ]
            )
        )
        self.assertEqual(
            stats.to_dict()["value_histograms"]["expires_date_delta"], {"negative": 1}
        )

    def test_absent_fields_yield_no_histograms(self) -> None:
        stats = StatsCollector()
        stats.process_linter(self._linter([("Server", "nginx")]))
        self.assertNotIn("value_histograms", stats.to_dict())

    def test_counts_accumulate_across_responses(self) -> None:
        stats = StatsCollector()
        for _ in range(3):
            stats.process_linter(self._linter([("Cache-Control", "max-age=3600")]))
        self.assertEqual(
            stats.to_dict()["value_histograms"]["cache_control_max_age"],
            {"1-24 hours": 3},
        )


if __name__ == "__main__":
    unittest.main()
