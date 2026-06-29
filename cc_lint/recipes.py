"""Reusable top-N "recipe" tabulation with a per-site dimension.

A *recipe* is a normalised header-value signature -- e.g. the sorted,
deduped set of field-names in a ``Vary`` header, or (future, see #9) the
normalised directive set of a ``Cache-Control`` header. This module
provides the collect / merge / trim machinery once so multiple headers
can reuse it; the header-specific normalisation lives elsewhere (see
``cc_lint.vary``).

Each recipe carries two measures:

- an **occurrence** count (one per response that produced the recipe), and
- a HyperLogLog of the distinct **sites** that produced it.

The two differ for the same reason per-note site cardinality differs from
note occurrence: one CDN-configured origin can emit a single recipe across
millions of responses, so per-occurrence is emitter-weighted while
per-site is operator-weighted. Both are interesting; we keep both.

Serialized form (round-trips through the mrjob JSONProtocol shuffle)::

    {"occ": {recipe: count, ...}, "hlls": {recipe: [registers...], ...}}

``hlls`` only carries entries for recipes seen with a known site, so it can
be sparser than ``occ``. :func:`trim_recipe_dict` caps the long tail by
occurrence and reports whether anything was dropped, mirroring the
``trim_stats_dict`` discipline in ``cc_lint.emr.job``.
"""

from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from cc_lint.hll import HLL_P_PER_NOTE, hll_add, hll_merge, make_registers

# The two sub-dicts every recipe *block* (a serialized header view) carries: a
# high-cardinality recipe dict and a bounded marginal dict, each a recipe dict
# in the {"occ": ..., "hlls": ...} shape above. Both ``cc_lint.vary`` and
# ``cc_lint.cache_control`` serialize to this shape, so the block-level merge
# and trim live here once.
RECIPE_BLOCK_SUBKEYS: Tuple[str, ...] = ("recipes", "marginals")


class RecipeStats:
    """Accumulate per-recipe occurrence counts and per-site HLLs.

    ``precision`` is the HLL register precision for the per-value site
    HLLs. It is caller-chosen because per-value HLLs multiply by value
    cardinality, so a high-cardinality structure (recipes) should use a
    coarser precision to stay within the shuffle budget while a bounded,
    headline-bearing one (field marginals) can afford the default. The
    merge/trim helpers don't need it -- ``hll_merge``/``hll_estimate``
    infer width from the register list -- so only ``add`` carries it.
    """

    def __init__(self, precision: int = HLL_P_PER_NOTE) -> None:
        self.precision = precision
        self.occ: Counter[str] = Counter()
        self.hlls: Dict[str, List[int]] = {}

    def add(self, recipe: str, site: Optional[str]) -> None:
        if not recipe:
            return
        self.occ[recipe] += 1
        if site:
            registers = self.hlls.get(recipe)
            if registers is None:
                registers = make_registers(self.precision)
                self.hlls[recipe] = registers
            hll_add(registers, self.precision, site)

    def to_dict(self) -> Dict[str, Any]:
        return {"occ": dict(self.occ), "hlls": self.hlls}


def merge_recipe_dict(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """Merge a serialized recipe dict into ``target`` in place.

    Occurrence counts sum; per-site HLLs merge register-wise (idempotent
    union), so re-merging the same source is safe for the site dimension
    but not for occurrence -- callers must not double-feed a source.
    """
    occ_target: Dict[str, int] = target.setdefault("occ", {})
    for recipe, count in source.get("occ", {}).items():
        occ_target[recipe] = occ_target.get(recipe, 0) + int(count)
    hll_target: Dict[str, List[int]] = target.setdefault("hlls", {})
    for recipe, registers in source.get("hlls", {}).items():
        existing = hll_target.get(recipe)
        if existing is None:
            hll_target[recipe] = list(registers)
        else:
            hll_merge(existing, registers)


def merge_recipe_block(
    target: Dict[str, Any],
    source: Dict[str, Any],
    count_field: str,
    subkeys: Tuple[str, ...] = RECIPE_BLOCK_SUBKEYS,
) -> None:
    """Merge a serialized recipe *block* into ``target`` in place.

    A block is ``{count_field: int, <sub>: recipe_dict, <sub>_truncated:
    bool, ...}`` for each ``sub`` in ``subkeys`` -- the shape both the
    ``vary`` and ``cache_control`` views emit. ``count_field`` (the
    response-count scalar) sums; each sub recipe dict merges via
    :func:`merge_recipe_dict`; truncation flags OR together. Header-specific
    wrappers (``merge_cache_control``) just bind ``count_field``.
    """
    target[count_field] = target.get(count_field, 0) + int(source.get(count_field, 0))
    for sub in subkeys:
        if sub in source:
            merge_recipe_dict(target.setdefault(sub, {}), source[sub])
        if source.get(f"{sub}_truncated"):
            target[f"{sub}_truncated"] = True


def trim_recipe_block(
    block: Dict[str, Any],
    top_k: int,
    subkeys: Tuple[str, ...] = RECIPE_BLOCK_SUBKEYS,
) -> None:
    """Cap each sub recipe dict in a block, setting sticky truncation flags."""
    for sub in subkeys:
        recipe_dict = block.get(sub)
        if recipe_dict and trim_recipe_dict(recipe_dict, top_k):
            block[f"{sub}_truncated"] = True


def trim_recipe_dict(recipe_dict: Dict[str, Any], top_k: int) -> bool:
    """Cap a recipe dict to the top-K recipes by occurrence, in place.

    Drops the long tail from both ``occ`` and ``hlls`` (HLLs for recipes
    that didn't survive the occurrence cut are useless). Returns ``True``
    if anything was dropped so callers can set a sticky truncation flag.
    """
    occ: Dict[str, int] = recipe_dict.get("occ", {})
    if len(occ) <= top_k:
        return False
    kept = dict(sorted(occ.items(), key=lambda kv: kv[1], reverse=True)[:top_k])
    recipe_dict["occ"] = kept
    hlls = recipe_dict.get("hlls")
    if hlls:
        recipe_dict["hlls"] = {r: h for r, h in hlls.items() if r in kept}
    return True
