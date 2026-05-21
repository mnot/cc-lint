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


_BUCKET_VAR_ORDER = {
    "freshness_left_bucket": [label for label, _ in DURATION_BUCKETS],
    "duration_bucket": [label for label, _ in DURATION_BUCKETS],
    "cookie_value_size_bucket": [label for label, _ in BYTE_BUCKETS],
    "field_size_bucket": [label for label, _ in BYTE_BUCKETS],
}


def bucket_order(var_name: str) -> Optional[List[str]]:
    """Display order for a histogram var, or None for non-histogram vars."""
    return _BUCKET_VAR_ORDER.get(var_name)
