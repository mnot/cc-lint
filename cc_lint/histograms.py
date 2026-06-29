"""Coarse histogram buckets for numeric note vars.

The EMR pipeline pays per-row shuffle cost on every distinct var value,
so feeding raw numeric values into the vars dict would explode the
payload. Instead, the worker maps a numeric value into one of a small
fixed set of labelled buckets; mapper / reducer aggregation then treats
the bucket label like any other categorical var value. Roughness is
intentional -- these reports describe corpus-level shape, not
individual responses.
"""

from typing import Callable, List, Optional, Tuple

DURATION_BUCKETS: List[Tuple[str, Callable[[float], bool]]] = [
    ("<1 min", lambda s: s < 60),
    ("1-10 min", lambda s: s < 600),
    ("10-60 min", lambda s: s < 3600),
    ("1-24 hours", lambda s: s < 86400),
    ("1-7 days", lambda s: s < 7 * 86400),
    ("7-30 days", lambda s: s < 30 * 86400),
    ("30-365 days", lambda s: s < 365 * 86400),
    (">1 year", lambda _s: True),
]

# Corpus-wide lifetime/duration scale for the numeric-header histograms
# (issue #8). Unlike DURATION_BUCKETS (used by the note-gated histograms),
# this scale carries an explicit "0" bucket -- a zero max-age is a deliberate
# "do not reuse" signal, distinct from a sub-minute lifetime -- a "negative"
# bucket for anomalies (a max-age that parsed negative, or an Expires that
# predates Date, i.e. stale on arrival), and a ">10 years" overflow bucket so
# an absurd value (max-age=99999999999) lands somewhere instead of skewing the
# top bucket. Defined once so the seven corpus histograms stay comparable.
# Predicate order matters: "negative" and "0" are matched before the "<"
# thresholds.
LIFETIME_BUCKETS: List[Tuple[str, Callable[[float], bool]]] = [
    ("negative", lambda s: s < 0),
    ("0", lambda s: s == 0),
    ("<1 min", lambda s: s < 60),
    ("1-10 min", lambda s: s < 600),
    ("10-60 min", lambda s: s < 3600),
    ("1-24 hours", lambda s: s < 86400),
    ("1-7 days", lambda s: s < 7 * 86400),
    ("7-30 days", lambda s: s < 30 * 86400),
    ("30-365 days", lambda s: s < 365 * 86400),
    ("1-10 years", lambda s: s < 10 * 365 * 86400),
    (">10 years", lambda _s: True),
]

BYTE_BUCKETS: List[Tuple[str, Callable[[int], bool]]] = [
    ("<256 B", lambda b: b < 256),
    ("256-1023 B", lambda b: b < 1024),
    ("1-4 KB", lambda b: b < 4096),
    ("4-8 KB", lambda b: b < 8192),
    ("8-16 KB", lambda b: b < 16384),
    ("16-32 KB", lambda b: b < 32768),
    ("32+ KB", lambda _b: True),
]


def duration_bucket(seconds: float) -> str:
    for label, pred in DURATION_BUCKETS:
        if pred(seconds):
            return label
    return DURATION_BUCKETS[-1][0]


def byte_bucket(byte_size: int) -> str:
    for label, pred in BYTE_BUCKETS:
        if pred(byte_size):
            return label
    return BYTE_BUCKETS[-1][0]


def lifetime_bucket(seconds: float) -> str:
    for label, pred in LIFETIME_BUCKETS:
        if pred(seconds):
            return label
    return LIFETIME_BUCKETS[-1][0]


# Display order for the corpus-wide value histograms (issue #8). All seven
# share the single lifetime scale, so they render with comparable rows.
LIFETIME_BUCKET_ORDER: List[str] = [label for label, _ in LIFETIME_BUCKETS]


_BUCKET_VAR_ORDER = {
    "freshness_left_bucket": [label for label, _ in DURATION_BUCKETS],
    "duration_bucket": [label for label, _ in DURATION_BUCKETS],
    "cookie_value_size_bucket": [label for label, _ in BYTE_BUCKETS],
    "field_size_bucket": [label for label, _ in BYTE_BUCKETS],
}


def bucket_order(var_name: str) -> Optional[List[str]]:
    """Display order for a histogram var, or None for non-histogram vars."""
    return _BUCKET_VAR_ORDER.get(var_name)
