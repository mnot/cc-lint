"""Tests for cc_lint.emr.job merge, trim, and bucket helpers."""

import unittest
from typing import Any, Dict

from cc_lint.emr.job import (
    TOP_K_ASN,
    TOP_K_CSP_SITES,
    TOP_K_FIELD_COUNTS,
    TOP_K_RECIPES,
    TOP_K_VAR_VALUES,
    _failure_bucket,
    _trim_csp_sizes,
    merge_csp_sizes,
    merge_globals,
    merge_note,
    merge_stats_dict,
    merge_value_histograms,
    sample_key,
    trim_stats_dict,
)
from cc_lint.hll import HLL_P_GLOBAL, HLL_P_PER_NOTE, hll_add, make_registers
from cc_lint.recipes import RecipeStats


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
                            {
                                "url": "http://a.example/",
                                "vars": {},
                                "site": "a.example",
                            }
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
                            {
                                "url": "http://a.example/x",
                                "vars": {},
                                "site": "a.example",
                            },
                            {
                                "url": "http://b.example/",
                                "vars": {},
                                "site": "b.example",
                            },
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

    def test_merges_every_to_dict_field(self) -> None:
        # Regression: merge_stats_dict has to cover every key that
        # StatsCollector.to_dict() emits. Dropping any field silently
        # discards data when the EMR mapper folds per-WARC worker output
        # into its running stats. Verify sites_hll, severity_counts,
        # csp_max_by_site, vary, and cache_control all survive the merge.
        first_hll = make_registers(HLL_P_GLOBAL)
        second_hll = make_registers(HLL_P_GLOBAL)
        hll_add(first_hll, HLL_P_GLOBAL, "a.example")
        hll_add(second_hll, HLL_P_GLOBAL, "b.example")

        target: Dict[str, Any] = {}
        merge_stats_dict(
            target,
            {
                "total_responses": 50,
                "notes": {},
                "field_counts": {"date": 50},
                "unprocessed_counts": {},
                "sites_hll": first_hll,
                "csp_max_by_site": {"a.example": 100},
                "severity_counts": {"warn": 30, "clean": 20},
                "value_histograms": {"age": {"1-10 min": 3, "0": 1}},
                "vary": {
                    "responses_with_vary": 4,
                    "recipes": {"occ": {"accept-encoding": 4}, "hlls": {}},
                    "marginals": {"occ": {"accept-encoding": 4}, "hlls": {}},
                },
                "cache_control": {
                    "responses_with_cc": 4,
                    "recipes": {"occ": {"max-age=N, public": 4}, "hlls": {}},
                    "marginals": {"occ": {"max-age": 4, "public": 4}, "hlls": {}},
                },
                "cooccur": {
                    "responses": 50,
                    "bundles": {"occ": {"(none)": 40, "x-frame-options": 10}},
                    "marginals": {"occ": {"x-frame-options": 10}},
                    "pairs": {"occ": {}},
                    "by_layer": {"x-frame-options": {"cloudflare": 10}},
                },
                "transition": {
                    "responses": 50,
                    "pairs": {
                        "frame_options": {
                            "occ": {"both": 5, "legacy_only": 10, "neither": 35},
                            "hlls": {},
                        }
                    },
                },
            },
        )
        merge_stats_dict(
            target,
            {
                "total_responses": 30,
                "notes": {},
                "field_counts": {"date": 30, "server": 25},
                "unprocessed_counts": {},
                "sites_hll": second_hll,
                "csp_max_by_site": {"a.example": 500, "b.example": 200},
                "severity_counts": {"warn": 10, "info": 15, "clean": 5},
                "value_histograms": {
                    "age": {"1-10 min": 2},
                    "hsts_max_age": {"1-10 years": 4},
                },
                "vary": {
                    "responses_with_vary": 2,
                    "recipes": {"occ": {"accept-encoding": 1, "cookie": 1}, "hlls": {}},
                    "marginals": {
                        "occ": {"accept-encoding": 1, "cookie": 1},
                        "hlls": {},
                    },
                },
                "cache_control": {
                    "responses_with_cc": 2,
                    "recipes": {
                        "occ": {"max-age=N, public": 1, "no-store": 1},
                        "hlls": {},
                    },
                    "marginals": {
                        "occ": {"max-age": 1, "public": 1, "no-store": 1},
                        "hlls": {},
                    },
                },
                "cooccur": {
                    "responses": 30,
                    "bundles": {"occ": {"(none)": 20, "x-frame-options": 10}},
                    "marginals": {"occ": {"x-frame-options": 10}},
                    "pairs": {"occ": {}},
                    "by_layer": {"x-frame-options": {"cloudflare": 5, "nginx": 5}},
                },
                "transition": {
                    "responses": 30,
                    "pairs": {
                        "frame_options": {
                            "occ": {"both": 3, "modern_only": 7, "neither": 20},
                            "hlls": {},
                        }
                    },
                },
            },
        )
        self.assertEqual(target["total_responses"], 80)
        self.assertEqual(target["field_counts"], {"date": 80, "server": 25})
        # Severity counts sum.
        self.assertEqual(
            target["severity_counts"],
            {"warn": 40, "info": 15, "clean": 25},
        )
        # CSP per-site max.
        self.assertEqual(
            target["csp_max_by_site"],
            {"a.example": 500, "b.example": 200},
        )
        # HLL is unioned register-wise; the two disjoint adds give two
        # distinct sites in the merged register state.
        self.assertIn("sites_hll", target)
        self.assertGreaterEqual(sum(1 for r in target["sites_hll"] if r > 0), 1)
        # Vary block survives: responses_with_vary sums and recipe/marginal
        # occurrence counts merge across the two folds.
        self.assertEqual(target["vary"]["responses_with_vary"], 6)
        self.assertEqual(
            target["vary"]["recipes"]["occ"],
            {"accept-encoding": 5, "cookie": 1},
        )
        self.assertEqual(
            target["vary"]["marginals"]["occ"],
            {"accept-encoding": 5, "cookie": 1},
        )
        # Cache-Control block survives: responses_with_cc sums and
        # recipe/marginal occurrence counts merge across the two folds.
        self.assertEqual(target["cache_control"]["responses_with_cc"], 6)
        self.assertEqual(
            target["cache_control"]["recipes"]["occ"],
            {"max-age=N, public": 5, "no-store": 1},
        )
        self.assertEqual(
            target["cache_control"]["marginals"]["occ"],
            {"max-age": 5, "public": 5, "no-store": 1},
        )
        # Value histograms (issue #8) survive: buckets sum per histogram and
        # a histogram seen in only one fold still carries through.
        self.assertEqual(
            target["value_histograms"],
            {
                "age": {"1-10 min": 5, "0": 1},
                "hsts_max_age": {"1-10 years": 4},
            },
        )
        # cooccur block survives: responses sum, bundle/marginal occ merge, and
        # by_layer (bundle -> layer -> count) merges across both folds.
        self.assertEqual(target["cooccur"]["responses"], 80)
        self.assertEqual(
            target["cooccur"]["bundles"]["occ"],
            {"(none)": 60, "x-frame-options": 20},
        )
        self.assertEqual(target["cooccur"]["marginals"]["occ"], {"x-frame-options": 20})
        self.assertEqual(
            target["cooccur"]["by_layer"]["x-frame-options"],
            {"cloudflare": 15, "nginx": 5},
        )
        # transition block (issue #11) survives: responses sum and per-pair,
        # per-category occurrence counts merge across both folds.
        self.assertEqual(target["transition"]["responses"], 80)
        self.assertEqual(
            target["transition"]["pairs"]["frame_options"]["occ"],
            {"both": 8, "legacy_only": 10, "modern_only": 7, "neither": 55},
        )


class TestMergeValueHistograms(unittest.TestCase):
    def test_buckets_sum_per_histogram(self) -> None:
        target: Dict[str, Dict[str, int]] = {}
        merge_value_histograms(
            target, {"age": {"1-10 min": 2}, "cache_control_max_age": {"0": 1}}
        )
        merge_value_histograms(
            target,
            {"age": {"1-10 min": 3, ">10 years": 1}, "cache_control_max_age": {"0": 4}},
        )
        self.assertEqual(
            target,
            {
                "age": {"1-10 min": 5, ">10 years": 1},
                "cache_control_max_age": {"0": 5},
            },
        )


class TestFingerprintMerges(unittest.TestCase):
    def test_layer_stats_in_stats_dict(self) -> None:
        target: Dict[str, Any] = {}
        merge_stats_dict(
            target,
            {
                "total_responses": 1,
                "notes": {},
                "layer_counts": {"cloudflare": 2, "__unmatched__": 1},
                "field_counts_by_layer": {"server": {"cloudflare": 2}},
            },
        )
        merge_stats_dict(
            target,
            {
                "total_responses": 1,
                "notes": {},
                "layer_counts": {"cloudflare": 3, "nginx": 1},
                "field_counts_by_layer": {
                    "server": {"cloudflare": 3, "nginx": 1},
                    "via": {"fastly": 1},
                },
            },
        )
        self.assertEqual(
            target["layer_counts"],
            {"cloudflare": 5, "nginx": 1, "__unmatched__": 1},
        )
        self.assertEqual(
            target["field_counts_by_layer"],
            {"server": {"cloudflare": 5, "nginx": 1}, "via": {"fastly": 1}},
        )

    def test_layer_stats_in_globals(self) -> None:
        target: Dict[str, Any] = {}
        merge_globals(
            target,
            {
                "total_responses": 1,
                "layer_counts": {"akamai": 4},
                "field_counts_by_layer": {"x-xss-protection": {"akamai": 4}},
            },
        )
        merge_globals(
            target,
            {"total_responses": 1, "layer_counts": {"akamai": 1, "vercel": 2}},
        )
        self.assertEqual(target["layer_counts"], {"akamai": 5, "vercel": 2})
        self.assertEqual(
            target["field_counts_by_layer"], {"x-xss-protection": {"akamai": 4}}
        )

    def test_note_by_layer_sums(self) -> None:
        target = _empty_note()
        merge_note(target, {"count": 2, "by_layer": {"cloudflare": 2}})
        merge_note(target, {"count": 1, "by_layer": {"cloudflare": 1, "nginx": 1}})
        self.assertEqual(target["count"], 3)
        self.assertEqual(target["by_layer"], {"cloudflare": 3, "nginx": 1})

    def test_fcbl_trimmed(self) -> None:
        stats: Dict[str, Any] = {
            "field_counts_by_layer": {
                f"h{i}": {"nginx": i + 1} for i in range(TOP_K_FIELD_COUNTS + 5)
            }
        }
        trim_stats_dict(stats)
        self.assertEqual(len(stats["field_counts_by_layer"]), TOP_K_FIELD_COUNTS)
        self.assertTrue(stats.get("truncated_field_counts_by_layer"))
        # The highest-total headers survive the trim.
        self.assertIn(f"h{TOP_K_FIELD_COUNTS + 4}", stats["field_counts_by_layer"])

    def test_fcbl_below_cap_no_flag(self) -> None:
        stats: Dict[str, Any] = {"field_counts_by_layer": {"server": {"nginx": 1}}}
        trim_stats_dict(stats)
        self.assertNotIn("truncated_field_counts_by_layer", stats)

    def test_asn_counts_merge_and_trim(self) -> None:
        target: Dict[str, Any] = {}
        merge_stats_dict(
            target,
            {"total_responses": 1, "notes": {}, "asn_counts": {"13335": 2}},
        )
        merge_globals(target, {"total_responses": 1, "asn_counts": {"13335": 3, "1": 1}})
        self.assertEqual(target["asn_counts"], {"13335": 5, "1": 1})
        big = {"asn_counts": {str(i): i + 1 for i in range(TOP_K_ASN + 3)}}
        trim_stats_dict(big)
        self.assertEqual(len(big["asn_counts"]), TOP_K_ASN)
        self.assertTrue(big.get("truncated_asn_counts"))


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
            dropped_max = max(int(k.split("-")[1]) + 1 for k in dropped)
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
        self.assertEqual(target, {"a.example": 300, "b.example": 200, "c.example": 50})

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
        dropped_max = max(size for site, size in many.items() if site not in trimmed)
        self.assertGreaterEqual(kept_min, dropped_max)


class TestVaryAggregation(unittest.TestCase):
    def _vary_payload(self, occ_recipe: str, site: str) -> Dict[str, Any]:
        recipes = RecipeStats()
        recipes.add(occ_recipe, site)
        marginals = RecipeStats()
        for token in occ_recipe.split(", "):
            marginals.add(token, site)
        return {
            "responses_with_vary": 1,
            "recipes": recipes.to_dict(),
            "marginals": marginals.to_dict(),
        }

    def test_merge_stats_dict_vary(self) -> None:
        target: Dict[str, Any] = {}
        merge_stats_dict(
            target,
            {"total_responses": 1, "vary": self._vary_payload("a, b", "s1")},
        )
        merge_stats_dict(
            target,
            {"total_responses": 1, "vary": self._vary_payload("a, b", "s2")},
        )
        self.assertEqual(target["vary"]["responses_with_vary"], 2)
        self.assertEqual(target["vary"]["recipes"]["occ"]["a, b"], 2)
        self.assertEqual(target["vary"]["marginals"]["occ"]["a"], 2)

    def test_trim_stats_dict_trims_vary(self) -> None:
        recipes = RecipeStats()
        for i in range(TOP_K_RECIPES + 5):
            recipes.add(f"recipe-{i}", f"s{i}")
        stats: Dict[str, Any] = {
            "vary": {
                "responses_with_vary": TOP_K_RECIPES + 5,
                "recipes": recipes.to_dict(),
                "marginals": {"occ": {}, "hlls": {}},
            }
        }
        trim_stats_dict(stats)
        self.assertLessEqual(len(stats["vary"]["recipes"]["occ"]), TOP_K_RECIPES)
        self.assertTrue(stats["vary"]["recipes_truncated"])


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
