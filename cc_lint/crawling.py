import os
import shutil
import logging
from typing import Iterator, Any
import requests
import boto3
from warcio.archiveiterator import ArchiveIterator  # type: ignore


def get_warc_stream_http(path: str) -> Any:
    """
    Yields a stream of the object body from data.commoncrawl.org via HTTP.
    """
    url = f"https://data.commoncrawl.org/{path}"
    # Note: If caching is used, we might want to capture to a file first,
    # but that is handled in get_warc_stream wrapper.
    # Directly returning stream here.
    response = requests.get(url, stream=True, timeout=120)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} fetching {url}")
    return response.raw


def get_warc_stream_s3(path: str) -> Any:
    """
    Yields a stream from S3. Requires credentials or correct config for Requester Pays if needed.
    """
    s3 = boto3.client("s3")
    obj = s3.get_object(Bucket="commoncrawl", Key=path, RequestPayer="requester")
    return obj["Body"]


def get_warc_stream(path: str, use_s3: bool = False, cache_dir: str | None = None) -> Any:
    if not cache_dir:
        # Stream directly if no cache
        if use_s3:
            return get_warc_stream_s3(path)
        return get_warc_stream_http(path)

    # Caching enabled
    # Preserve directory structure to avoid collisions and keep it organized
    local_path = os.path.join(cache_dir, path)

    if os.path.exists(local_path):
        # Cache hit
        logging.getLogger(__name__).info("Cache hit: %s", local_path)
        return open(local_path, "rb")

    # Cache miss
    logging.getLogger(__name__).info("Cache miss: Downloading %s to %s", path, local_path)

    # Ensure directory exists
    os.makedirs(os.path.dirname(local_path), exist_ok=True)

    # Download logic
    # We download to a temp file then move to ensure atomicity (mostly)
    temp_path = local_path + ".tmp"
    try:
        if use_s3:
            s3 = boto3.client("s3")
            s3.download_file(
                "commoncrawl", path, temp_path, ExtraArgs={"RequestPayer": "requester"}
            )
        else:
            url = f"https://data.commoncrawl.org/{path}"
            res = requests.get(url, stream=True, timeout=120)
            if res.status_code != 200:
                raise RuntimeError(f"HTTP {res.status_code} fetching {url}")
            with open(temp_path, "wb") as file_handle:
                shutil.copyfileobj(res.raw, file_handle)

        os.rename(temp_path, local_path)
    except Exception as exc:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise exc

    return open(local_path, "rb")


def iter_warc_records(stream: Any) -> Iterator[Any]:
    """
    Iterates over WARC records from a stream.
    """
    yield from ArchiveIterator(stream)
