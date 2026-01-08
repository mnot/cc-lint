import os
import logging
import zipfile
from typing import Set
from urllib.parse import urlparse

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
    except Exception as exc:  # pylint: disable=broad-except
        logging.getLogger(__name__).error("Error loading top sites: %s", exc)

    return sites


def is_in_top_sites(url_or_host: str, top_sites: Set[str]) -> bool:
    """
    Check if a URL's host (or the host string itself) is in the top sites list.
    Handles 'www.' variance.
    """
    try:
        if "://" in url_or_host:
            parsed = urlparse(url_or_host)
            host = parsed.hostname
        else:
            host = url_or_host

        if not host:
            return False

        host_stripped = host.lower()
        if host_stripped.startswith("www."):
            host_stripped = host_stripped[4:]

        return host_stripped in top_sites
    except Exception:  # pylint: disable=broad-except
        return False
