"""WAT input helpers for cc-lint EMR jobs.

cc-lint reads WAT (metadata) records rather than full WARC response bodies,
since httplint only inspects HTTP response headers. WAT paths are derived
from WARC paths by rewriting the path segment and extension.
"""

# pylint: disable=no-name-in-module

import os
import tempfile
import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

import boto3
from botocore.config import Config
from warcio.archiveiterator import ArchiveIterator

from cc_lint.cc_paths import warc_path_to_wat

# Re-export for backward compatibility with existing test imports.
__all__ = ["create_s3_client", "iter_wat_records", "warc_path_to_wat"]

COMMON_CRAWL_BUCKET = "commoncrawl"
ProgressCallback = Callable[[str], None]


def create_s3_client() -> Any:
    return boto3.client(
        "s3",
        region_name="us-east-1",
        config=Config(
            read_timeout=120,
            connect_timeout=30,
            # Adaptive retry mode plus a higher attempt budget rides out
            # transient S3 throttles on the common-crawl prefix without
            # adding much latency on the happy path. Standard mode's 3
            # attempts proved too tight in earlier passes.
            retries={"max_attempts": 5, "mode": "adaptive"},
        ),
    )


@contextmanager
def _local_or_downloaded(
    raw_path: str,
    s3_client: Any,
    progress: Optional[ProgressCallback] = None,
) -> Iterator[str]:
    if os.path.exists(raw_path):
        yield raw_path
        return

    key = warc_path_to_wat(raw_path)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".warc.wat.gz") as tmp:
        temp_path = tmp.name

    done = threading.Event()

    def heartbeat() -> None:
        while not done.wait(timeout=30):
            if progress:
                progress(raw_path)

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
        s3_client.download_file(
            COMMON_CRAWL_BUCKET,
            key,
            temp_path,
            ExtraArgs={"RequestPayer": "requester"},
        )
        yield temp_path
    finally:
        done.set()
        heartbeat_thread.join(timeout=1)
        if os.path.exists(temp_path):
            os.remove(temp_path)


def iter_wat_records(
    raw_path: str,
    s3_client: Any,
    progress: Optional[ProgressCallback] = None,
) -> Iterator[Any]:
    """Yield WAT metadata records for the given Common Crawl WARC path.

    ``raw_path`` may be a local file or an S3 key. S3 keys are translated
    via ``warc_path_to_wat`` before download.
    """
    with _local_or_downloaded(raw_path, s3_client, progress) as wat_path:
        with open(wat_path, "rb") as wat_file:
            for record in ArchiveIterator(wat_file):
                if record.rec_type == "metadata":
                    yield record
