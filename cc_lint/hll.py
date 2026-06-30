"""Tiny HyperLogLog used to estimate distinct-site counts.

Three precisions are used in cc-lint:

- ``HLL_P_GLOBAL`` (12) for the crawl-wide site count; ~1.6% error,
  ~4 KB of register state.
- ``HLL_P_PER_NOTE`` (8) for per-note "seen on N sites"; ~6.5% error,
  ~256 bytes of register state. With ~200 active notes across 1600
  mappers that's ~320 MB shuffle, well within budget.
- ``HLL_P_RECIPE`` (6) for the per-value (per-recipe) "seen on N sites"
  HLLs in the Vary breakdown; ~13% error, ~64 bytes of register state.
  Per-value HLLs multiply by tracked-value cardinality (up to
  ``TOP_K_RECIPES`` distinct recipes per mapper), so they use the
  coarsest precision -- a recipe's per-site count is a ranking signal,
  not a headline number, and 1/4 the register state keeps the shuffle in
  budget. Per-field *marginal* HLLs stay at ``HLL_P_PER_NOTE``: their key
  space is bounded (a few hundred field-names) and they carry the
  headline axes (Cookie / Accept-Language / …) where accuracy matters.

Registers serialize as a plain ``List[int]`` so they round-trip through
the mrjob JSONProtocol shuffle.

Adapted from the implementation in feed_survey.analysis.stats.
"""

from __future__ import annotations

import math
import zlib
from typing import List

HLL_P_GLOBAL = 12
HLL_P_PER_NOTE = 8
HLL_P_RECIPE = 6


def make_registers(precision: int) -> List[int]:
    return [0] * (1 << precision)


def hll_add(registers: List[int], precision: int, item: str) -> None:
    if not item:
        return
    hashed = zlib.crc32(item.encode("utf-8")) & 0xFFFFFFFF
    bucket_count = 1 << precision
    idx = hashed & (bucket_count - 1)
    w_bits = 32 - precision
    remaining = hashed >> precision
    rho = (w_bits - remaining.bit_length() + 1) if remaining > 0 else (w_bits + 1)
    if rho > registers[idx]:
        registers[idx] = rho


def hll_merge(target: List[int], source: List[int]) -> None:
    """Register-wise max merge in place; tolerates length mismatches."""
    width = min(len(target), len(source))
    for i in range(width):
        if source[i] > target[i]:
            target[i] = source[i]


def hll_estimate(registers: List[int]) -> int:
    bucket_count = len(registers)
    if bucket_count == 0:
        return 0
    alpha = 0.7213 / (1 + 1.079 / bucket_count)
    z_inverse = sum(2.0**-r for r in registers)
    estimate = alpha * (bucket_count**2) / z_inverse
    # Small-range (linear-counting) correction only. We deliberately omit the
    # classic large-range correction: the 32-bit zlib.crc32 hash (see hll_add)
    # caps usable cardinality well below 2^32 anyway, where hash collisions
    # bias the estimate low. cc-lint's largest count is the crawl-wide site
    # total (tens of thousands), far under that ceiling, so neither matters
    # here -- but any future per-(var, value) HLL set over a high-cardinality
    # key would need a 64-bit hash and the large-range correction added back.
    if estimate <= 2.5 * bucket_count:
        zeroes = registers.count(0)
        if zeroes > 0:
            estimate = bucket_count * math.log(bucket_count / zeroes)
    return int(estimate)
