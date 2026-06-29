"""Classify response header *names* into byte-economics categories (issue #10).

The report's header byte-economics section splits the total response-header
byte budget three ways: bytes carried by **standard** (registered) fields,
by **deprecated** fields, and by **proprietary** (non-registered) fields.
Classification is a report-time pass over the per-header byte dict, so it
reuses httplint's field knowledge rather than shipping a separate IANA
snapshot:

- ``deprecated`` -- the name is in httplint's deprecated/obsoleted field
  table (``httplint.field.deprecated.field_lookup``). httplint's finder maps
  these to ``DeprecatedField``, so ``is_registered_field`` would also call
  them "registered"; we check deprecation first so a deprecated field lands
  in its own bucket, not the standard one.
- ``standard`` -- httplint recognises the field (it has a parser, or it is a
  registered field). This is the same ``is_registered_field`` test the Vary
  section uses for "non-standard token", so the two stay consistent.
- ``proprietary`` -- everything else: a real field name httplint doesn't
  recognise (``x-amz-cf-id``, ``cf-ray``, ``x-vercel-cache`` ...).

This is the same standard/non-standard boundary issue #12 will build its
non-standard census on; both lean on ``is_registered_field`` rather than a
bundled registry.
"""

from typing import Dict, List, Tuple

from httplint.field import deprecated

from cc_lint.vary import is_registered_field

STANDARD = "standard"
DEPRECATED = "deprecated"
PROPRIETARY = "proprietary"

# Display / iteration order for the category breakdown.
CATEGORY_ORDER: List[str] = [STANDARD, DEPRECATED, PROPRIETARY]


def classify_header(name: str) -> str:
    """Bucket a (lowercased) response header name for byte accounting."""
    if name in deprecated.field_lookup:
        return DEPRECATED
    if is_registered_field(name):
        return STANDARD
    return PROPRIETARY


def categorize_header_bytes(field_bytes: Dict[str, int]) -> List[Tuple[str, int]]:
    """Sum per-header byte totals into the three categories, in display order.

    ``field_bytes`` is the top-K per-header byte dict, so the totals describe
    the head of the distribution, not the full long tail -- the report labels
    them accordingly.
    """
    totals: Dict[str, int] = {category: 0 for category in CATEGORY_ORDER}
    for name, byte_total in field_bytes.items():
        totals[classify_header(name.lower())] += int(byte_total)
    return [(category, totals[category]) for category in CATEGORY_ORDER]
