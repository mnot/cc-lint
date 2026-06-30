"""Response-header co-occurrence tabulation (issue #6).

Which response headers travel together. The thesis under test is
*defaultability*: do security/policy headers arrive as a *bundle* (e.g.
``Strict-Transport-Security`` + ``X-Frame-Options`` +
``X-Content-Type-Options``), and is that bundle tied to a particular
CDN/framework default? Conditioning the bundles on the infrastructure
fingerprint (#4) is the high-value view -- "platform X's default header
set."

The unit is the **bundle**: the subset of a curated header alphabet (loaded
from ``cooccur_alphabet.toml`` via :func:`default_security_headers`; the
default is the security/policy posture set) that is present on a response,
normalised as a recipe (sorted, deduped, ``", "``-joined). Bundles reuse
the generic collect / merge / trim machinery in :mod:`cc_lint.recipes`,
exactly as :mod:`cc_lint.vary` does; this module is just the
header-specific normalisation layer.

Cardinality discipline (issue #6's first concern). Bundling over *all*
present headers is ~unique per response and unbounded across the corpus.
Restricting to a fixed ~16-name alphabet bounds the bundle space: at most
``2**len(alphabet)`` keys in theory, a few hundred in practice, and
the ``TOP_K`` trim caps the tail regardless. Three measures ride along, all
bounded by the same alphabet:

- **bundles** -- one recipe per response (the empty bundle ``(none)`` is a
  real, headline-bearing data point: responses carrying *no* security
  header at all). Per-occurrence count + per-site HLL.
- **marginals** -- per individual header present. The denominators for
  conditional lifts.
- **pairs** -- sorted 2-tuples of co-present headers, bounded at
  ``C(len(alphabet), 2)`` keys. Gives exact conditional lifts
  ``P(A|B) = pairs(A,B) / marginals(B)`` without summing over the
  (trim-lossy) bundle distribution.

plus the infra-conditioning dimension:

- **by_layer** -- ``bundle -> fingerprint-layer -> occurrence count``. Plain
  integer counts (no HLL), bounded by alphabet x fingerprint-table size,
  mirroring ``field_counts_by_layer`` / per-note ``by_layer``. Inverting it
  at report time yields each layer's modal bundle: its default header set.

The denominator throughout is *all* responses (every response contributes a
bundle, possibly ``(none)``), so shares are directly comparable.

Serialized ``cooccur`` block (round-trips the JSONProtocol shuffle)::

    {
      "responses": int,                              # all responses
      "bundles":   {"occ": {...}, "hlls": {...}},    # subset present; coarse HLL
      "marginals": {"occ": {...}, "hlls": {...}},    # per-header; default HLL
      "pairs":     {"occ": {...}, "hlls": {...}},    # sorted 2-tuples; coarse HLL
      "by_layer":  {bundle: {layer: count, ...}, ...},
      "bundles_truncated":   bool,
      "marginals_truncated": bool,
      "pairs_truncated":     bool,
    }
"""

import sys
from collections import Counter
from functools import lru_cache
from importlib.resources import files
from itertools import combinations
from typing import Any, Dict, Iterable, List, Optional, Tuple

from cc_lint.recipes import (
    merge_recipe_dict,
    recipe_key,
    recipe_tokens,
    trim_recipe_dict,
)

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.9/3.10
    import tomli as tomllib

# Recipe label for a response that carried none of the alphabet's headers.
# Not a header name, so renderers treat it as a plain label.
EMPTY_BUNDLE_LABEL = "(none)"


def _parse_alphabet(data: Dict[str, Any]) -> Tuple[str, ...]:
    """Validate a parsed ``cooccur_alphabet.toml`` and return the header tuple.

    The alphabet must be a non-empty array of non-empty strings; names are
    lowercased (to match cc-lint's normalised header names) and de-duplicated
    while preserving order, so the bundle space stays a stable, reviewable set.
    """
    raw = data.get("headers")
    if not isinstance(raw, list) or not raw:
        raise ValueError("cooccur alphabet: 'headers' must be a non-empty array")
    if not all(isinstance(name, str) and name for name in raw):
        raise ValueError("cooccur alphabet: 'headers' must be non-empty strings")
    seen: Dict[str, None] = {}
    for name in raw:
        seen.setdefault(name.lower(), None)
    return tuple(seen)


def load_security_headers(path: Optional[str] = None) -> Tuple[str, ...]:
    """Load and validate the co-occurrence header alphabet.

    With ``path`` unset, reads the table shipped inside the package; pass a
    path to override it (mirrors :func:`cc_lint.fingerprint.load_fingerprinter`).
    Raises ``ValueError`` on a malformed table so a bad edit fails fast.
    """
    if path is not None:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    else:
        text = (files("cc_lint") / "cooccur_alphabet.toml").read_text(encoding="utf-8")
        data = tomllib.loads(text)
    return _parse_alphabet(data)


@lru_cache(maxsize=1)
def default_security_headers() -> Tuple[str, ...]:
    """The packaged co-occurrence alphabet, loaded once per process.

    Falls back to a ``cooccur_alphabet.toml`` in the working directory if the
    packaged resource can't be read -- on EMR the table is shipped via
    ``--files`` (which lands it in the mapper's cwd), mirroring how
    :func:`cc_lint.fingerprint.default_fingerprinter` finds its table.
    """
    try:
        return load_security_headers()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return load_security_headers("cooccur_alphabet.toml")


@lru_cache(maxsize=1)
def _alphabet_set() -> "frozenset[str]":
    """Membership set for the alphabet, built once (mapper hot path)."""
    return frozenset(default_security_headers())


def present_security_headers(header_names: Iterable[str]) -> List[str]:
    """Sorted, deduped subset of ``header_names`` that is in the alphabet.

    ``header_names`` are expected already lowercased (cc-lint normalises
    header names before they reach here).
    """
    alphabet = _alphabet_set()
    return sorted({name for name in header_names if name in alphabet})


def bundle_key(present: List[str]) -> str:
    """Recipe string for a present-subset, or ``EMPTY_BUNDLE_LABEL`` if empty."""
    return recipe_key(present) if present else EMPTY_BUNDLE_LABEL


def pair_keys(present: List[str]) -> Iterable[str]:
    """Yield the sorted 2-tuple recipe key for each co-present header pair."""
    for first, second in combinations(present, 2):
        yield recipe_key([first, second])


# ---- merge / trim ----------------------------------------------------------


def merge_cooccur(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """Merge a serialized ``cooccur`` block into ``target`` in place."""
    target["responses"] = target.get("responses", 0) + int(source.get("responses", 0))
    for sub in ("bundles", "marginals", "pairs"):
        if sub in source:
            merge_recipe_dict(target.setdefault(sub, {}), source[sub])
        if source.get(f"{sub}_truncated"):
            target[f"{sub}_truncated"] = True
    src_by_layer: Dict[str, Dict[str, int]] = source.get("by_layer") or {}
    if src_by_layer:
        tgt_by_layer: Dict[str, Dict[str, int]] = target.setdefault("by_layer", {})
        for bundle, layers in src_by_layer.items():
            per = tgt_by_layer.setdefault(bundle, {})
            for layer, count in layers.items():
                per[layer] = per.get(layer, 0) + int(count)


def trim_cooccur(cooccur: Dict[str, Any], top_k: int) -> None:
    """Cap the bundle / marginal / pair tails and prune ``by_layer`` to match.

    ``by_layer`` is keyed by bundle, so any bundle dropped from the bundle
    head is dead weight there too; prune it to the surviving bundle set so a
    trimmed bundle can't leak back in through the infra-conditioning view.
    """
    for sub in ("bundles", "marginals", "pairs"):
        recipe_dict = cooccur.get(sub)
        if recipe_dict and trim_recipe_dict(recipe_dict, top_k):
            cooccur[f"{sub}_truncated"] = True
    by_layer = cooccur.get("by_layer")
    if by_layer:
        surviving = cooccur.get("bundles", {}).get("occ", {})
        for bundle in list(by_layer.keys()):
            if bundle not in surviving:
                del by_layer[bundle]


# ---- report-time derivation ------------------------------------------------


def empty_bundle_count(cooccur: Dict[str, Any]) -> int:
    """Occurrences of the empty bundle -- responses with no security header."""
    occ: Dict[str, int] = cooccur.get("bundles", {}).get("occ", {})
    return int(occ.get(EMPTY_BUNDLE_LABEL, 0))


def ranked_bundles(cooccur: Dict[str, Any]) -> List[Tuple[str, int]]:
    """Bundles by occurrence, descending."""
    occ: Dict[str, int] = cooccur.get("bundles", {}).get("occ", {})
    return sorted(occ.items(), key=lambda kv: kv[1], reverse=True)


def ranked_marginals(cooccur: Dict[str, Any]) -> List[Tuple[str, int]]:
    """Individual headers by occurrence, descending."""
    occ: Dict[str, int] = cooccur.get("marginals", {}).get("occ", {})
    return sorted(occ.items(), key=lambda kv: kv[1], reverse=True)


def conditional_lifts(cooccur: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    """Top co-occurring header pairs with conditional probabilities and lift.

    For a pair ``(a, b)`` with joint count ``j``, marginals ``na``/``nb`` and
    ``responses`` total: ``P(a|b) = j/nb``, ``P(b|a) = j/na``, and
    ``lift = j*responses / (na*nb)`` (>1 means they co-occur more than chance
    would predict -- the "travel together" signal). Ranked by joint count so
    the common bundles' constituent pairs surface first.
    """
    responses = int(cooccur.get("responses", 0))
    pair_occ: Dict[str, int] = cooccur.get("pairs", {}).get("occ", {})
    marg_occ: Dict[str, int] = cooccur.get("marginals", {}).get("occ", {})
    ordered = sorted(pair_occ.items(), key=lambda kv: kv[1], reverse=True)
    out: List[Dict[str, Any]] = []
    for pair, joint in ordered[:limit]:
        tokens = recipe_tokens(pair)
        if len(tokens) != 2:
            continue
        header_a, header_b = tokens
        na = marg_occ.get(header_a, 0)
        nb = marg_occ.get(header_b, 0)
        out.append(
            {
                "a": header_a,
                "b": header_b,
                "joint": int(joint),
                "p_a_given_b": (joint / nb) if nb else 0.0,
                "p_b_given_a": (joint / na) if na else 0.0,
                "lift": (joint * responses / (na * nb)) if (na and nb) else 0.0,
            }
        )
    return out


def layer_default_bundles(cooccur: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Each fingerprint layer's modal (most common) bundle -- its default set.

    Inverts ``by_layer`` (bundle -> layer -> count) into per-layer bundle
    distributions, then reports each layer's top bundle and its share of that
    layer's fingerprinted responses. Layers are ranked by response volume.
    """
    by_layer: Dict[str, Dict[str, int]] = cooccur.get("by_layer") or {}
    layer_totals: Counter[str] = Counter()
    layer_bundles: Dict[str, Counter[str]] = {}
    for bundle, layers in by_layer.items():
        for layer, count in layers.items():
            layer_totals[layer] += int(count)
            layer_bundles.setdefault(layer, Counter())[bundle] += int(count)
    out: List[Dict[str, Any]] = []
    for layer, total in layer_totals.most_common():
        if total <= 0:
            continue
        bundle, count = layer_bundles[layer].most_common(1)[0]
        out.append(
            {
                "layer": layer,
                "responses": total,
                "bundle": bundle,
                "share": count / total,
            }
        )
    return out
