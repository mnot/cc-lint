"""Smoke tests for the HyperLogLog used to estimate distinct-site counts."""

import unittest

from cc_lint.hll import (
    HLL_P_GLOBAL,
    HLL_P_PER_NOTE,
    hll_add,
    hll_estimate,
    hll_merge,
    make_registers,
)


class TestHLL(unittest.TestCase):
    def test_global_precision_in_band(self) -> None:
        registers = make_registers(HLL_P_GLOBAL)
        for i in range(1000):
            hll_add(registers, HLL_P_GLOBAL, f"site-{i}.example")
        # CRC32-based HLL is coarse; accept a wide ~30% band.
        estimate = hll_estimate(registers)
        self.assertGreater(estimate, 700)
        self.assertLess(estimate, 1300)

    def test_merge_unions_disjoint_sets(self) -> None:
        first = make_registers(HLL_P_PER_NOTE)
        second = make_registers(HLL_P_PER_NOTE)
        for i in range(300):
            hll_add(first, HLL_P_PER_NOTE, f"a-{i}")
        for i in range(300):
            hll_add(second, HLL_P_PER_NOTE, f"b-{i}")
        hll_merge(first, second)
        estimate = hll_estimate(first)
        self.assertGreater(estimate, 400)
        self.assertLess(estimate, 1000)

    def test_empty_register_is_zero(self) -> None:
        self.assertEqual(hll_estimate([]), 0)

    def test_duplicates_dont_inflate(self) -> None:
        registers = make_registers(HLL_P_PER_NOTE)
        for _ in range(100):
            hll_add(registers, HLL_P_PER_NOTE, "same.example")
        self.assertLess(hll_estimate(registers), 5)


if __name__ == "__main__":
    unittest.main()
