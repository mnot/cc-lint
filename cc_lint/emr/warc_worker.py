"""Crash-isolated WAT processing for EMR mappers.

Lint and stats accumulation for one WAT file runs in a child process so a
native crash inside warcio, decompression, or httplint kills only the
child. The parent mapper logs the offending path and continues with the
next WARC, instead of losing the entire mapper chunk.
"""

# pylint: disable=broad-exception-caught

from __future__ import annotations

import pickle
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Any, Optional, Set, cast

from cc_lint.emr.warc_source import create_s3_client, iter_wat_records
from cc_lint.ipasn import IpAsnTable
from cc_lint.linting import lint_record
from cc_lint.stats import StatsCollector
from cc_lint.top_sites import is_in_top_sites


@dataclass
class WarcWorkerResult:
    stats: StatsCollector
    records_seen: int
    total_ms: int
    process_ms: int
    iterator_ms: int


def process_warc_to_file(  # pylint: disable=too-many-positional-arguments
    raw_path: str,
    result_path: str,
    record_limit: int,
    top_sites: Optional[Set[str]],
    sample_sites: Optional[Set[str]] = None,
    ipasn: Optional[IpAsnTable] = None,
) -> None:
    """Process one WAT file and write the result to ``result_path``.

    ``top_sites`` gates which responses contribute to stats at all (the
    response-set filter). ``sample_sites`` is a separate, typically smaller
    Tranco ceiling that gates which responses contribute to the sample
    lists -- so the published shame-list is restricted to popular sites
    that can absorb the attention, while small-site responses still count
    toward the aggregate metrics.

    Intended to run in a child process. Any exception escapes after the
    traceback is logged so the parent observes a non-zero exit code.
    """
    worker_start = time.perf_counter()
    stats = StatsCollector(sample_sites=sample_sites, ipasn=ipasn)
    s3_client: Any = create_s3_client()
    records_seen = 0
    processing_time = 0.0

    try:
        for record in iter_wat_records(raw_path, s3_client, _download_heartbeat):
            if 0 < record_limit <= records_seen:
                break
            records_seen += 1

            process_start = time.perf_counter()
            try:
                linter = lint_record(record)
                if linter is not None:
                    if top_sites is None or is_in_top_sites(linter.base_uri, top_sites):
                        stats.process_linter(linter)
            except Exception as exc:
                sys.stderr.write(f"WARN: lint failed for record in {raw_path}: {exc}\n")
                sys.stderr.flush()
            processing_time += time.perf_counter() - process_start

        total_ms = int((time.perf_counter() - worker_start) * 1000)
        process_ms = int(processing_time * 1000)
        result = WarcWorkerResult(
            stats=stats,
            records_seen=records_seen,
            total_ms=total_ms,
            process_ms=process_ms,
            iterator_ms=max(0, total_ms - process_ms),
        )
        with open(result_path, "wb") as result_file:
            pickle.dump(result, result_file)
    except Exception as exc:
        sys.stderr.write(f"ERROR: WARC worker failed for {raw_path}: {exc}\n")
        sys.stderr.write(traceback.format_exc())
        sys.stderr.flush()
        raise


def load_warc_worker_result(path: str) -> WarcWorkerResult:
    with open(path, "rb") as result_file:
        return cast(WarcWorkerResult, pickle.load(result_file))


def _download_heartbeat(raw_path: str) -> None:
    sys.stderr.write(f"INFO: child still downloading WARC: {raw_path}\n")
    sys.stderr.flush()
