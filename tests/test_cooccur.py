"""Tests for cc_lint.cooccur normalisation, merging, trimming, derivation."""

import os
import tempfile
import unittest
from typing import Any, Dict

from cc_lint.cooccur import (
    EMPTY_BUNDLE_LABEL,
    bundle_key,
    conditional_lifts,
    default_security_headers,
    empty_bundle_count,
    layer_default_bundles,
    load_security_headers,
    merge_cooccur,
    pair_keys,
    present_security_headers,
    ranked_bundles,
    ranked_marginals,
    trim_cooccur,
)
from cc_lint.recipes import RecipeStats


class TestNormalisation(unittest.TestCase):
    def test_present_filters_dedupes_sorts(self) -> None:
        names = ["x-frame-options", "date", "x-frame-options", "strict-transport-security"]
        self.assertEqual(
            present_security_headers(names),
            ["strict-transport-security", "x-frame-options"],
        )

    def test_present_ignores_non_alphabet(self) -> None:
        self.assertEqual(present_security_headers(["server", "date", "cf-ray"]), [])

    def test_bundle_key_empty_is_label(self) -> None:
        self.assertEqual(bundle_key([]), EMPTY_BUNDLE_LABEL)

    def test_bundle_key_joins_sorted(self) -> None:
        self.assertEqual(
            bundle_key(["x-frame-options", "strict-transport-security"]),
            "strict-transport-security, x-frame-options",
        )

    def test_pair_keys_are_sorted_combinations(self) -> None:
        pairs = list(pair_keys(["a", "b", "c"]))
        self.assertEqual(pairs, ["a, b", "a, c", "b, c"])
        self.assertEqual(list(pair_keys(["a"])), [])


class TestMerge(unittest.TestCase):
    def test_merge_sums_and_flags(self) -> None:
        target: Dict[str, Any] = {
            "responses": 2,
            "bundles": {"occ": {"x-frame-options": 2}, "hlls": {}},
            "by_layer": {"x-frame-options": {"cloudflare": 2}},
            "bundles_truncated": True,
        }
        source: Dict[str, Any] = {
            "responses": 3,
            "bundles": {"occ": {"x-frame-options": 1, "(none)": 3}, "hlls": {}},
            "marginals": {"occ": {"x-frame-options": 1}, "hlls": {}},
            "by_layer": {
                "x-frame-options": {"cloudflare": 1, "nginx": 1},
                "(none)": {"nginx": 3},
            },
        }
        merge_cooccur(target, source)
        self.assertEqual(target["responses"], 5)
        self.assertEqual(target["bundles"]["occ"], {"x-frame-options": 3, "(none)": 3})
        self.assertEqual(target["marginals"]["occ"], {"x-frame-options": 1})
        self.assertEqual(
            target["by_layer"]["x-frame-options"], {"cloudflare": 3, "nginx": 1}
        )
        self.assertEqual(target["by_layer"]["(none)"], {"nginx": 3})
        self.assertTrue(target["bundles_truncated"])


class TestTrim(unittest.TestCase):
    def test_trims_and_prunes_by_layer(self) -> None:
        bundles = RecipeStats()
        for i in range(5):
            # higher i -> higher occurrence so the top-K keeps the high ones
            for _ in range(i + 1):
                bundles.add(f"b{i}", f"s{i}")
        cooccur: Dict[str, Any] = {
            "responses": 15,
            "bundles": bundles.to_dict(),
            "marginals": {"occ": {"x": 1}, "hlls": {}},
            "pairs": {"occ": {}, "hlls": {}},
            # by_layer references a bundle that will be trimmed away (b0) and
            # ones that survive.
            "by_layer": {
                "b0": {"nginx": 1},
                "b4": {"cloudflare": 5},
            },
        }
        trim_cooccur(cooccur, 2)
        self.assertEqual(set(cooccur["bundles"]["occ"]), {"b3", "b4"})
        self.assertTrue(cooccur["bundles_truncated"])
        # b0 was dropped from bundles, so it must be pruned from by_layer too.
        self.assertNotIn("b0", cooccur["by_layer"])
        self.assertIn("b4", cooccur["by_layer"])

    def test_below_cap_no_flag(self) -> None:
        cooccur: Dict[str, Any] = {
            "bundles": {"occ": {"a": 2, "b": 1}, "hlls": {}},
        }
        trim_cooccur(cooccur, 5)
        self.assertNotIn("bundles_truncated", cooccur)


class TestDerivation(unittest.TestCase):
    def _sample(self) -> Dict[str, Any]:
        return {
            "responses": 10,
            "bundles": {
                "occ": {
                    "strict-transport-security, x-content-type-options": 6,
                    EMPTY_BUNDLE_LABEL: 3,
                    "x-content-type-options": 1,
                },
                "hlls": {},
            },
            "marginals": {
                "occ": {"strict-transport-security": 6, "x-content-type-options": 7},
                "hlls": {},
            },
            "pairs": {
                "occ": {"strict-transport-security, x-content-type-options": 6},
                "hlls": {},
            },
            "by_layer": {
                "strict-transport-security, x-content-type-options": {
                    "cloudflare": 6
                },
                EMPTY_BUNDLE_LABEL: {"nginx": 2},
                "x-content-type-options": {"nginx": 1},
            },
        }

    def test_empty_bundle_count(self) -> None:
        self.assertEqual(empty_bundle_count(self._sample()), 3)

    def test_ranked_bundles_desc(self) -> None:
        ranked = ranked_bundles(self._sample())
        self.assertEqual(ranked[0][1], 6)
        self.assertEqual([c for _, c in ranked], [6, 3, 1])

    def test_ranked_marginals_desc(self) -> None:
        ranked = ranked_marginals(self._sample())
        self.assertEqual(ranked[0], ("x-content-type-options", 7))

    def test_conditional_lifts_math(self) -> None:
        lifts = conditional_lifts(self._sample(), 25)
        self.assertEqual(len(lifts), 1)
        row = lifts[0]
        self.assertEqual(row["a"], "strict-transport-security")
        self.assertEqual(row["b"], "x-content-type-options")
        self.assertEqual(row["joint"], 6)
        # P(a|b) = 6/7, P(b|a) = 6/6
        self.assertAlmostEqual(row["p_a_given_b"], 6 / 7)
        self.assertAlmostEqual(row["p_b_given_a"], 1.0)
        # lift = joint*responses / (na*nb) = 6*10 / (6*7)
        self.assertAlmostEqual(row["lift"], 60 / 42)

    def test_layer_default_bundles(self) -> None:
        defaults = layer_default_bundles(self._sample())
        by_layer = {row["layer"]: row for row in defaults}
        self.assertEqual(
            by_layer["cloudflare"]["bundle"],
            "strict-transport-security, x-content-type-options",
        )
        self.assertAlmostEqual(by_layer["cloudflare"]["share"], 1.0)
        # nginx saw two bundles: (none) x2 and x-content-type-options x1 ->
        # modal is (none) at 2/3.
        self.assertEqual(by_layer["nginx"]["bundle"], EMPTY_BUNDLE_LABEL)
        self.assertEqual(by_layer["nginx"]["responses"], 3)
        self.assertAlmostEqual(by_layer["nginx"]["share"], 2 / 3)


class TestAlphabetLoading(unittest.TestCase):
    """The co-occurrence alphabet is loaded from TOML, configurable (#28)."""

    def _write_toml(self, body: str) -> str:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".toml", delete=False, encoding="utf-8"
        )
        handle.write(body)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_packaged_default_is_the_security_posture_set(self) -> None:
        headers = default_security_headers()
        self.assertIn("strict-transport-security", headers)
        self.assertIn("nel", headers)
        # present_security_headers reads the same loaded alphabet.
        self.assertEqual(present_security_headers(["nel", "date"]), ["nel"])

    def test_override_lowercases_and_dedupes_preserving_order(self) -> None:
        path = self._write_toml('headers = ["X-Foo", "x-bar", "x-foo"]\n')
        self.assertEqual(load_security_headers(path), ("x-foo", "x-bar"))

    def test_empty_alphabet_rejected(self) -> None:
        path = self._write_toml("headers = []\n")
        with self.assertRaises(ValueError):
            load_security_headers(path)

    def test_non_string_entries_rejected(self) -> None:
        path = self._write_toml("headers = [1, 2]\n")
        with self.assertRaises(ValueError):
            load_security_headers(path)

    def test_missing_headers_key_rejected(self) -> None:
        path = self._write_toml("other = 1\n")
        with self.assertRaises(ValueError):
            load_security_headers(path)


if __name__ == "__main__":
    unittest.main()
