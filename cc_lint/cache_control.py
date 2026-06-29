"""``Cache-Control`` recipe tabulation.

Issue #9: tabulate the normalised ``Cache-Control`` *recipes* operators
actually ship, ranked by frequency, to test the cargo-cult-caching thesis
directly. Where ``CC_CONFLICTING`` flags a single conflict, this shows the
whole recipe -- if the kitchen-sink ``max-age=N, must-revalidate, no-cache,
no-store, private`` is a top recipe, that is the slide.

The unit is the **recipe**: the normalised set of directives in a
response's ``Cache-Control`` header. Normalisation is the central tension
(see the issue): we must collapse the value space so the recipe space stays
bounded, while preserving the *shape*. Each directive becomes a token:

- a value-less directive is its bare name (``no-store``, ``must-revalidate``);
- a directive carrying any value is ``name=N`` -- the value is elided so
  ``max-age=0`` and ``max-age=31536000`` share one recipe. The actual
  numbers are a separate, coarse histogram (issue #8).

Per-field **marginals** (keyed on the bare directive name) are kept as a
rollup alongside, answering "how prevalent is each directive, regardless of
value / combination".

This module is the ``Cache-Control``-specific normalisation layer; the
generic collect / merge / trim machinery lives in :mod:`cc_lint.recipes`,
shared with :mod:`cc_lint.vary`.

Serialized ``cache_control`` block::

    {
      "responses_with_cc": int,
      "recipes":   {"occ": {...}, "hlls": {...}},   # shaped directive set
      "marginals": {"occ": {...}, "hlls": {...}},   # per bare directive name
      "recipes_truncated":   bool,                  # set by trim
      "marginals_truncated": bool,
    }
"""

from typing import Any, Dict, List

from httplint.field.parsers.cache_control import KNOWN_CC

from cc_lint.recipes import merge_recipe_block, recipe_tokens, trim_recipe_block

# The serialized block's response-count scalar (the denominator for "% of
# Cache-Control"). Named distinctly from Vary's so the two blocks never alias.
COUNT_FIELD = "responses_with_cc"

# Placeholder for an elided directive value. httplint hands us the parsed
# value (int for max-age et al., the unquoted string for no-cache/private,
# the raw string for unknown directives); we keep only the fact that a value
# was present, never the value itself -- that is what bounds the recipe space.
VALUE_PLACEHOLDER = "N"

# httplint renders a value-less ``no-cache`` / ``private`` (both parsed with
# ``unquote_string``) as the literal string "None" -- ``unquote_string(None)``
# returns "None", not Python ``None``. Treat that sentinel as "no value" so
# bare ``no-cache`` normalises to ``no-cache``, not ``no-cache=N``.
_NONE_SENTINEL = "None"


def normalize_directive(name: str, value: Any) -> str:
    """Normalise one ``(directive, value)`` pair to a shaped recipe token.

    Value-less directives keep their bare (lowercased) name; any directive
    carrying a value becomes ``name=N``. See the module docstring for why
    the value is elided.
    """
    name = str(name).strip().lower()
    if value is None:
        return name
    if str(value) == _NONE_SENTINEL:
        return name
    return f"{name}={VALUE_PLACEHOLDER}"


def cc_directives(linter: Any) -> List[str]:
    """Return the shaped recipe tokens for a finished linter's Cache-Control.

    Reuses httplint's own parsed value (a list of ``(directive, value)``
    tuples, directive names already lowercased) rather than re-splitting the
    raw text, so cc-lint sees exactly what the linter saw -- including its
    handling of quoted values that may contain commas. Returns ``[]`` when
    there is no ``Cache-Control`` header or it parsed to nothing.
    """
    try:
        handler = linter.headers.handlers.get("cache-control")
    except AttributeError:
        return []
    if handler is None:
        return []
    value = getattr(handler, "value", None)
    if not value:
        return []
    tokens: List[str] = []
    for entry in value:
        try:
            name, directive_value = entry
        except (TypeError, ValueError):
            continue
        if not str(name).strip():
            continue
        tokens.append(normalize_directive(name, directive_value))
    return tokens


def marginal_key(token: str) -> str:
    """The bare directive name for a shaped token (``max-age=N`` -> ``max-age``)."""
    return token.split("=", 1)[0]


def is_nonstandard_directive(token: str) -> bool:
    """Whether a token's directive is one httplint's parser doesn't define.

    ``KNOWN_CC`` is httplint's registry of RFC 9111 (+ the IE pre/post-check)
    directives. Anything outside it -- ``surrogate-control``-style vendor
    knobs, typos like ``s-max-age``, edge-CDN extensions -- reads as
    non-standard, which is the synthetic / cargo-cult signal. The bare name
    is matched so ``stale-while-revalidate=N`` classifies on its directive,
    not its shaped form.
    """
    return marginal_key(token) not in KNOWN_CC


def count_nonstandard(recipe: str) -> int:
    """Number of non-standard directives in a recipe string."""
    return sum(1 for token in recipe_tokens(recipe) if is_nonstandard_directive(token))


def merge_cache_control(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """Merge a serialized ``cache_control`` block into ``target`` in place."""
    merge_recipe_block(target, source, COUNT_FIELD)


def trim_cache_control(cache_control: Dict[str, Any], top_k: int) -> None:
    """Cap the recipe and marginal long tails, setting sticky flags."""
    trim_recipe_block(cache_control, top_k)
