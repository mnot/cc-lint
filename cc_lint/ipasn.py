"""Offline IP-to-ASN resolution from a CAIDA RouteViews pfx2as snapshot (issue #4).

cc-lint fingerprints infrastructure partly from the crawl-time
``WARC-IP-Address`` the crawler recorded. Mapping that IP to an Autonomous
System Number lets the fingerprint table match a CDN/host by network even when
the response strips identifying headers.

The lookup is fully offline and deterministic: a pinned pfx2as snapshot (one
chosen to match the crawl epoch, since ASN ownership drifts) is downloaded by a
Makefile target and shipped to the EMR mappers like the Tranco list. No live
DNS, so the report stays a pure function of the input crawl.

File format (tab-separated, CAIDA pfx2as):

    1.0.0.0\t24\t13335
    2001:200::\t32\t2500

The AS field may carry a MOAS/AS-set form (``13335_14618`` or ``{1,2}``); we
keep the first ASN listed, which is best-effort but adequate for fingerprinting.

Lookup uses longest-prefix match implemented as a dict per prefix length: for
each candidate length (longest first) the masked address is checked against the
length's network->ASN map. This keeps memory to plain ints and avoids a C
extension (which would be awkward to ship to EMR).
"""

from __future__ import annotations

import ipaddress
from typing import Dict, List, Optional


class IpAsnTable:
    """Longest-prefix IP->ASN lookup over a pfx2as snapshot."""

    def __init__(self) -> None:
        # version -> prefix_length -> network_int -> asn
        self._maps: Dict[int, Dict[int, Dict[int, int]]] = {4: {}, 6: {}}
        # version -> prefix lengths present, longest first
        self._lengths: Dict[int, List[int]] = {4: [], 6: []}

    def add(self, network_int: int, prefix_len: int, version: int, asn: int) -> None:
        per_len = self._maps[version].setdefault(prefix_len, {})
        # First writer wins on an exact duplicate prefix; pfx2as rarely repeats
        # an exact (prefix, len), and either origin is acceptable best-effort.
        per_len.setdefault(network_int, asn)

    def finalize(self) -> None:
        for version in (4, 6):
            self._lengths[version] = sorted(self._maps[version].keys(), reverse=True)

    def lookup(self, ip: str) -> Optional[int]:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None
        version = addr.version
        ip_int = int(addr)
        bits = 32 if version == 4 else 128
        per_len_maps = self._maps[version]
        for prefix_len in self._lengths[version]:
            mask = (~0 << (bits - prefix_len)) & ((1 << bits) - 1)
            network = ip_int & mask
            asn = per_len_maps[prefix_len].get(network)
            if asn is not None:
                return asn
        return None


def _parse_asn(field: str) -> Optional[int]:
    """Extract the first ASN from a pfx2as AS field (handles MOAS/AS-set forms)."""
    digits: List[str] = []
    for char in field:
        if char.isdigit():
            digits.append(char)
        elif digits:
            break
    if not digits:
        return None
    return int("".join(digits))


def load_ipasn(path: str) -> IpAsnTable:
    """Load a pfx2as TSV (IPv4 and/or IPv6 prefixes) into an IpAsnTable."""
    table = IpAsnTable()
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            prefix, prefix_len_str, as_field = parts[0], parts[1], parts[2]
            try:
                prefix_len = int(prefix_len_str)
                network = ipaddress.ip_network(f"{prefix}/{prefix_len}", strict=False)
            except ValueError:
                continue
            asn = _parse_asn(as_field)
            if asn is None:
                continue
            table.add(
                int(network.network_address),
                prefix_len,
                network.version,
                asn,
            )
    table.finalize()
    return table
