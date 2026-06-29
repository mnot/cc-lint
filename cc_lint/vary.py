"""``Vary`` composition tabulation.

Issue #3: the report measured ``Vary`` cardinality but never tabulated
*composition* -- which request field-names actually appear in ``Vary``,
how often, in what combinations, and which tokens are non-standard
(synthetic cache-key engineering at the edge).

The unit is the **recipe**: the normalised (lowercased, deduped, sorted)
set of field-names in a response's ``Vary`` header. Recipes are the
primary artifact -- the synthetic-cache-key signal lives in the whole
tuple (``accept-encoding, x-ab-bucket, x-device``), not in the marginal
"``x-ab-bucket`` appears N times". Per-field marginals are kept as a
rollup alongside.

This module is the ``Vary``-specific normalisation layer; the generic
collect / merge / trim machinery lives in :mod:`cc_lint.recipes` so a
future ``Cache-Control``-recipe view (#9) can reuse it.

Serialized ``vary`` block::

    {
      "responses_with_vary": int,
      "recipes":   {"occ": {...}, "hlls": {...}},   # full token-set
      "marginals": {"occ": {...}, "hlls": {...}},   # per field-name
      "recipes_truncated":   bool,                  # set by trim
      "marginals_truncated": bool,
    }
"""

import functools
import importlib
import pkgutil
from typing import Any, Dict, List

from cc_lint.recipes import (
    merge_recipe_dict,
    recipe_key,
    recipe_tokens,
    trim_recipe_dict,
)

# The wildcard token is the cache opt-out, not a field-name. It is kept in
# the recipe string (so "*" shows up as its own recipe) but excluded from
# the per-field marginals and from synthetic-token flagging.
ASTERISK = "*"

# accept-encoding dominates Vary (~90%+ of headers), so it flattens the
# recipe ranking. The report shows recipes both raw and with this token
# factored out, so the meaningful axes surface.
ACCEPT_ENCODING = "accept-encoding"

# The high-interest axes called out explicitly in issue #3, in report order.
HIGH_INTEREST_AXES = ["cookie", "accept-language", "accept-encoding", "user-agent"]

# Placeholder recipe key for responses whose Vary was *only* accept-encoding
# after that token is factored out. Not a real token, so renderers treat it
# as a plain label rather than a (mis-classified) synthetic token.
AE_ONLY_LABEL = "(accept-encoding only)"


def vary_tokens(linter: Any) -> List[str]:
    """Return the parsed ``Vary`` field-name tokens for a finished linter.

    Reuses httplint's own parsed value (lowercased, deduped, and merged
    across repeated ``Vary`` headers) rather than re-splitting the raw
    text, so cc-lint sees exactly what the linter saw. Returns ``[]`` when
    there is no ``Vary`` header or it parsed to nothing.
    """
    try:
        handler = linter.headers.handlers.get("vary")
    except AttributeError:
        return []
    if handler is None:
        return []
    value = getattr(handler, "value", None)
    if not value:
        return []
    tokens: List[str] = []
    for token in value:
        token_str = str(token).strip().lower()
        if token_str:
            tokens.append(token_str)
    return tokens


# ---- token classification --------------------------------------------------

_PARSERS_IMPORTED = False


def _ensure_parsers_imported() -> None:
    """Import every httplint field parser so the finder can recognise them.

    ``HttpFieldFinder.find_handler_class`` resolves a field via
    ``sys.modules``, so a parser module that was never imported reads as
    "unknown". Importing them all once (only happens at report time, never
    in mappers) makes classification deterministic.
    """
    global _PARSERS_IMPORTED  # pylint: disable=global-statement
    if _PARSERS_IMPORTED:
        return
    parsers_pkg = importlib.import_module("httplint.field.parsers")
    for module_info in pkgutil.iter_modules(parsers_pkg.__path__):
        importlib.import_module(f"httplint.field.parsers.{module_info.name}")
    _PARSERS_IMPORTED = True


@functools.lru_cache(maxsize=None)
def is_registered_field(token: str) -> bool:
    """Whether ``token`` is a field-name httplint's registry recognises.

    This approximates "a registered HTTP field" (≈ IANA-registered). A
    token httplint does not recognise -- ``x-ab-bucket``, ``x-device``,
    ``x-edge-cache`` -- is treated as non-standard / synthetic. The
    distinction is an approximation, not a guarantee: a handful of real
    request headers httplint has no parser for (e.g. ``save-data``) read
    as non-standard, and it makes no use of httplint's unreliable
    ``valid_in_requests`` flag, so a registered *response* field appearing
    in ``Vary`` also counts as "registered". The report states this basis.
    """
    if token == ASTERISK or not token:
        return False
    _ensure_parsers_imported()
    finder = importlib.import_module("httplint.field.finder")
    return finder.HttpFieldFinder.find_handler_class(token) is not None


def is_nonstandard_token(token: str) -> bool:
    """A real field-name token (not ``*``) that httplint doesn't recognise."""
    return token != ASTERISK and not is_registered_field(token)


def count_nonstandard(recipe: str) -> int:
    """Number of non-standard / synthetic tokens in a recipe string."""
    return sum(1 for token in recipe_tokens(recipe) if is_nonstandard_token(token))


# ---- report-time derivation ------------------------------------------------


def factor_out(
    recipe_dict: Dict[str, Any], drop_token: str, empty_label: str
) -> Dict[str, Any]:
    """Regroup recipes with ``drop_token`` removed from each.

    Recipes that become identical after dropping the token are merged:
    occurrence counts sum and per-site HLLs union (a valid, exact HLL
    merge -- this is why per-site survives the regrouping). A recipe that
    consisted solely of ``drop_token`` collapses to ``empty_label``.
    Returns a fresh serialized recipe dict; the input is not mutated.
    """
    out: Dict[str, Any] = {"occ": {}, "hlls": {}}
    occ: Dict[str, int] = recipe_dict.get("occ", {})
    hlls: Dict[str, List[int]] = recipe_dict.get("hlls", {})
    for recipe, count in occ.items():
        remaining = [t for t in recipe_tokens(recipe) if t != drop_token]
        new_key = recipe_key(remaining) if remaining else empty_label
        single = {
            "occ": {new_key: int(count)},
            "hlls": {new_key: hlls[recipe]} if recipe in hlls else {},
        }
        merge_recipe_dict(out, single)
    return out


# ---- collect / merge / trim ------------------------------------------------


def merge_vary(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """Merge a serialized ``vary`` block into ``target`` in place."""
    target["responses_with_vary"] = target.get("responses_with_vary", 0) + int(
        source.get("responses_with_vary", 0)
    )
    for sub in ("recipes", "marginals"):
        if sub in source:
            merge_recipe_dict(target.setdefault(sub, {}), source[sub])
        if source.get(f"{sub}_truncated"):
            target[f"{sub}_truncated"] = True


def trim_vary(vary: Dict[str, Any], top_k: int) -> None:
    """Cap the recipe and marginal long tails, setting sticky flags."""
    for sub in ("recipes", "marginals"):
        recipe_dict = vary.get(sub)
        if recipe_dict and trim_recipe_dict(recipe_dict, top_k):
            vary[f"{sub}_truncated"] = True
