"""Tests for cc_lint.transition: detection, categorisation, merge, derivation."""

import unittest
from typing import Any, Dict, List, Optional, Tuple

from cc_lint.hll import HLL_P_PER_NOTE, hll_add, make_registers
from cc_lint.transition import (
    PAIRS_BY_KEY,
    TRANSITION_PAIRS,
    TransitionStats,
    build_context,
    categorize,
    merge_transition,
    transition_rows,
)


class _FakeHandler:
    def __init__(self, value: Any) -> None:
        self.value = value


class _FakeHeaders:
    def __init__(self, handlers: Dict[str, Any], text: List[Tuple[str, str]]) -> None:
        self.handlers = handlers
        self.text = text


class _FakeLinter:
    """Minimal stand-in exposing only what build_context reads."""

    def __init__(
        self,
        csp_value: Any = None,
        cc_value: Any = None,
        text: Optional[List[Tuple[str, str]]] = None,
    ) -> None:
        handlers: Dict[str, Any] = {}
        text = text or []
        has_csp_text = any(str(n).lower() == "content-security-policy" for n, _ in text)
        if csp_value is not None or has_csp_text:
            # A real linter keeps the handler even when the value failed to
            # parse (value None), which is exactly when the raw fallback runs.
            handlers["content-security-policy"] = _FakeHandler(csp_value)
        if cc_value is not None:
            handlers["cache-control"] = _FakeHandler(cc_value)
        self.headers = _FakeHeaders(handlers, text)


def _ctx(
    header_items: List[Tuple[str, str]],
    csp_value: Any = None,
    cc_value: Any = None,
    text: Optional[List[Tuple[str, str]]] = None,
) -> Any:
    linter = _FakeLinter(csp_value=csp_value, cc_value=cc_value, text=text)
    return build_context(linter, header_items)


class TestDetection(unittest.TestCase):
    def test_header_presence_pairs(self) -> None:
        ctx = _ctx([("report-to", "x"), ("feature-policy", "y")])
        self.assertIn("report-to", ctx.headers)
        self.assertTrue(PAIRS_BY_KEY["reporting"].legacy(ctx))
        self.assertFalse(PAIRS_BY_KEY["reporting"].modern(ctx))  # type: ignore[misc]

    def test_csp_frame_ancestors_from_parsed(self) -> None:
        ctx = _ctx(
            [("content-security-policy", "v")],
            csp_value=[{"default-src": "'self'", "frame-ancestors": "'none'"}],
        )
        self.assertIn("frame-ancestors", ctx.csp_directives)
        self.assertTrue(PAIRS_BY_KEY["frame_options"].modern(ctx))  # type: ignore[misc]

    def test_csp_frame_ancestors_raw_fallback(self) -> None:
        # No parsed value, but raw header text present -> fallback split.
        ctx = _ctx(
            [("content-security-policy", "v")],
            csp_value=None,
            text=[("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")],
        )
        self.assertIn("frame-ancestors", ctx.csp_directives)

    def test_cc_directives(self) -> None:
        ctx = _ctx(
            [("cache-control", "v")],
            cc_value=[("max-age", 60), ("no-cache", "None")],
        )
        self.assertEqual(ctx.cc_directives, {"max-age", "no-cache"})
        self.assertTrue(PAIRS_BY_KEY["expiry"].modern(_with_header(ctx, "expires")))

    def test_pragma_no_cache(self) -> None:
        ctx = _ctx([("pragma", "no-cache")])
        self.assertTrue(ctx.pragma_no_cache)
        self.assertTrue(PAIRS_BY_KEY["no_cache"].legacy(ctx))

    def test_pragma_other_value_not_no_cache(self) -> None:
        ctx = _ctx([("pragma", "public")])
        self.assertFalse(ctx.pragma_no_cache)


def _with_header(ctx: Any, name: str) -> Any:
    """Return a copy of ctx with an extra present header (test helper)."""
    return ctx._replace(headers=ctx.headers | {name})


class TestCategorize(unittest.TestCase):
    def _frame_ctx(self, legacy: bool, modern: bool) -> Any:
        headers = ["x-frame-options"] if legacy else []
        csp = [{"frame-ancestors": "'none'"}] if modern else None
        header_items = [(h, "v") for h in headers]
        if modern:
            header_items.append(("content-security-policy", "v"))
        return _ctx(header_items, csp_value=csp)

    def test_both(self) -> None:
        self.assertEqual(
            categorize(PAIRS_BY_KEY["frame_options"], self._frame_ctx(True, True)),
            "both",
        )

    def test_legacy_only(self) -> None:
        self.assertEqual(
            categorize(PAIRS_BY_KEY["frame_options"], self._frame_ctx(True, False)),
            "legacy_only",
        )

    def test_modern_only(self) -> None:
        self.assertEqual(
            categorize(PAIRS_BY_KEY["frame_options"], self._frame_ctx(False, True)),
            "modern_only",
        )

    def test_neither(self) -> None:
        self.assertEqual(
            categorize(PAIRS_BY_KEY["frame_options"], self._frame_ctx(False, False)),
            "neither",
        )

    def test_legacy_only_pair_never_modern(self) -> None:
        # X-XSS-Protection has no modern side; presence -> legacy_only, else neither.
        present = _ctx([("x-xss-protection", "1")])
        absent = _ctx([])
        self.assertEqual(categorize(PAIRS_BY_KEY["xss_protection"], present), "legacy_only")
        self.assertEqual(categorize(PAIRS_BY_KEY["xss_protection"], absent), "neither")


class TestStatsAndMerge(unittest.TestCase):
    def test_add_and_to_dict(self) -> None:
        stats = TransitionStats(HLL_P_PER_NOTE)
        stats.add("frame_options", "both", "a.example")
        stats.add("frame_options", "both", "b.example")
        stats.add("frame_options", "legacy_only", "c.example")
        out = stats.to_dict()
        self.assertEqual(out["frame_options"]["occ"], {"both": 2, "legacy_only": 1})
        self.assertIn("both", out["frame_options"]["hlls"])

    def test_merge_sums_occ_and_unions_hlls(self) -> None:
        reg_a = make_registers(HLL_P_PER_NOTE)
        reg_b = make_registers(HLL_P_PER_NOTE)
        hll_add(reg_a, HLL_P_PER_NOTE, "a.example")
        hll_add(reg_b, HLL_P_PER_NOTE, "b.example")
        target: Dict[str, Any] = {}
        merge_transition(
            target,
            {
                "responses": 10,
                "pairs": {"frame_options": {"occ": {"both": 2}, "hlls": {"both": reg_a}}},
            },
        )
        merge_transition(
            target,
            {
                "responses": 5,
                "pairs": {"frame_options": {"occ": {"both": 3}, "hlls": {"both": reg_b}}},
            },
        )
        self.assertEqual(target["responses"], 15)
        self.assertEqual(target["pairs"]["frame_options"]["occ"]["both"], 5)
        merged = target["pairs"]["frame_options"]["hlls"]["both"]
        self.assertGreaterEqual(sum(1 for r in merged if r > 0), 1)

    def test_merge_disjoint_pairs(self) -> None:
        target: Dict[str, Any] = {}
        merge_transition(
            target,
            {"responses": 1, "pairs": {"expiry": {"occ": {"both": 1}, "hlls": {}}}},
        )
        merge_transition(
            target,
            {"responses": 1, "pairs": {"no_cache": {"occ": {"legacy_only": 1}, "hlls": {}}}},
        )
        self.assertEqual(set(target["pairs"]), {"expiry", "no_cache"})


class TestDerivation(unittest.TestCase):
    def test_rows_cover_every_pair(self) -> None:
        rows = transition_rows({"responses": 0, "pairs": {}})
        self.assertEqual(len(rows), len(TRANSITION_PAIRS))
        keys = {row["key"] for row in rows}
        self.assertEqual(keys, set(PAIRS_BY_KEY))

    def test_ratio_uses_present_rollups(self) -> None:
        transition = {
            "responses": 100,
            "pairs": {
                "frame_options": {
                    "occ": {"both": 10, "modern_only": 30, "legacy_only": 20, "neither": 40},
                    "hlls": {},
                }
            },
        }
        row = next(r for r in transition_rows(transition) if r["key"] == "frame_options")
        # modern_present = 10 + 30 = 40; legacy_present = 10 + 20 = 30; scoped = 70.
        self.assertEqual(row["modern_present"], 40)
        self.assertEqual(row["legacy_present"], 30)
        self.assertAlmostEqual(row["ratio"], 40 / 70)
        # neither is excluded from the scoped denominator.
        self.assertEqual(row["scoped"], 70)

    def test_legacy_only_pair_has_null_ratio(self) -> None:
        transition = {
            "responses": 10,
            "pairs": {"xss_protection": {"occ": {"legacy_only": 4, "neither": 6}, "hlls": {}}},
        }
        row = next(r for r in transition_rows(transition) if r["key"] == "xss_protection")
        self.assertTrue(row["legacy_only_pair"])
        # modern_present = 0 -> ratio is 0.0 (scoped = legacy_present = 4).
        self.assertEqual(row["modern_present"], 0)
        self.assertEqual(row["ratio"], 0.0)

    def test_rows_sorted_by_scoped_desc(self) -> None:
        transition = {
            "responses": 100,
            "pairs": {
                "frame_options": {"occ": {"both": 1, "neither": 99}, "hlls": {}},
                "expiry": {"occ": {"both": 50, "neither": 50}, "hlls": {}},
            },
        }
        rows = transition_rows(transition)
        self.assertEqual(rows[0]["key"], "expiry")

    def test_site_ratio_unions_category_hlls(self) -> None:
        both = make_registers(HLL_P_PER_NOTE)
        modern = make_registers(HLL_P_PER_NOTE)
        legacy = make_registers(HLL_P_PER_NOTE)
        hll_add(both, HLL_P_PER_NOTE, "shared.example")
        hll_add(modern, HLL_P_PER_NOTE, "m.example")
        hll_add(legacy, HLL_P_PER_NOTE, "l.example")
        transition = {
            "responses": 3,
            "pairs": {
                "frame_options": {
                    "occ": {"both": 1, "modern_only": 1, "legacy_only": 1},
                    "hlls": {"both": both, "modern_only": modern, "legacy_only": legacy},
                }
            },
        }
        row = next(r for r in transition_rows(transition) if r["key"] == "frame_options")
        # modern sites = union(both, modern_only) ~ 2; legacy sites ~ 2.
        self.assertIsNotNone(row["modern_sites"])
        self.assertIsNotNone(row["legacy_sites"])
        self.assertIsNotNone(row["site_ratio"])
        self.assertGreater(row["site_ratio"], 0.0)


if __name__ == "__main__":
    unittest.main()
