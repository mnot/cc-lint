import logging
import os
import zipfile
from typing import Optional, Set
from urllib.parse import urlparse

import idna
import requests

TRANCO_URL = "https://tranco-list.eu/top-1m.csv.zip"
TRANCO_FILENAME = "top-1m.csv"


def get_top_sites_path(cache_dir: str) -> str:
    """
    Ensure Tranco list is downloaded and return path to the CSV.
    """
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir, exist_ok=True)

    csv_path = os.path.join(cache_dir, TRANCO_FILENAME)
    zip_path = os.path.join(cache_dir, "tranco.zip")

    if os.path.exists(csv_path):
        return csv_path

    logging.getLogger(__name__).info("Downloading Tranco list from %s", TRANCO_URL)

    try:
        response = requests.get(TRANCO_URL, stream=True, timeout=120)
        response.raise_for_status()
        with open(zip_path, "wb") as zip_file:
            for chunk in response.iter_content(chunk_size=8192):
                zip_file.write(chunk)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extract(TRANCO_FILENAME, path=cache_dir)

    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)

    return csv_path


def load_top_sites(path: str, limit: int) -> Set[str]:
    """
    Load the top N sites from the CSV file.
    CSV format: rank,domain
    """
    sites = set()
    try:
        with open(path, "r", encoding="utf-8") as csv_file:
            for i, line in enumerate(csv_file):
                if i >= limit:
                    break
                parts = line.strip().split(",")
                if len(parts) >= 2:
                    sites.add(parts[1])
    except (OSError, UnicodeDecodeError) as exc:
        logging.getLogger(__name__).error("Error loading top sites: %s", exc)

    return sites


def normalize_site(url_or_host: Optional[str]) -> Optional[str]:
    """Normalize a URL or hostname to a comparable site key.

    Lowercases, strips a leading "www." label, punycode-encodes
    internationalized (non-ASCII) hosts, and returns just the host portion.
    Returns None for empty/None input or any parse failure.

    The result is the same shape as entries in the Tranco list (host form,
    IDN hosts in punycode), so it can be compared against top-sites sets
    directly.
    """
    if not url_or_host:
        return None
    try:
        if "://" in url_or_host:
            host = urlparse(url_or_host).hostname
        else:
            host = url_or_host
        if not host:
            return None
        host = host.lower()
        if host.startswith("www."):
            host = host[4:]
        if not host.isascii():
            # Tranco lists IDN hosts in punycode (xn--…). Encode so they match
            # the top-sites set and don't split into a second HLL site key from
            # their Unicode form. Use the idna package (IDNA2008/UTS-46), not
            # the stdlib "idna" codec (IDNA2003): registries — and therefore
            # Tranco's xn-- labels — follow IDNA2008, and the two disagree on a
            # residual set (e.g. ß — IDNA2003 maps faß.de to fass.de, IDNA2008
            # keeps it as xn--fa-hia.de). idna.IDNAError subclasses UnicodeError,
            # so a malformed label is caught here and we keep the Unicode host
            # rather than letting the outer handler drop the site.
            try:
                host = idna.encode(host, uts46=True).decode("ascii")
            except UnicodeError:
                pass
        return host
    except (ValueError, AttributeError, TypeError):
        # urlparse raises ValueError on malformed IPv6; AttributeError /
        # TypeError catch unexpected non-string inputs.
        return None


def is_in_top_sites(url_or_host: Optional[str], top_sites: Set[str]) -> bool:
    """Check if a URL's host (or the host string itself) is in ``top_sites``."""
    site = normalize_site(url_or_host)
    return site is not None and site in top_sites
