"""Tests for cc_lint.vary normalisation, classification, and merging."""

import unittest
from typing import Any, Dict

from httplint.message import HttpResponseLinter

from cc_lint.hll import hll_estimate
from cc_lint.recipes import RecipeStats
from cc_lint.vary import (
    ACCEPT_ENCODING,
    AE_ONLY_LABEL,
    count_nonstandard,
    factor_out,
    is_nonstandard_token,
    is_registered_field,
    merge_vary,
    recipe_key,
    recipe_tokens,
    trim_vary,
    vary_tokens,
)


def _linter_with_vary(*vary_values: str) -> HttpResponseLinter:
    linter = HttpResponseLinter()
    linter.base_uri = "http://example.com/"
    linter.process_response_topline(b"HTTP/1.1", b"200", b"OK")
    headers = [(b"Vary", value.encode()) for value in vary_values]
    linter.process_headers(headers)
    linter.finish_content(True)
    return linter


class TestTokenExtraction(unittest.TestCase):
    def test_merges_across_headers(self) -> None:
        linter = _linter_with_vary("Accept-Encoding, Cookie", "User-Agent")
        self.assertEqual(
            vary_tokens(linter), ["accept-encoding", "cookie", "user-agent"]
        )

    def test_no_vary_returns_empty(self) -> None:
        linter = HttpResponseLinter()
        linter.base_uri = "http://example.com/"
        linter.process_response_topline(b"HTTP/1.1", b"200", b"OK")
        linter.process_headers([(b"Content-Type", b"text/html")])
        linter.finish_content(True)
        self.assertEqual(vary_tokens(linter), [])

    def test_recipe_key_sorts_dedupes(self) -> None:
        self.assertEqual(
            recipe_key(["cookie", "accept-encoding", "cookie"]),
            "accept-encoding, cookie",
        )
        self.assertEqual(
            recipe_tokens("accept-encoding, cookie"),
            [
                "accept-encoding",
                "cookie",
            ],
        )


class TestClassification(unittest.TestCase):
    def test_registered_request_fields(self) -> None:
        for token in ("cookie", "accept-language", "accept-encoding", "user-agent"):
            self.assertTrue(is_registered_field(token), token)

    def test_synthetic_tokens_flagged(self) -> None:
        for token in ("x-ab-bucket", "x-device", "x-edge-cache"):
            self.assertTrue(is_nonstandard_token(token), token)
            self.assertFalse(is_registered_field(token), token)

    def test_asterisk_is_neither(self) -> None:
        self.assertFalse(is_registered_field("*"))
        self.assertFalse(is_nonstandard_token("*"))

    def test_count_nonstandard(self) -> None:
        self.assertEqual(count_nonstandard("accept-encoding, x-ab-bucket, x-device"), 2)
        self.assertEqual(count_nonstandard("accept-encoding, cookie"), 0)


class TestFactorOut(unittest.TestCase):
    def test_removes_token_and_merges(self) -> None:
        recipes = RecipeStats()
        recipes.add("accept-encoding, cookie", "s1")
        recipes.add("cookie", "s2")  # collapses with the row above after AE drop
        recipes.add("accept-encoding", "s3")  # becomes the AE-only bucket
        factored = factor_out(recipes.to_dict(), ACCEPT_ENCODING, AE_ONLY_LABEL)
        self.assertEqual(factored["occ"]["cookie"], 2)
        self.assertEqual(factored["occ"][AE_ONLY_LABEL], 1)
        # Per-site survives the regroup: the two cookie rows came from 2 sites.
        self.assertEqual(hll_estimate(factored["hlls"]["cookie"]), 2)


class TestMergeAndTrim(unittest.TestCase):
    def test_merge_vary_sums_and_flags(self) -> None:
        target: Dict[str, Any] = {
            "responses_with_vary": 2,
            "recipes": {"occ": {"cookie": 2}, "hlls": {}},
            "recipes_truncated": True,
        }
        source: Dict[str, Any] = {
            "responses_with_vary": 3,
            "recipes": {"occ": {"cookie": 1, "user-agent": 3}, "hlls": {}},
        }
        merge_vary(target, source)
        self.assertEqual(target["responses_with_vary"], 5)
        self.assertEqual(target["recipes"]["occ"], {"cookie": 3, "user-agent": 3})
        self.assertTrue(target["recipes_truncated"])

    def test_trim_vary_sets_flags(self) -> None:
        vary: Dict[str, Any] = {
            "responses_with_vary": 6,
            "recipes": {
                "occ": {"a": 3, "b": 2, "c": 1},
                "hlls": {},
            },
            "marginals": {"occ": {"x": 1}, "hlls": {}},
        }
        trim_vary(vary, 2)
        self.assertEqual(set(vary["recipes"]["occ"]), {"a", "b"})
        self.assertTrue(vary["recipes_truncated"])
        self.assertNotIn("marginals_truncated", vary)


if __name__ == "__main__":
    unittest.main()
