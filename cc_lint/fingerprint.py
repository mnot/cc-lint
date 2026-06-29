"""Best-effort infrastructure fingerprinting (issue #4).

Derives a set of infrastructure "layers" (CDN / server / framework /
platform / mesh) for a response from its headers, using a data-driven table
(``fingerprints.toml``). Header signals are matched case-insensitively; a
response may match several layers at once (a stack), e.g. cloudflare + nextjs.

ASN-based matching (mapping the crawl-time WARC-IP-Address to an ASN) is
threaded through :meth:`Fingerprinter.match` via the optional ``asn``
argument; the IP->ASN lookup itself is wired in a later step.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.9/3.10
    import tomli as tomllib


_VALID_ROLES = {"cdn", "server", "framework", "platform", "mesh"}


@dataclass(frozen=True)
class _Signal:
    """One match rule against a single (lowercased) header name.

    ``contains`` and ``regex`` are mutually exclusive; with neither set the
    rule is a bare presence test (the header appearing at all is a match).
    """

    header: str
    contains: Optional[str]
    regex: Optional["re.Pattern[str]"]

    def matches(self, values: Sequence[str]) -> bool:
        if self.contains is None and self.regex is None:
            return True  # presence: the named header exists
        for value in values:
            if self.contains is not None and self.contains in value.lower():
                return True
            if self.regex is not None and self.regex.search(value):
                return True
        return False


@dataclass(frozen=True)
class _Layer:
    id: str
    role: str
    signals: Tuple[_Signal, ...]
    asns: frozenset[int]


class Fingerprinter:
    """Matches a response's headers (and optional ASN) to a set of layer ids."""

    def __init__(self, layers: List[_Layer]) -> None:
        self._layers = layers
        self.roles: Dict[str, str] = {layer.id: layer.role for layer in layers}
        # ASN -> layer id, for annotating the top-ASN histogram with the
        # vendor it maps to (first layer wins on a shared ASN).
        self.asn_to_layer: Dict[int, str] = {}
        for layer in layers:
            for asn in layer.asns:
                self.asn_to_layer.setdefault(asn, layer.id)

    @property
    def layer_ids(self) -> List[str]:
        return [layer.id for layer in self._layers]

    def match(
        self,
        headers: Mapping[str, Sequence[str]],
        asn: Optional[int] = None,
    ) -> Set[str]:
        """Return the set of layer ids matching ``headers`` / ``asn``.

        ``headers`` maps lowercased header names to the list of values seen
        for that name on the response (a repeated header yields several).
        """
        matched: Set[str] = set()
        for layer in self._layers:
            if asn is not None and asn in layer.asns:
                matched.add(layer.id)
                continue
            for signal in layer.signals:
                values = headers.get(signal.header)
                if values is not None and signal.matches(values):
                    matched.add(layer.id)
                    break
        return matched


def _parse_signal(raw: Dict[str, Any], layer_id: str) -> _Signal:
    header = raw.get("header")
    if not isinstance(header, str) or not header:
        raise ValueError(f"fingerprint layer {layer_id!r}: signal missing 'header'")
    contains = raw.get("contains")
    regex_src = raw.get("regex")
    if contains is not None and not isinstance(contains, str):
        raise ValueError(f"fingerprint layer {layer_id!r}: 'contains' must be a string")
    if regex_src is not None and not isinstance(regex_src, str):
        raise ValueError(f"fingerprint layer {layer_id!r}: 'regex' must be a string")
    if contains is not None and regex_src is not None:
        raise ValueError(
            f"fingerprint layer {layer_id!r}: signal sets both 'contains' and 'regex'"
        )
    compiled = re.compile(regex_src, re.IGNORECASE) if regex_src is not None else None
    return _Signal(
        header=header.lower(),
        contains=contains.lower() if contains is not None else None,
        regex=compiled,
    )


def _parse_layer(raw: Dict[str, Any]) -> _Layer:
    layer_id = raw.get("id")
    if not isinstance(layer_id, str) or not layer_id:
        raise ValueError("fingerprint layer missing 'id'")
    role = raw.get("role")
    if not isinstance(role, str) or role not in _VALID_ROLES:
        raise ValueError(
            f"fingerprint layer {layer_id!r}: invalid role {role!r} "
            f"(expected one of {sorted(_VALID_ROLES)})"
        )
    raw_signals = raw.get("signals", [])
    if not isinstance(raw_signals, list):
        raise ValueError(f"fingerprint layer {layer_id!r}: 'signals' must be an array")
    signals = tuple(_parse_signal(sig, layer_id) for sig in raw_signals)
    raw_asns = raw.get("asns", [])
    if not isinstance(raw_asns, list):
        raise ValueError(f"fingerprint layer {layer_id!r}: 'asns' must be an array")
    try:
        asns = frozenset(int(asn) for asn in raw_asns)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"fingerprint layer {layer_id!r}: 'asns' must be integers"
        ) from exc
    if not signals and not asns:
        raise ValueError(
            f"fingerprint layer {layer_id!r}: needs at least one signal or asn"
        )
    return _Layer(id=layer_id, role=role, signals=signals, asns=asns)


def load_fingerprinter(path: Optional[str] = None) -> Fingerprinter:
    """Load and validate the fingerprint table.

    With ``path`` unset, reads the table shipped inside the package; pass a
    path to override it (mirrors how the EMR job ships the Tranco list).
    Raises ``ValueError`` on a malformed table so a bad edit fails fast.
    """
    if path is not None:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    else:
        text = (files("cc_lint") / "fingerprints.toml").read_text(encoding="utf-8")
        data = tomllib.loads(text)
    raw_layers = data.get("layer", [])
    if not isinstance(raw_layers, list) or not raw_layers:
        raise ValueError("fingerprint table has no [[layer]] entries")
    layers = [_parse_layer(raw) for raw in raw_layers]
    ids = [layer.id for layer in layers]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate fingerprint layer ids: {dupes}")
    return Fingerprinter(layers)


# Bucket key for responses that matched no known layer. Lets per-layer rates
# share a real denominator and makes fingerprint coverage reportable.
UNMATCHED = "__unmatched__"


@lru_cache(maxsize=1)
def default_fingerprinter() -> Fingerprinter:
    """The packaged fingerprint table, loaded once per process.

    Falls back to a ``fingerprints.toml`` in the working directory if the
    packaged resource can't be read -- on EMR the table is also shipped via
    ``--files`` (which lands it in the mapper's cwd), so this keeps the job
    working even if mrjob's bundling omits the package's non-.py data.
    """
    try:
        return load_fingerprinter()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return load_fingerprinter("fingerprints.toml")
