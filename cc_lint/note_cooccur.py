"""Note co-occurrence tabulation (issue #7).

Which *findings* clump on the same response. The thesis under test is the
cargo-cult / hand-rolling story: defects cluster rather than scatter -- a
malformed ``Via`` rides with a particular ``Server``, the quoting curse
fires across several headers at once, ``CC_CONFLICTING`` travels with a
legacy ``Pragma``. The unit is the **note** (an httplint finding), so this
is the same bounded co-occurrence component as header co-occurrence
(:mod:`cc_lint.cooccur`, issue #6) pointed at a different key space: the
generic block collect / merge / trim / lift machinery is reused verbatim
(:func:`cc_lint.cooccur.merge_cooccur`, :func:`~cc_lint.cooccur.trim_cooccur`,
:func:`~cc_lint.cooccur.conditional_lifts`, …); this module is only the
note-specific normalisation layer.

Two things make notes different from the header alphabet:

- **Severity gate.** The thesis is about *defects*, so by default only
  ``bad`` / ``warn`` notes participate (:data:`NOTE_COOCCUR_SEVERITIES`).
  ``good`` / ``info`` notes (e.g. "a Date header is present") are ubiquitous
  and would drown the defect clusters in both the bundles and the lift
  table. The set is a single knob so the gate can be widened without
  touching the collection code.
- **Parent/child exclusion** (issue #5). httplint attaches a finding's
  strength/quality sub-findings as children (``CSP_UNSAFE_INLINE`` under
  ``CONTENT_SECURITY_POLICY`` etc.). A child *always* co-occurs with every
  ancestor on its branch, so those pairs are mechanical, not evidence;
  :func:`note_pair_keys` drops any pair whose two notes are in an
  ancestor/descendant relationship. The exclusion is purely structural --
  it holds regardless of the notes' severities and regardless of any
  excluded-severity note sitting between them on the branch (the caller
  threads ancestry through the *whole* tree). Siblings (two children of the
  same parent, e.g. ``CSP_UNSAFE_INLINE`` + ``CSP_UNSAFE_EVAL``) are *kept*:
  they do not always co-occur, so a sibling pair is a genuine clump.

The serialized ``note_cooccur`` block is the same shape the ``cooccur``
block uses minus the ``by_layer`` infra-conditioning dimension (which #7
does not call for)::

    {
      "responses":  int,                              # all responses
      "bundles":    {"occ": {...}, "hlls": {...}},    # fired defect set; coarse HLL
      "marginals":  {"occ": {...}, "hlls": {...}},    # per note; default HLL
      "pairs":      {"occ": {...}, "hlls": {...}},    # lineage-excluded 2-tuples
      "bundles_truncated":   bool,
      "marginals_truncated": bool,
      "pairs_truncated":     bool,
    }
"""

from itertools import combinations
from typing import FrozenSet, Iterable, Iterator, List, Set

from cc_lint.cooccur import bundle_key
from cc_lint.recipes import recipe_key

# The severity labels (see :func:`cc_lint.stats._level_to_severity`) that a
# note must carry to participate in note co-occurrence. Defaulting to the two
# defect severities keeps the bundles and lift table focused on the
# clump-of-defects thesis; widen it here to fold in ``info`` / ``good``.
NOTE_COOCCUR_SEVERITIES: FrozenSet[str] = frozenset({"bad", "warn"})


def note_bundle_key(fired: Iterable[str]) -> str:
    """Recipe string for the fired-note set, or ``(none)`` if empty.

    Reuses the header bundle normalisation: sorted, deduped, ``", "``-joined,
    with the shared empty-bundle label for a response that produced no
    participating note.
    """
    return bundle_key(sorted(set(fired)))


def note_pair_keys(fired: Iterable[str], lineage: Set[FrozenSet[str]]) -> Iterator[str]:
    """Yield the sorted 2-tuple recipe key for each co-fired note pair.

    ``lineage`` is the set of ``frozenset({ancestor_id, descendant_id})``
    edges observed on this response; any pair appearing there is a mechanical
    parent/child (or deeper ancestry) correlation and is skipped.
    """
    present: List[str] = sorted(set(fired))
    for first, second in combinations(present, 2):
        if frozenset((first, second)) not in lineage:
            yield recipe_key([first, second])
