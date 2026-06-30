"""Non-standard header census (issue #12).

A report-time clustering of the proprietary (IANA-unregistered) response
header names the crawl saw. It answers the "incentives beat specs" question
-- how large is the vendor-owned ``x-`` namespace, and what de-facto
conventions are forming in it -- by clustering the non-standard header names
three ways:

* **by inferred vendor** -- the headline view, attributing each name to a
  vendor lexically via :meth:`Fingerprinter.vendor_for_name` (the same #4
  signal table the infrastructure section uses, so the two agree on vendor
  identity);
* **by semantic family** -- caching / observability / routing / security,
  from the data-driven ``header_families.toml`` table;
* **by literal prefix** -- auto-derived from the name (``x-amz-``, ``cf-``,
  ``x-vercel-``), so a *novel* prefix surfaces on its own without any table
  edit -- the discovery payoff.

Everything is derived from the already-merged ``unprocessed_counts`` (the
existing "Top Unsupported Headers" dict: non-standard name -> count of
responses carrying it) and ``field_bytes`` (name -> bytes, the #10
byte-economics dict). ``unprocessed_counts`` is the right source because it
is a *dedicated* top-K of only the non-standard names, so it keeps a deeper
proprietary tail through the shuffle than ``field_counts`` would (whose head
is mostly eaten by common registered headers). No per-response data and no
new shuffle key are needed; clustering is a pure function of the header name.

"Non-standard" is the same boundary the Vary synthetic-token flag and the
byte-economics proprietary bucket use:
:func:`cc_lint.header_categories.classify_header` returning ``PROPRIETARY``
(a name httplint has no parser for and that is not deprecated). The mapper
populates ``unprocessed_counts`` from httplint's ``UnknownHttpField``, which
is the same boundary; the report-time ``classify_header`` filter here is a
defensive normalisation (and reconciles any map-time/report-time httplint
version skew toward current knowledge).

**Head-only.** Both source dicts are the top-K head retained through the
shuffle (the long tail is trimmed at map time), so every cluster total is a
lower bound -- it surfaces the prominent members of a cluster, not its true
size. Accurate per-cluster totals would need the cluster key computed at map
time so the tail rolls up before truncation; that is a deliberate future
mapper-side enhancement, out of scope for this report-time census.

**Stable shape.** Header names are lowercased and every list is sorted
deterministically (count desc, then name/key asc) so a future longitudinal
diff across crawls is a trivial set-difference rather than a reformat.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files
from typing import Any, Dict, List, Optional, Tuple

from cc_lint.fingerprint import Fingerprinter, default_fingerprinter
from cc_lint.header_categories import PROPRIETARY, classify_header

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.9/3.10
    import tomli as tomllib


# Bucket keys for headers that matched no vendor / no semantic family. Kept
# distinct from any real id so callers can special-case the label.
UNATTRIBUTED = "__unattributed__"
OTHER_FAMILY = "__other__"

# How many member names to keep per cluster, and how many prefix clusters to
# surface. Caps keep the rendered tables readable; the full proprietary head
# is still summarised in the totals and the flat top-headers list.
DEFAULT_MEMBER_LIMIT = 8
DEFAULT_PREFIX_LIMIT = 30
DEFAULT_TOP_HEADERS = 50


class FamilyTable:
    """Semantic-family classifier + well-known tagging, from a TOML table."""

    def __init__(
        self,
        families: List[Tuple[str, str, Tuple[str, ...]]],
        well_known_prefixes: Tuple[str, ...],
        well_known_names: frozenset[str],
    ) -> None:
        # (id, label, prefixes), tried in order; first prefix match wins.
        self._families = families
        self._labels: Dict[str, str] = {fid: label for fid, label, _ in families}
        self._well_known_prefixes = well_known_prefixes
        self._well_known_names = well_known_names

    @property
    def family_order(self) -> List[str]:
        """Family ids in table order (display order), then the OTHER bucket."""
        return [fid for fid, _, _ in self._families] + [OTHER_FAMILY]

    def label(self, family_id: str) -> str:
        if family_id == OTHER_FAMILY:
            return "Other / unclassified"
        return self._labels.get(family_id, family_id)

    def classify(self, name: str) -> str:
        """Family id for a lowercased header name (OTHER_FAMILY if none)."""
        for family_id, _label, prefixes in self._families:
            for prefix in prefixes:
                if name == prefix or name.startswith(prefix):
                    return family_id
        return OTHER_FAMILY

    def is_well_known(self, name: str) -> bool:
        """Whether ``name`` is a de-facto-standard-but-unregistered header."""
        if name in self._well_known_names:
            return True
        return any(name.startswith(prefix) for prefix in self._well_known_prefixes)


def _parse_families(data: Dict[str, Any]) -> FamilyTable:
    raw_families = data.get("family", [])
    if not isinstance(raw_families, list) or not raw_families:
        raise ValueError("header_families table has no [[family]] entries")
    families: List[Tuple[str, str, Tuple[str, ...]]] = []
    seen: set[str] = set()
    for raw in raw_families:
        family_id = raw.get("id")
        if not isinstance(family_id, str) or not family_id:
            raise ValueError("header_families family missing 'id'")
        if family_id in seen:
            raise ValueError(f"duplicate header_families family id: {family_id!r}")
        seen.add(family_id)
        label = raw.get("label")
        if not isinstance(label, str) or not label:
            raise ValueError(f"header_families family {family_id!r}: missing 'label'")
        prefixes = raw.get("prefixes", [])
        if not isinstance(prefixes, list) or not all(
            isinstance(p, str) and p for p in prefixes
        ):
            raise ValueError(
                f"header_families family {family_id!r}: 'prefixes' must be "
                "an array of non-empty strings"
            )
        families.append((family_id, label, tuple(p.lower() for p in prefixes)))
    well_known = data.get("well_known", {})
    wk_prefixes_raw = well_known.get("prefixes", []) if well_known else []
    wk_names_raw = well_known.get("names", []) if well_known else []
    if not isinstance(wk_prefixes_raw, list) or not isinstance(wk_names_raw, list):
        raise ValueError("header_families [well_known] prefixes/names must be arrays")
    wk_prefixes = tuple(p.lower() for p in wk_prefixes_raw if isinstance(p, str) and p)
    wk_names = frozenset(n.lower() for n in wk_names_raw if isinstance(n, str) and n)
    return FamilyTable(families, wk_prefixes, wk_names)


def load_families(path: Optional[str] = None) -> FamilyTable:
    """Load and validate the semantic-family table.

    With ``path`` unset, reads the table shipped inside the package; pass a
    path to override it. Raises ``ValueError`` on a malformed table.
    """
    if path is not None:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    else:
        text = (files("cc_lint") / "header_families.toml").read_text(encoding="utf-8")
        data = tomllib.loads(text)
    return _parse_families(data)


@lru_cache(maxsize=1)
def default_families() -> FamilyTable:
    """The packaged family table, loaded once per process (cwd fallback)."""
    try:
        return load_families()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return load_families("header_families.toml")


def derive_prefix(name: str) -> str:
    """Auto-derive a clustering prefix from a lowercased header name.

    ``x-`` names group by their second segment (``x-amz-cf-id`` -> ``x-amz``,
    ``x-forwarded-for`` -> ``x-forwarded``, ``x-cache`` -> ``x-cache``); other
    names group by their first segment (``cf-ray`` -> ``cf``); a name with no
    hyphen is its own bucket. This means a brand-new vendor namespace forms a
    cluster the moment it appears, without any table edit.
    """
    tokens = name.split("-")
    if tokens[0] == "x" and len(tokens) >= 2:
        return f"x-{tokens[1]}"
    if len(tokens) >= 2:
        return tokens[0]
    return name


@dataclass(frozen=True)
class HeaderEntry:
    """One proprietary header name with its head-only totals."""

    name: str
    count: int
    byte_count: int
    vendor: Optional[str]
    well_known: bool


@dataclass(frozen=True)
class Cluster:
    """A group of proprietary headers under one axis key (vendor/family/...)."""

    key: str
    label: str
    count: int
    byte_count: int
    distinct: int
    members: List[HeaderEntry]


@dataclass(frozen=True)
class Census:
    """The full non-standard header census for one crawl (report-time)."""

    distinct_names: int
    total_count: int
    total_bytes: int
    well_known_names: int
    novel_names: int
    by_vendor: List[Cluster] = field(default_factory=list)
    by_family: List[Cluster] = field(default_factory=list)
    by_prefix: List[Cluster] = field(default_factory=list)
    top_headers: List[HeaderEntry] = field(default_factory=list)
    truncated: bool = False

    @property
    def has_data(self) -> bool:
        return self.distinct_names > 0


def _cluster(
    key: str,
    label: str,
    entries: List[HeaderEntry],
    member_limit: int,
) -> Cluster:
    members = sorted(entries, key=lambda e: (-e.count, e.name))
    return Cluster(
        key=key,
        label=label,
        count=sum(e.count for e in entries),
        byte_count=sum(e.byte_count for e in entries),
        distinct=len(entries),
        members=members[:member_limit],
    )


def _grouped(
    groups: Dict[str, List[HeaderEntry]],
    label_for: Any,
    order: Optional[List[str]],
    member_limit: int,
    limit: Optional[int],
) -> List[Cluster]:
    clusters = [
        _cluster(key, label_for(key), entries, member_limit)
        for key, entries in groups.items()
    ]
    if order is not None:
        rank = {key: idx for idx, key in enumerate(order)}
        clusters.sort(key=lambda c: rank.get(c.key, len(rank)))
    else:
        clusters.sort(key=lambda c: (-c.count, c.key))
    if limit is not None:
        clusters = clusters[:limit]
    return clusters


def build_census(
    nonstandard_counts: Dict[str, int],
    field_bytes: Dict[str, int],
    truncated: bool,
    fingerprinter: Optional[Fingerprinter] = None,
    families: Optional[FamilyTable] = None,
    member_limit: int = DEFAULT_MEMBER_LIMIT,
    prefix_limit: int = DEFAULT_PREFIX_LIMIT,
    top_headers: int = DEFAULT_TOP_HEADERS,
) -> Census:
    """Cluster the proprietary header names in ``nonstandard_counts``.

    ``nonstandard_counts`` is the merged ``unprocessed_counts`` head (name ->
    responses carrying it); ``field_bytes`` is consulted for byte share only.
    Names are lowercased and folded (so any case variants sum), then filtered
    to PROPRIETARY defensively. ``field_bytes`` keys are likewise lowercased
    for lookup; a name whose bytes fell off the byte-economics head simply
    contributes 0 to the byte share (a lower bound, consistent with
    head-only).
    """
    fp = fingerprinter if fingerprinter is not None else default_fingerprinter()
    fam = families if families is not None else default_families()

    folded: Dict[str, int] = {}
    for raw_name, count in nonstandard_counts.items():
        name = raw_name.lower()
        # x-crawler-* are Common-Crawl-injected, not part of the upstream
        # response. The map-time source dicts already exclude them; skipping
        # here too makes the renderers' "x-crawler-* excluded" claim
        # self-evident rather than dependent on that upstream invariant.
        if name.startswith("x-crawler-"):
            continue
        if classify_header(name) != PROPRIETARY:
            continue
        folded[name] = folded.get(name, 0) + int(count)

    bytes_by_name: Dict[str, int] = {}
    for raw_name, byte_total in field_bytes.items():
        lname = raw_name.lower()
        bytes_by_name[lname] = bytes_by_name.get(lname, 0) + int(byte_total)

    entries: List[HeaderEntry] = [
        HeaderEntry(
            name=name,
            count=count,
            byte_count=bytes_by_name.get(name, 0),
            vendor=fp.vendor_for_name(name),
            well_known=fam.is_well_known(name),
        )
        for name, count in folded.items()
    ]

    if not entries:
        return Census(0, 0, 0, 0, 0, truncated=truncated)

    by_vendor_groups: Dict[str, List[HeaderEntry]] = {}
    by_family_groups: Dict[str, List[HeaderEntry]] = {}
    by_prefix_groups: Dict[str, List[HeaderEntry]] = {}
    for entry in entries:
        by_vendor_groups.setdefault(entry.vendor or UNATTRIBUTED, []).append(entry)
        by_family_groups.setdefault(fam.classify(entry.name), []).append(entry)
        by_prefix_groups.setdefault(derive_prefix(entry.name), []).append(entry)

    def vendor_label(key: str) -> str:
        return "Unattributed" if key == UNATTRIBUTED else key

    well_known = sum(1 for e in entries if e.well_known)
    return Census(
        distinct_names=len(entries),
        total_count=sum(e.count for e in entries),
        total_bytes=sum(e.byte_count for e in entries),
        well_known_names=well_known,
        novel_names=len(entries) - well_known,
        by_vendor=_grouped(by_vendor_groups, vendor_label, None, member_limit, None),
        by_family=_grouped(
            by_family_groups, fam.label, fam.family_order, member_limit, None
        ),
        by_prefix=_grouped(
            by_prefix_groups, lambda k: f"{k}-*", None, member_limit, prefix_limit
        ),
        top_headers=sorted(entries, key=lambda e: (-e.count, e.name))[:top_headers],
        truncated=truncated,
    )
