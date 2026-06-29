"""Legacy/modern dual-emit pair tabulation (issue #11) -- the transition tax.

For a curated set of deprecated/replacement header pairs, classify every
response into exactly one of four buckets per pair:

- **both** -- legacy *and* modern side present (a site mid-migration, paying
  the dual-emit cost);
- **legacy_only** -- only the deprecated side;
- **modern_only** -- only the replacement;
- **neither** -- the pair's topic doesn't apply to this response.

The thesis under test is *ossification / the transition tax*: the cost of
never retiring anything. The per-pair headline is the **transition ratio**
``modern_present / (modern_present + legacy_present)`` -- how far the corpus
has moved from the legacy header to its replacement. ``neither`` is reported
for context but excluded from the ratio's denominator, so the population of
responses that never carried either side can't drown the signal (one of the
issue's stated concerns).

Detection is not always header-presence. The modern side is sometimes a
*directive inside* a header: ``frame-ancestors`` lives in CSP, ``max-age``
and ``no-cache`` in ``Cache-Control``. Each pair therefore carries a small
predicate over a :class:`TransitionContext` built once per response (present
header names + parsed CSP/Cache-Control directive sets), so the per-pair
checks are cheap set lookups and httplint is never re-parsed. The CSP cases
depend on the directive-name extraction here (closed issue #5 added the
subnote traversal that surfaces CSP strength notes; this reads the parsed
policy directly).

One pair -- ``X-XSS-Protection`` -- has no modern replacement (the header was
removed from browsers, not superseded). Its ``modern`` predicate is ``None``;
it can only land in ``legacy_only`` or ``neither``, and its transition ratio
is undefined (rendered as residual legacy usage rather than a ratio).

Cardinality is bounded by ``len(TRANSITION_PAIRS) * len(CATEGORIES)`` -- a
compile-time constant (currently 6 x 4 = 24 keys). Unlike the recipe-flavoured
views (Vary / Cache-Control / co-occurrence), there is *no* long tail to trim
and therefore no ``truncated_*`` flag: the key space cannot grow with the
corpus. Adding a pair to the config widens it by four keys, still bounded.

Serialized ``transition`` block (round-trips the JSONProtocol shuffle)::

    {
      "responses": int,                       # all responses (denominator)
      "pairs": {
        pair_key: {
          "occ":  {category: count, ...},     # one bucket per response
          "hlls": {category: [registers...]}, # distinct sites per bucket
        },
        ...
      },
    }
"""

from collections import Counter
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Set, Tuple

from cc_lint.cache_control import cc_directives, marginal_key
from cc_lint.hll import (
    HLL_P_PER_NOTE,
    hll_add,
    hll_estimate,
    hll_merge,
    make_registers,
)

# The four mutually-exclusive buckets a response falls into for a given pair.
# Order is the canonical display order (most-migrated to least-relevant).
CATEGORIES: Tuple[str, ...] = ("both", "modern_only", "legacy_only", "neither")


class TransitionContext(NamedTuple):
    """Per-response detection inputs, built once and shared across all pairs.

    ``headers`` is the set of present response-header names (lowercased).
    ``csp_directives`` / ``cc_directives`` are the directive names found inside
    the response's ``Content-Security-Policy`` / ``Cache-Control`` headers
    (lowercased, value-stripped). ``pragma_no_cache`` flags a ``Pragma`` header
    carrying the ``no-cache`` token (httplint has no Pragma parser, so this is
    read off the raw value).
    """

    headers: Set[str]
    csp_directives: Set[str]
    cc_directives: Set[str]
    pragma_no_cache: bool


Predicate = Callable[[TransitionContext], bool]


class TransitionPair(NamedTuple):
    """One deprecated/replacement pair and how to detect each side.

    ``key`` is the stable serialization id. ``legacy`` / ``modern`` are
    predicates over a :class:`TransitionContext`; ``modern`` is ``None`` for a
    legacy-only header with no replacement.
    """

    key: str
    legacy_label: str
    modern_label: str
    legacy: Predicate
    modern: Optional[Predicate]


def _has(name: str) -> Predicate:
    """Predicate: the named response header is present."""
    return lambda ctx: name in ctx.headers


def _csp_directive(name: str) -> Predicate:
    """Predicate: the named directive appears in a Content-Security-Policy."""
    return lambda ctx: name in ctx.csp_directives


def _cc_directive(name: str) -> Predicate:
    """Predicate: the named directive appears in a Cache-Control header."""
    return lambda ctx: name in ctx.cc_directives


# The curated pair list. Explicit and ordered (not derived) so the tracked set
# is a stable, reviewable surface; extend it consciously -- each pair adds four
# bounded keys. Detection notes per the issue: frame-ancestors lives inside
# CSP; max-age / no-cache inside Cache-Control; the rest are header-presence.
TRANSITION_PAIRS: Tuple[TransitionPair, ...] = (
    TransitionPair(
        "frame_options",
        "X-Frame-Options",
        "CSP frame-ancestors",
        _has("x-frame-options"),
        _csp_directive("frame-ancestors"),
    ),
    TransitionPair(
        "reporting",
        "Report-To",
        "Reporting-Endpoints",
        _has("report-to"),
        _has("reporting-endpoints"),
    ),
    TransitionPair(
        "expiry",
        "Expires",
        "Cache-Control: max-age",
        _has("expires"),
        _cc_directive("max-age"),
    ),
    TransitionPair(
        "permissions_policy",
        "Feature-Policy",
        "Permissions-Policy",
        _has("feature-policy"),
        _has("permissions-policy"),
    ),
    TransitionPair(
        "no_cache",
        "Pragma: no-cache",
        "Cache-Control: no-cache",
        lambda ctx: ctx.pragma_no_cache,
        _cc_directive("no-cache"),
    ),
    TransitionPair(
        "xss_protection",
        "X-XSS-Protection",
        "(no replacement)",
        _has("x-xss-protection"),
        None,
    ),
)

PAIRS_BY_KEY: Dict[str, TransitionPair] = {pair.key: pair for pair in TRANSITION_PAIRS}


# ---- per-response detection ------------------------------------------------


def _csp_directive_names(linter: Any) -> Set[str]:
    """Lowercased directive names across every Content-Security-Policy header.

    Reads httplint's parsed value -- a list of ``{directive: value}`` dicts,
    one per policy -- so cc-lint sees exactly the directives the linter parsed.
    Falls back to a raw split on ``;``/whitespace if the parsed shape is
    missing or unexpected, so a parse miss degrades to best-effort rather than
    silently reporting zero frame-ancestors.
    """
    names: Set[str] = set()
    try:
        handler = linter.headers.handlers.get("content-security-policy")
    except AttributeError:
        return names
    if handler is None:
        return names
    value = getattr(handler, "value", None)
    if isinstance(value, list):
        for policy in value:
            if isinstance(policy, dict):
                for directive in policy:
                    names.add(str(directive).strip().lower())
    if names:
        return names
    # Fallback: split the raw header text(s) into directive names.
    for raw in _raw_csp_values(linter):
        for clause in str(raw).split(";"):
            clause = clause.strip()
            if clause:
                names.add(clause.split()[0].lower())
    return names


def _raw_csp_values(linter: Any) -> List[str]:
    """Raw Content-Security-Policy header value(s) as they appeared on the wire."""
    out: List[str] = []
    try:
        items = linter.headers.text
    except AttributeError:
        return out
    for name, value in items:
        if str(name).lower() == "content-security-policy":
            out.append(str(value))
    return out


def _pragma_has_no_cache(header_values: List[str]) -> bool:
    """Whether any Pragma header value carries the ``no-cache`` token."""
    for value in header_values:
        for token in str(value).replace(",", " ").split():
            if token.strip().lower() == "no-cache":
                return True
    return False


def build_context(
    linter: Any, header_items: List[Tuple[str, Any]]
) -> TransitionContext:
    """Build the per-response detection context.

    ``header_items`` is the shared pre-lowercased ``(name, value)`` list from
    :meth:`StatsCollector.process_linter`, so headers are not re-walked.
    """
    headers = {name for name, _ in header_items}
    pragma_values = [value for name, value in header_items if name == "pragma"]
    cc_names = {marginal_key(token) for token in cc_directives(linter)}
    return TransitionContext(
        headers=headers,
        csp_directives=_csp_directive_names(linter),
        cc_directives=cc_names,
        pragma_no_cache=_pragma_has_no_cache(pragma_values),
    )


def categorize(pair: TransitionPair, ctx: TransitionContext) -> str:
    """Classify one response into a :data:`CATEGORIES` bucket for ``pair``."""
    has_legacy = pair.legacy(ctx)
    has_modern = pair.modern is not None and pair.modern(ctx)
    if has_legacy and has_modern:
        return "both"
    if has_modern:
        return "modern_only"
    if has_legacy:
        return "legacy_only"
    return "neither"


# ---- accumulation ----------------------------------------------------------


class TransitionStats:
    """Accumulate per-pair, per-category occurrence counts and per-site HLLs.

    The structure is bounded (pairs x categories), so a single default-precision
    HLL per bucket is affordable and there is no trim. ``precision`` is the HLL
    register precision for the per-bucket site HLLs.
    """

    def __init__(self, precision: int = HLL_P_PER_NOTE) -> None:
        self.precision = precision
        self.occ: Dict[str, Counter[str]] = {}
        self.hlls: Dict[str, Dict[str, List[int]]] = {}

    def add(self, pair_key: str, category: str, site: Optional[str]) -> None:
        self.occ.setdefault(pair_key, Counter())[category] += 1
        if site:
            per_cat = self.hlls.setdefault(pair_key, {})
            registers = per_cat.get(category)
            if registers is None:
                registers = make_registers(self.precision)
                per_cat[category] = registers
            hll_add(registers, self.precision, site)

    def to_dict(self) -> Dict[str, Any]:
        return {
            pair_key: {"occ": dict(counts), "hlls": self.hlls.get(pair_key, {})}
            for pair_key, counts in self.occ.items()
        }


# ---- merge -----------------------------------------------------------------


def merge_transition(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """Merge a serialized ``transition`` block into ``target`` in place.

    ``responses`` sums; per-pair, per-category occurrence counts sum and the
    per-bucket site HLLs union register-wise (idempotent for the site
    dimension, additive for occurrence -- callers must not double-feed a
    source). Bounded key space, so there is no trim to mirror.
    """
    target["responses"] = target.get("responses", 0) + int(source.get("responses", 0))
    tgt_pairs: Dict[str, Any] = target.setdefault("pairs", {})
    for pair_key, block in (source.get("pairs") or {}).items():
        tgt_block = tgt_pairs.setdefault(pair_key, {})
        tgt_occ: Dict[str, int] = tgt_block.setdefault("occ", {})
        for category, count in (block.get("occ") or {}).items():
            tgt_occ[category] = tgt_occ.get(category, 0) + int(count)
        tgt_hlls: Dict[str, List[int]] = tgt_block.setdefault("hlls", {})
        for category, registers in (block.get("hlls") or {}).items():
            existing = tgt_hlls.get(category)
            if existing is None:
                tgt_hlls[category] = list(registers)
            else:
                hll_merge(existing, registers)


# ---- report-time derivation ------------------------------------------------


def _union_hll(
    hlls: Dict[str, Any], categories: Tuple[str, ...]
) -> Optional[List[int]]:
    """Register-wise union of the named categories' HLLs, or None if absent.

    HLLs merge by per-register max, so unioning ``both`` with ``modern_only``
    yields the distinct-site estimate for "modern present" without
    double-counting sites that carried both sides.
    """
    merged: Optional[List[int]] = None
    for category in categories:
        registers = hlls.get(category)
        if isinstance(registers, list) and registers:
            if merged is None:
                merged = list(registers)
            else:
                hll_merge(merged, registers)
    return merged


def _hll_estimate(registers: Optional[List[int]]) -> Optional[int]:
    if registers:
        est = hll_estimate(registers)
        if est > 0:
            return est
    return None


def transition_rows(transition: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One summary row per configured pair, ranked by relevance (scoped occ).

    Joins :data:`TRANSITION_PAIRS` (labels, legacy-only flag) with the merged
    counts. For each pair computes the four occurrence buckets, the "present"
    rollups (``both`` counted on each side), the occurrence-level transition
    ratio over the scoped population (legacy or modern present), and the
    site-level estimates / ratio via HLL union. Pairs with no observations are
    still emitted (all zeros) so the report shows the full tracked set.
    """
    pairs_data: Dict[str, Any] = transition.get("pairs") or {}
    rows: List[Dict[str, Any]] = []
    for pair in TRANSITION_PAIRS:
        block = pairs_data.get(pair.key) or {}
        occ: Dict[str, int] = block.get("occ") or {}
        hlls: Dict[str, Any] = block.get("hlls") or {}
        both = int(occ.get("both", 0))
        modern_only = int(occ.get("modern_only", 0))
        legacy_only = int(occ.get("legacy_only", 0))
        neither = int(occ.get("neither", 0))
        modern_present = both + modern_only
        legacy_present = both + legacy_only
        scoped = modern_present + legacy_present
        ratio = (modern_present / scoped) if scoped else None

        modern_sites = _hll_estimate(_union_hll(hlls, ("both", "modern_only")))
        legacy_sites = _hll_estimate(_union_hll(hlls, ("both", "legacy_only")))
        both_sites = _hll_estimate(hlls.get("both"))
        site_scoped = (modern_sites or 0) + (legacy_sites or 0)
        site_ratio = ((modern_sites or 0) / site_scoped) if site_scoped else None

        rows.append(
            {
                "key": pair.key,
                "legacy_label": pair.legacy_label,
                "modern_label": pair.modern_label,
                "legacy_only_pair": pair.modern is None,
                "both": both,
                "modern_only": modern_only,
                "legacy_only": legacy_only,
                "neither": neither,
                "modern_present": modern_present,
                "legacy_present": legacy_present,
                "scoped": scoped,
                "ratio": ratio,
                "both_sites": both_sites,
                "modern_sites": modern_sites,
                "legacy_sites": legacy_sites,
                "site_ratio": site_ratio,
            }
        )
    rows.sort(key=lambda row: row["scoped"], reverse=True)
    return rows
