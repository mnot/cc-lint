"""Tests for cc_lint.top_sites helpers."""

import os
import tempfile
import unittest

from cc_lint.top_sites import is_in_top_sites, load_top_sites, normalize_site


class TestNormalizeSite(unittest.TestCase):
    def test_https_url(self) -> None:
        self.assertEqual(normalize_site("https://example.com/path"), "example.com")

    def test_strips_leading_www(self) -> None:
        self.assertEqual(normalize_site("http://www.Example.COM/"), "example.com")

    def test_hostname_only(self) -> None:
        self.assertEqual(normalize_site("example.com"), "example.com")

    def test_hostname_with_www_only(self) -> None:
        self.assertEqual(normalize_site("WWW.example.com"), "example.com")

    def test_url_with_port(self) -> None:
        self.assertEqual(normalize_site("http://example.com:8080/"), "example.com")

    def test_url_with_query_fragment(self) -> None:
        self.assertEqual(
            normalize_site("https://example.com/a?b=c#d"), "example.com"
        )

    def test_ipv6_url(self) -> None:
        # urlparse extracts the IPv6 host without brackets and lowercases it.
        self.assertEqual(
            normalize_site("http://[2001:db8::1]/foo"), "2001:db8::1"
        )

    def test_none_input(self) -> None:
        self.assertIsNone(normalize_site(None))

    def test_empty_input(self) -> None:
        self.assertIsNone(normalize_site(""))

    def test_url_with_empty_host(self) -> None:
        # urlparse on "http://" yields no hostname; the function returns None.
        self.assertIsNone(normalize_site("http://"))

    def test_keeps_subdomains(self) -> None:
        self.assertEqual(
            normalize_site("https://blog.example.com/"), "blog.example.com"
        )

    def test_idn_host_punycoded(self) -> None:
        # Non-ASCII hosts are encoded to the punycode form Tranco lists.
        self.assertEqual(
            normalize_site("https://bücher.example/"), "xn--bcher-kva.example"
        )

    def test_idn_host_with_www(self) -> None:
        self.assertEqual(
            normalize_site("http://www.münchen.de/"), "xn--mnchen-3ya.de"
        )

    def test_idn_hostname_only(self) -> None:
        self.assertEqual(normalize_site("例え.テスト"), "xn--r8jz45g.xn--zckzah")

    def test_idn2008_sharp_s_not_mapped_to_ss(self) -> None:
        # IDNA2008/UTS-46 (what registries and Tranco use) keeps ß; the stdlib
        # IDNA2003 codec would wrongly map faß.de -> fass.de and miss Tranco.
        self.assertEqual(normalize_site("https://faß.de/"), "xn--fa-hia.de")

    def test_already_punycode_unchanged(self) -> None:
        self.assertEqual(
            normalize_site("xn--bcher-kva.example"), "xn--bcher-kva.example"
        )


class TestIsInTopSites(unittest.TestCase):
    def test_match(self) -> None:
        self.assertTrue(is_in_top_sites("http://example.com/", {"example.com"}))

    def test_www_normalized_match(self) -> None:
        self.assertTrue(is_in_top_sites("http://www.example.com/", {"example.com"}))

    def test_miss(self) -> None:
        self.assertFalse(is_in_top_sites("http://other.com/", {"example.com"}))

    def test_unparseable_input_is_false(self) -> None:
        self.assertFalse(is_in_top_sites("", {"example.com"}))

    def test_idn_match_against_punycode_set(self) -> None:
        self.assertTrue(
            is_in_top_sites("https://bücher.example/", {"xn--bcher-kva.example"})
        )


class TestLoadTopSites(unittest.TestCase):
    def test_reads_rank_domain_lines(self) -> None:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False
        ) as csv_file:
            csv_file.write("1,first.com\n2,second.com\n3,third.com\n")
            path = csv_file.name
        try:
            sites = load_top_sites(path, limit=2)
            self.assertEqual(sites, {"first.com", "second.com"})
        finally:
            os.unlink(path)

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(load_top_sites("/no/such/file.csv", limit=10), set())


if __name__ == "__main__":
    unittest.main()
