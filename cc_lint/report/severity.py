"""Severity classification and reachability for httplint notes.

The report distinguishes three kinds of notes for "Unseen note types":

- Reachable but didn't fire on the analysed responses (the interesting case).
- Body-only notes that cc-lint's WAT pipeline can't reach because it reads
  headers only.
- Request-only notes that cc-lint can't reach because we run
  HttpResponseLinter against response metadata.

The two REACHABLE / BODY_ONLY / REQUEST_ONLY denylists are curated by reading
httplint's source; rather than re-derive on every run, we keep them as
explicit constants so the report can label the buckets clearly.
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from httplint.note import Note, categories, levels


# Stable ordering of severities for sorting and for the "highest first"
# tie-break inside category sections.
SEVERITY_ORDER = {"bad": 4, "warn": 3, "info": 2, "good": 1}


def _all_note_subclasses(cls: Any) -> Set[Any]:
    return set(cls.__subclasses__()).union(
        {s for c in cls.__subclasses__() for s in _all_note_subclasses(c)}
    )


def _level_to_severity(level: Any) -> Optional[str]:
    if level == levels.BAD:
        return "bad"
    if level == levels.WARN:
        return "warn"
    if level == levels.INFO:
        return "info"
    if level == levels.GOOD:
        return "good"
    return None


def build_severity_index() -> Dict[str, str]:
    """Map note class name -> severity string ('bad'/'warn'/'info'/'good').

    Excludes Note subclasses defined inside our own tests package so test-shim
    mocks don't show up in the rendered report.
    """
    index: Dict[str, str] = {}
    for note_cls in _all_note_subclasses(Note):
        if note_cls.__module__.startswith("tests."):
            continue
        severity = _level_to_severity(getattr(note_cls, "level", None))
        if severity is not None:
            index[note_cls.__name__] = severity
    return index


def build_category_index() -> Dict[str, str]:
    """Map note class name -> category enum name (e.g. 'CACHING').

    Read from the class attribute, so notes whose category is overridden
    at firing time will still display under their declared class category.
    Acceptable approximation for the per-category report sections.
    """
    index: Dict[str, str] = {}
    for note_cls in _all_note_subclasses(Note):
        if note_cls.__module__.startswith("tests."):
            continue
        category = getattr(note_cls, "category", None)
        if isinstance(category, categories):
            index[note_cls.__name__] = category.name
    return index


def build_summary_index() -> Dict[str, str]:
    """Map note class name -> raw summary template from httplint.

    The template may contain ``%(var_name)s`` placeholders. We surface it
    raw so the report can show what each note means without needing a
    fired instance.
    """
    index: Dict[str, str] = {}
    for note_cls in _all_note_subclasses(Note):
        if note_cls.__module__.startswith("tests."):
            continue
        summary = getattr(note_cls, "_summary", "") or ""
        if summary:
            index[note_cls.__name__] = summary
    return index


def category_display_order() -> List[str]:
    """Return category enum names in the order we want to render them.

    Roughly: protocol correctness up front, then content / negotiation,
    then GENERAL last as a catch-all.
    """
    preferred = [
        "CACHING",
        "SECURITY",
        "COOKIES",
        "CORS",
        "CONNEG",
        "RANGE",
        "VALIDATION",
        "CONNECTION",
        "GENERAL",
    ]
    known = {member.name for member in categories}
    # Preserve preferred order; append any enum values we didn't list.
    seen = set(preferred)
    return preferred + [name for name in sorted(known) if name not in seen]


def possible_note_ids(severity_index: Dict[str, str]) -> Set[str]:
    return set(severity_index.keys())


# Notes whose firing paths in httplint are only reached when linting a request
# (cc-lint feeds an HttpResponseLinter from WAT response metadata, so these
# can never fire on our pipeline).
REQUEST_ONLY_NOTES: Set[str] = {
    "MISSING_USER_AGENT",
    "REQUEST_CONTENT_NOT_DEFINED",
    "URI_BAD_SYNTAX",
    "URI_TOO_LONG",
    "RESPONSE_HDR_IN_REQUEST",
    "CORS_PREFLIGHT_REQUEST",
    "CORS_PREFLIGHT_REQ_METHOD_WRONG",
    "CORS_PREFLIGHT_REQ_NO_ORIGIN",
    "CORS_PREFLIGHT_REQ_NO_METHOD",
}

# Notes that only fire when the response body is fed to the linter. cc-lint's
# WAT pipeline reads headers only; the body-derived findings below cannot fire
# until/unless we add a full-WARC mode. Listed explicitly so they don't bloat
# the Unseen list.
BODY_ONLY_NOTES: Set[str] = {
    "BAD_GZIP",
    "BAD_BROTLI",
    "BAD_ZLIB",
    "CL_INCORRECT",
}


def classify_unseen(
    possible_ids: Set[str], seen_ids: Set[str]
) -> Tuple[List[str], List[str], List[str]]:
    """Split unseen note ids into reachable / request-only / body-only buckets."""
    unseen = possible_ids - seen_ids
    request_only = sorted(unseen & REQUEST_ONLY_NOTES)
    body_only = sorted(unseen & BODY_ONLY_NOTES)
    reachable_unseen = sorted(unseen - REQUEST_ONLY_NOTES - BODY_ONLY_NOTES)
    return reachable_unseen, request_only, body_only
