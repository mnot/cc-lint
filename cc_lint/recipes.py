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
from typing import Any, Dict, List, Optional

from cc_lint.hll import HLL_P_PER_NOTE, hll_add, hll_merge, make_registers


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
