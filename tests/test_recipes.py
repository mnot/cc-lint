"""Tests for the reusable recipe tabulation in cc_lint.recipes."""

import unittest
from typing import Any, Dict

from cc_lint.hll import hll_estimate
from cc_lint.recipes import RecipeStats, merge_recipe_dict, trim_recipe_dict


class TestRecipeStats(unittest.TestCase):
    def test_occurrence_and_site_counts(self) -> None:
        stats = RecipeStats()
        stats.add("a, b", "site1")
        stats.add("a, b", "site2")
        stats.add("a, b", "site1")  # repeat site -> occ up, sites flat
        stats.add("c", None)  # no site -> occ only, no hll
        out = stats.to_dict()
        self.assertEqual(out["occ"]["a, b"], 3)
        self.assertEqual(out["occ"]["c"], 1)
        self.assertIn("a, b", out["hlls"])
        self.assertNotIn("c", out["hlls"])
        self.assertEqual(hll_estimate(out["hlls"]["a, b"]), 2)

    def test_empty_recipe_ignored(self) -> None:
        stats = RecipeStats()
        stats.add("", "site1")
        self.assertEqual(stats.to_dict()["occ"], {})


class TestMergeRecipeDict(unittest.TestCase):
    def test_occ_sums_and_hlls_union(self) -> None:
        left = RecipeStats()
        left.add("a, b", "s1")
        right = RecipeStats()
        right.add("a, b", "s2")
        right.add("c", "s3")
        target = left.to_dict()
        merge_recipe_dict(target, right.to_dict())
        self.assertEqual(target["occ"], {"a, b": 2, "c": 1})
        # HLL union of two distinct sites -> ~2
        self.assertEqual(hll_estimate(target["hlls"]["a, b"]), 2)
        self.assertEqual(hll_estimate(target["hlls"]["c"]), 1)

    def test_merge_copies_registers(self) -> None:
        src = RecipeStats()
        src.add("a", "s1")
        target: Dict[str, Any] = {}
        merge_recipe_dict(target, src.to_dict())
        # Mutating the merged copy must not alias the source registers.
        target["hlls"]["a"][0] = 99
        self.assertNotEqual(src.hlls["a"][0], 99)


class TestTrimRecipeDict(unittest.TestCase):
    def test_keeps_top_k_by_occurrence(self) -> None:
        stats = RecipeStats()
        for i in range(10):
            for _ in range(i + 1):
                stats.add(f"r{i}", f"s{i}")
        recipe_dict = stats.to_dict()
        truncated = trim_recipe_dict(recipe_dict, 3)
        self.assertTrue(truncated)
        self.assertEqual(set(recipe_dict["occ"]), {"r9", "r8", "r7"})
        # HLLs for dropped recipes are pruned too.
        self.assertEqual(set(recipe_dict["hlls"]), {"r9", "r8", "r7"})

    def test_no_trim_returns_false(self) -> None:
        stats = RecipeStats()
        stats.add("a", "s1")
        recipe_dict = stats.to_dict()
        self.assertFalse(trim_recipe_dict(recipe_dict, 10))
        self.assertEqual(set(recipe_dict["occ"]), {"a"})


if __name__ == "__main__":
    unittest.main()
