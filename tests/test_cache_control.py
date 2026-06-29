"""Tests for cc_lint.cache_control normalisation, classification, merging."""

import unittest
from typing import Any, Dict

from httplint.message import HttpResponseLinter

from cc_lint.cache_control import (
    cc_directives,
    count_nonstandard,
    is_nonstandard_directive,
    marginal_key,
    merge_cache_control,
    normalize_directive,
    recipe_key,
    recipe_tokens,
    trim_cache_control,
)


def _linter_with_cc(*cc_values: str) -> HttpResponseLinter:
    linter = HttpResponseLinter()
    linter.base_uri = "http://example.com/"
    linter.process_response_topline(b"HTTP/1.1", b"200", b"OK")
    headers = [(b"Cache-Control", value.encode()) for value in cc_values]
    linter.process_headers(headers)
    linter.finish_content(True)
    return linter


class TestNormalization(unittest.TestCase):
    def test_valueless_directive_is_bare(self) -> None:
        self.assertEqual(normalize_directive("no-store", None), "no-store")

    def test_int_value_collapses_to_n(self) -> None:
        self.assertEqual(normalize_directive("max-age", 0), "max-age=N")
        self.assertEqual(normalize_directive("max-age", 31536000), "max-age=N")

    def test_string_value_collapses_to_n(self) -> None:
        # Unknown directive carrying an arbitrary value.
        self.assertEqual(normalize_directive("foo", "bar"), "foo=N")

    def test_none_sentinel_treated_as_valueless(self) -> None:
        # httplint renders bare no-cache / private (parsed via unquote_string)
        # as the literal string "None"; that must normalise to the bare name.
        self.assertEqual(normalize_directive("no-cache", "None"), "no-cache")
        self.assertEqual(normalize_directive("private", "None"), "private")

    def test_name_lowercased(self) -> None:
        self.assertEqual(normalize_directive("Max-Age", 5), "max-age=N")


class TestDirectiveExtraction(unittest.TestCase):
    def test_kitchen_sink_recipe(self) -> None:
        linter = _linter_with_cc(
            "no-store, no-cache, must-revalidate, max-age=0, private"
        )
        self.assertEqual(
            recipe_key(cc_directives(linter)),
            "max-age=N, must-revalidate, no-cache, no-store, private",
        )

    def test_merges_across_headers(self) -> None:
        linter = _linter_with_cc("public, max-age=600", "immutable")
        self.assertEqual(
            recipe_key(cc_directives(linter)),
            "immutable, max-age=N, public",
        )

    def test_repeated_directive_dedupes_in_recipe(self) -> None:
        # max-age twice -> both max-age=N -> one token (CC_DUP tracks the dup).
        linter = _linter_with_cc("max-age=5, max-age=10")
        self.assertEqual(recipe_key(cc_directives(linter)), "max-age=N")

    def test_no_cache_control_returns_empty(self) -> None:
        linter = HttpResponseLinter()
        linter.base_uri = "http://example.com/"
        linter.process_response_topline(b"HTTP/1.1", b"200", b"OK")
        linter.process_headers([(b"Content-Type", b"text/html")])
        linter.finish_content(True)
        self.assertEqual(cc_directives(linter), [])

    def test_recipe_tokens_roundtrip(self) -> None:
        self.assertEqual(
            recipe_tokens("max-age=N, no-store"), ["max-age=N", "no-store"]
        )


class TestClassification(unittest.TestCase):
    def test_known_directives(self) -> None:
        for token in ("max-age=N", "no-store", "private", "s-maxage=N"):
            self.assertFalse(is_nonstandard_directive(token), token)

    def test_nonstandard_directives(self) -> None:
        for token in ("surrogate-control=N", "s-max-age=N", "x-edge-cache"):
            self.assertTrue(is_nonstandard_directive(token), token)

    def test_marginal_key_strips_value(self) -> None:
        self.assertEqual(marginal_key("max-age=N"), "max-age")
        self.assertEqual(marginal_key("no-store"), "no-store")

    def test_count_nonstandard(self) -> None:
        self.assertEqual(
            count_nonstandard("max-age=N, surrogate-control=N, x-foo"), 2
        )
        self.assertEqual(count_nonstandard("max-age=N, no-store"), 0)


class TestMergeAndTrim(unittest.TestCase):
    def test_merge_sums_and_flags(self) -> None:
        target: Dict[str, Any] = {
            "responses_with_cc": 2,
            "recipes": {"occ": {"no-store": 2}, "hlls": {}},
            "recipes_truncated": True,
        }
        source: Dict[str, Any] = {
            "responses_with_cc": 3,
            "recipes": {"occ": {"no-store": 1, "max-age=N, public": 3}, "hlls": {}},
        }
        merge_cache_control(target, source)
        self.assertEqual(target["responses_with_cc"], 5)
        self.assertEqual(
            target["recipes"]["occ"], {"no-store": 3, "max-age=N, public": 3}
        )
        self.assertTrue(target["recipes_truncated"])

    def test_trim_sets_flags(self) -> None:
        cache_control: Dict[str, Any] = {
            "responses_with_cc": 6,
            "recipes": {"occ": {"a": 3, "b": 2, "c": 1}, "hlls": {}},
            "marginals": {"occ": {"x": 1}, "hlls": {}},
        }
        trim_cache_control(cache_control, 2)
        self.assertEqual(set(cache_control["recipes"]["occ"]), {"a", "b"})
        self.assertTrue(cache_control["recipes_truncated"])
        self.assertNotIn("marginals_truncated", cache_control)


if __name__ == "__main__":
    unittest.main()
