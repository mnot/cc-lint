"""Redaction of on-the-wire secrets before they ride the report / shuffle (#28).

Captured per-field sample values and the note-level sample pool both surface
slices of raw response headers. Public response headers on top sites are
low-risk, but some carry credentials -- a session cookie, a bearer token, a
signed-URL signature in a ``Reporting-Endpoints`` value. This module is the one
place that decides what gets scrubbed; ``cc_lint.stats.create_sample`` routes
both capture paths through it.
"""

import re
from typing import FrozenSet, Tuple

# Headers whose *value* is a credential in its entirety -- a session cookie, a
# bearer token, an auth challenge. The sample still records that the note fired
# (URL + site), but never the on-the-wire value. Matched against the lowercased
# field name.
SENSITIVE_FIELD_NAMES: FrozenSet[str] = frozenset(
    "set-cookie set-cookie2 cookie authorization proxy-authorization "
    "www-authenticate proxy-authenticate".split()
)

# Note vars httplint populates with a raw slice of the offending header value
# (generic validators set `value`; structured-field parse errors set `context`
# to a ~70-char window around the error). Dropped from the note-level pool when
# the offending header is itself a credential.
RAW_VALUE_NOTE_VARS: FrozenSet[str] = frozenset({"value", "context"})

# Query-string parameters whose value is a signature / token / key. A captured
# header value can embed a signed URL (e.g. a `Reporting-Endpoints` endpoint
# `https://.../reports?...&s=<sig>`); we redact the credential while keeping the
# rest legible. Matched case-insensitively against `?name=`/`&name=`.
_SENSITIVE_QUERY_PARAMS: Tuple[str, ...] = tuple(
    "signature sig s token access_token auth key apikey api_key hmac "
    "x-amz-signature x-amz-security-token x-goog-signature".split()
)
_SIGNATURE_PARAM_RE = re.compile(
    r"([?&](?:" + "|".join(re.escape(p) for p in _SENSITIVE_QUERY_PARAMS) + r")=)"
    r"[^&\s]+",
    re.IGNORECASE,
)


def redact_sensitive_value(value: str) -> str:
    """Redact signature/token query parameters embedded in a captured value."""
    return _SIGNATURE_PARAM_RE.sub(r"\1[redacted]", value)
