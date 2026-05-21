"""HTTP-only WAT streaming for the local cc-lint CLI.

The EMR path uses cc_lint.emr.warc_source (requester-pays S3 with
heartbeat); this module is the lightweight equivalent used by
``cc-lint lint`` for developer-machine runs against
data.commoncrawl.org.
"""

import logging
import os
import shutil
from typing import Any, Iterator, Optional

import requests
from warcio.archiveiterator import ArchiveIterator

from cc_lint.cc_paths import warc_path_to_wat


def _open_http_stream(path: str) -> Any:
    url = f"https://data.commoncrawl.org/{path}"
    response = requests.get(url, stream=True, timeout=120)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} fetching {url}")
    return response.raw


def get_warc_stream(path: str, cache_dir: Optional[str] = None) -> Any:
    """Return a binary stream for the WAT sibling of ``path``.

    When ``cache_dir`` is given, the WAT file is downloaded to
    ``<cache_dir>/<path>`` once (preserving the CC directory layout)
    and subsequent calls reuse the on-disk copy.
    """
    path = warc_path_to_wat(path)

    if not cache_dir:
        return _open_http_stream(path)

    local_path = os.path.join(cache_dir, path)
    if os.path.exists(local_path):
        logging.getLogger(__name__).info("Cache hit: %s", local_path)
        return open(local_path, "rb")  # pylint: disable=consider-using-with

    logging.getLogger(__name__).info(
        "Cache miss: downloading %s to %s", path, local_path
    )
    os.makedirs(os.path.dirname(local_path), exist_ok=True)

    temp_path = local_path + ".tmp"
    try:
        url = f"https://data.commoncrawl.org/{path}"
        response = requests.get(url, stream=True, timeout=120)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code} fetching {url}")
        with open(temp_path, "wb") as file_handle:
            shutil.copyfileobj(response.raw, file_handle)
        os.rename(temp_path, local_path)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise

    return open(local_path, "rb")  # pylint: disable=consider-using-with


def iter_warc_records(stream: Any) -> Iterator[Any]:
    """Yield records from a (W)ARC stream."""
    yield from ArchiveIterator(stream)
