"""Tests for cc_lint.emr.job merge, trim, and bucket helpers."""

import unittest
from typing import Any, Dict

from cc_lint.emr.job import (
    TOP_K_CSP_SITES,
    TOP_K_FIELD_COUNTS,
    TOP_K_VAR_VALUES,
    _failure_bucket,
    _trim_csp_sizes,
    merge_csp_sizes,
    merge_globals,
    merge_note,
    sample_key,
    merge_stats_dict,
    trim_stats_dict,
)
from cc_lint.hll import HLL_P_GLOBAL, HLL_P_PER_NOTE, hll_add, make_registers


def _empty_note() -> Dict[str, Any]:
    return {"count": 0, "samples": [], "vars": {}}


class TestMergeStatsDict(unittest.TestCase):
    def test_scalars_and_fields_sum(self) -> None:
        target: Dict[str, Any] = {}
        merge_stats_dict(
            target,
            {
                "total_responses": 5,
                "field_counts": {"h": 3},
                "unprocessed_counts": {"x": 1},
                "notes": {},
            },
        )
        merge_stats_dict(
            target,
            {
                "total_responses": 7,
                "field_counts": {"h": 1, "k": 2},
                "unprocessed_counts": {"x": 4},
                "notes": {},
            },
        )
        self.assertEqual(target["total_responses"], 12)
        self.assertEqual(target["field_counts"], {"h": 4, "k": 2})
        self.assertEqual(target["unprocessed_counts"], {"x": 5})

    def test_notes_dedupe_by_site(self) -> None:
        target: Dict[str, Any] = {}
        merge_stats_dict(
            target,
            {
                "total_responses": 1,
                "notes": {
                    "BAD_SYNTAX": {
                        "count": 2,
                        "samples": [
                            {"url": "http://a.example/", "vars": {}, "site": "a.example"}
                        ],
                        "vars": {},
                    }
                },
            },
        )
        merge_stats_dict(
            target,
            {
                "total_responses": 1,
                "notes": {
                    "BAD_SYNTAX": {
                        "count": 3,
                        "samples": [
                            # Same site, different URL -- should be deduped.
                            {"url": "http://a.example/x", "vars": {}, "site": "a.example"},
                            {"url": "http://b.example/", "vars": {}, "site": "b.example"},
                        ],
                        "vars": {},
                    }
                },
            },
        )
        note = target["notes"]["BAD_SYNTAX"]
        self.assertEqual(note["count"], 5)
        sites = {s.get("site") for s in note["samples"]}
        self.assertEqual(sites, {"a.example", "b.example"})


class TestMergeGlobalsHLLAndFlags(unittest.TestCase):
    def test_hll_union(self) -> None:
        first = make_registers(HLL_P_GLOBAL)
        second = make_registers(HLL_P_GLOBAL)
        for i in range(50):
            hll_add(first, HLL_P_GLOBAL, f"first-{i}")
        for i in range(50):
            hll_add(second, HLL_P_GLOBAL, f"second-{i}")
        target: Dict[str, Any] = {}
        merge_globals(target, {"total_responses": 1, "sites_hll": first})
        merge_globals(target, {"total_responses": 1, "sites_hll": second})
        self.assertIn("sites_hll", target)
        # Register-wise max merge: target register equals the larger source.
        for idx, (first_value, second_value) in enumerate(zip(first, second)):
            self.assertEqual(target["sites_hll"][idx], max(first_value, second_value))

    def test_truncation_flags_or_merge(self) -> None:
        target: Dict[str, Any] = {}
        merge_globals(target, {"total_responses": 0})
        merge_globals(target, {"total_responses": 0, "truncated_field_counts": True})
        merge_globals(target, {"total_responses": 0})  # later mapper without flag
        self.assertTrue(target["truncated_field_counts"])

    def test_run_context_first_wins(self) -> None:
        target: Dict[str, Any] = {}
        merge_globals(
            target,
            {"total_responses": 0, "run_context": {"crawl_id": "CC-1"}},
        )
        merge_globals(
            target,
            {"total_responses": 0, "run_context": {"crawl_id": "CC-2"}},
        )
        self.assertEqual(target["run_context"]["crawl_id"], "CC-1")


class TestMergeNoteHLLAndFlags(unittest.TestCase):
    def test_per_note_hll_union(self) -> None:
        first = make_registers(HLL_P_PER_NOTE)
        second = make_registers(HLL_P_PER_NOTE)
        hll_add(first, HLL_P_PER_NOTE, "a.example")
        hll_add(second, HLL_P_PER_NOTE, "b.example")
        target = _empty_note()
        merge_note(target, {**_empty_note(), "count": 1, "sites_hll": first})
        merge_note(target, {**_empty_note(), "count": 1, "sites_hll": second})
        self.assertEqual(target["count"], 2)
        self.assertIn("sites_hll", target)

    def test_truncated_vars_or_merge(self) -> None:
        target = _empty_note()
        merge_note(target, {**_empty_note(), "count": 1})
        merge_note(
            target,
            {
                **_empty_note(),
                "count": 1,
                "truncated_vars": {"field_name": True},
            },
        )
        merge_note(target, {**_empty_note(), "count": 1})
        self.assertTrue(target["truncated_vars"]["field_name"])


class TestTrimStatsDict(unittest.TestCase):
    def test_field_counts_truncated(self) -> None:
        stats: Dict[str, Any] = {
            "total_responses": 0,
            "field_counts": {f"h-{i}": i + 1 for i in range(TOP_K_FIELD_COUNTS + 50)},
            "unprocessed_counts": {},
        }
        trim_stats_dict(stats)
        self.assertEqual(len(stats["field_counts"]), TOP_K_FIELD_COUNTS)
        self.assertTrue(stats.get("truncated_field_counts"))

    def test_keeps_highest_counts(self) -> None:
        stats: Dict[str, Any] = {
            "field_counts": {f"h-{i}": i + 1 for i in range(TOP_K_FIELD_COUNTS + 10)},
        }
        trim_stats_dict(stats)
        kept_min = min(stats["field_counts"].values())
        # The dropped set had counts 1..10 plus possibly some retained tail;
        # the kept-min must dominate the dropped-max.
        dropped = {
            f"h-{i}": i + 1 for i in range(TOP_K_FIELD_COUNTS + 10)
        }.keys() - stats["field_counts"].keys()
        if dropped:
            dropped_max = max(
                int(k.split("-")[1]) + 1 for k in dropped
            )
            self.assertGreaterEqual(kept_min, dropped_max)

    def test_var_samples_pruned(self) -> None:
        # Build vars with > cap entries; var_samples should be pruned to the
        # surviving val_str set.
        big_vars = {f"v-{i}": i + 1 for i in range(TOP_K_VAR_VALUES + 5)}
        big_var_samples = {
            f"v-{i}": [{"url": "http://x/", "vars": {}, "site": "x"}]
            for i in range(TOP_K_VAR_VALUES + 5)
        }
        stats: Dict[str, Any] = {
            "field_counts": {},
            "notes": {
                "N": {
                    "count": 1,
                    "samples": [],
                    "vars": {"value": big_vars},
                    "var_samples": {"value": big_var_samples},
                }
            },
        }
        trim_stats_dict(stats)
        note = stats["notes"]["N"]
        kept_vars = set(note["vars"]["value"].keys())
        kept_samples = set(note["var_samples"]["value"].keys())
        self.assertEqual(kept_vars, kept_samples)
        self.assertTrue(note["truncated_vars"]["value"])

    def test_below_cap_no_flag(self) -> None:
        stats: Dict[str, Any] = {
            "field_counts": {"a": 1, "b": 2},
            "unprocessed_counts": {"x": 1},
        }
        trim_stats_dict(stats)
        self.assertNotIn("truncated_field_counts", stats)
        self.assertNotIn("truncated_unprocessed_counts", stats)


class TestFailureBucket(unittest.TestCase):
    def test_known_signals(self) -> None:
        self.assertEqual(_failure_bucket(-9), "warc_signal_sigkill")
        self.assertEqual(_failure_bucket(-11), "warc_signal_sigsegv")
        self.assertEqual(_failure_bucket(-10), "warc_signal_sigbus")
        self.assertEqual(_failure_bucket(-15), "warc_signal_sigterm")

    def test_other_signal_bucketed(self) -> None:
        self.assertEqual(_failure_bucket(-1), "warc_signal_other")

    def test_positive_exit_codes(self) -> None:
        self.assertEqual(_failure_bucket(0), "warc_exit_zero")
        self.assertEqual(_failure_bucket(1), "warc_exit_nonzero")
        self.assertEqual(_failure_bucket(137), "warc_exit_nonzero")

    def test_none(self) -> None:
        self.assertEqual(_failure_bucket(None), "warc_exit_unknown")


class TestMergeCspSizes(unittest.TestCase):
    def test_merge_takes_max_per_site(self) -> None:
        target: Dict[str, int] = {}
        merge_csp_sizes(target, {"a.example": 100, "b.example": 200})
        merge_csp_sizes(target, {"a.example": 300, "c.example": 50})
        self.assertEqual(
            target, {"a.example": 300, "b.example": 200, "c.example": 50}
        )

    def test_merge_keeps_zeros(self) -> None:
        # site "a" appeared with no CSP in source -> stays 0 in target
        target: Dict[str, int] = {}
        merge_csp_sizes(target, {"a.example": 0})
        self.assertEqual(target, {"a.example": 0})
        # later non-zero observation upgrades it
        merge_csp_sizes(target, {"a.example": 500})
        self.assertEqual(target, {"a.example": 500})

    def test_trim_caps_to_top_k_by_size(self) -> None:
        many = {f"site-{i}.example": i for i in range(TOP_K_CSP_SITES + 50)}
        trimmed = _trim_csp_sizes(many)
        self.assertEqual(len(trimmed), TOP_K_CSP_SITES)
        # The TOP_K_CSP_SITES largest sizes should survive.
        kept_min = min(trimmed.values())
        dropped_max = max(
            size for site, size in many.items() if site not in trimmed
        )
        self.assertGreaterEqual(kept_min, dropped_max)


class TestSampleKey(unittest.TestCase):
    def test_prefers_site(self) -> None:
        self.assertEqual(
            sample_key({"url": "http://a/x", "site": "a.example"}), "a.example"
        )

    def test_falls_back_to_url(self) -> None:
        self.assertEqual(sample_key({"url": "http://a/x"}), "http://a/x")

    def test_empty_inputs(self) -> None:
        self.assertEqual(sample_key({}), "")


if __name__ == "__main__":
    unittest.main()
