import argparse
import gzip
import os
import re
import statistics
import subprocess
from pathlib import Path
from typing import Iterable, List, NamedTuple

_FINISHED_RE = re.compile(
    r"INFO: finished WARC (?P<warc_num>\d+): (?P<path>.+?) \| "
    r"records=(?P<records>\d+) total_ms=(?P<total_ms>\d+) "
    r"process_ms=(?P<process_ms>\d+) "
    r"iterator_download_ms=(?P<iterator_ms>\d+)"
)
_FAILED_RE = re.compile(
    r"ERROR: failed WARC (?P<warc_num>\d+): (?P<path>.+?) \| "
    r"exit_code=(?P<exit_code>[^ ]+)(?: signal=(?P<signal>\d+))?"
)
_INIT_RE = re.compile(r"mapper_init finished successfully in (?P<ms>\d+)ms")
_JITTER_RE = re.compile(r"jitter sleep (?P<seconds>[0-9.]+)s")


class WarcTiming(NamedTuple):
    path: str
    records: int
    total_ms: int
    process_ms: int
    iterator_ms: int


class WarcFailure(NamedTuple):
    path: str
    exit_code: str
    signal: str
    container: str


class MapperTiming(NamedTuple):
    init_ms: int
    jitter_ms: int
    warc_total_ms: int
    process_ms: int
    iterator_ms: int
    records: int
    warcs: int
    container: str

    @property
    def elapsed_ms(self) -> int:
        return self.init_ms + self.jitter_ms + self.warc_total_ms


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize cc-lint EMR mapper timing logs."
    )
    parser.add_argument("--cluster-id", help="Completed EMR cluster id, e.g. j-...")
    parser.add_argument(
        "--log-dir",
        default=os.environ.get("EMR_LOG_DIR", "/tmp/cc-lint-emr-logs"),
        help="Local directory for downloaded logs or previously downloaded logs",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS region used when --cluster-id is supplied",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Number of slow WARCs/chunks to show",
    )
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    if args.cluster_id:
        log_dir = _download_cluster_logs(args.cluster_id, log_dir, args.region)

    warcs, failures, mappers = summarize_logs(log_dir)
    _print_summary(warcs, failures, mappers, args.top)


def summarize_logs(
    log_dir: Path,
) -> tuple[List[WarcTiming], List[WarcFailure], List[MapperTiming]]:
    stderr_paths = sorted(log_dir.rglob("stderr.gz"))
    warcs: List[WarcTiming] = []
    failures: List[WarcFailure] = []
    mappers: List[MapperTiming] = []

    for stderr_path in stderr_paths:
        mapper_warcs: List[WarcTiming] = []
        init_ms = 0
        jitter_ms = 0
        for line in _read_gzip_lines(stderr_path):
            init_match = _INIT_RE.search(line)
            if init_match:
                init_ms = int(init_match.group("ms"))

            jitter_match = _JITTER_RE.search(line)
            if jitter_match:
                jitter_ms = int(float(jitter_match.group("seconds")) * 1000)

            finished_match = _FINISHED_RE.search(line)
            if finished_match:
                timing = WarcTiming(
                    path=finished_match.group("path"),
                    records=int(finished_match.group("records")),
                    total_ms=int(finished_match.group("total_ms")),
                    process_ms=int(finished_match.group("process_ms")),
                    iterator_ms=int(finished_match.group("iterator_ms")),
                )
                mapper_warcs.append(timing)
                warcs.append(timing)

            failed_match = _FAILED_RE.search(line)
            if failed_match:
                failures.append(
                    WarcFailure(
                        path=failed_match.group("path"),
                        exit_code=failed_match.group("exit_code"),
                        signal=failed_match.group("signal") or "",
                        container=stderr_path.parent.name,
                    )
                )

        if mapper_warcs:
            mappers.append(
                MapperTiming(
                    init_ms=init_ms,
                    jitter_ms=jitter_ms,
                    warc_total_ms=sum(timing.total_ms for timing in mapper_warcs),
                    process_ms=sum(timing.process_ms for timing in mapper_warcs),
                    iterator_ms=sum(timing.iterator_ms for timing in mapper_warcs),
                    records=sum(timing.records for timing in mapper_warcs),
                    warcs=len(mapper_warcs),
                    container=stderr_path.parent.name,
                )
            )

    return warcs, failures, mappers


def _download_cluster_logs(cluster_id: str, base_log_dir: Path, region: str) -> Path:
    log_uri = _cluster_log_uri(cluster_id, region)
    if not log_uri:
        raise SystemExit(f"No LogUri found for cluster {cluster_id}")

    s3_uri = _to_s3_uri(log_uri).rstrip("/") + f"/{cluster_id}/containers/"
    local_dir = base_log_dir / cluster_id / "containers"
    local_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "aws",
            "s3",
            "cp",
            s3_uri,
            str(local_dir),
            "--recursive",
            "--exclude",
            "*",
            "--include",
            "*/stderr.gz",
        ],
        check=True,
    )
    return base_log_dir / cluster_id


def _cluster_log_uri(cluster_id: str, region: str) -> str:
    result = subprocess.run(
        [
            "aws",
            "emr",
            "describe-cluster",
            "--region",
            region,
            "--cluster-id",
            cluster_id,
            "--query",
            "Cluster.LogUri",
            "--output",
            "text",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _to_s3_uri(uri: str) -> str:
    if uri.startswith("s3n://"):
        return "s3://" + uri[len("s3n://") :]
    return uri


def _read_gzip_lines(path: Path) -> Iterable[str]:
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as gzip_file:
        yield from gzip_file


def _print_summary(
    warcs: List[WarcTiming],
    failures: List[WarcFailure],
    mappers: List[MapperTiming],
    top_count: int,
) -> None:
    if not warcs:
        raise SystemExit("No cc-lint timing lines found in stderr.gz logs.")

    print("# EMR Timing Summary")
    print()
    print(f"Mapper chunks: {len(mappers):,}")
    print(f"WARCs completed: {len(warcs):,}")
    print(f"WARCs failed: {len(failures):,}")
    print(f"Records seen: {sum(timing.records for timing in warcs):,}")
    print()

    if failures:
        print("## Failed WARCs")
        print()
        print("| Exit | Signal | Container | Path |")
        print("| --- | --- | --- | --- |")
        for failure in failures:
            print(
                "| "
                f"{failure.exit_code} | "
                f"{failure.signal or '-'} | "
                f"`{failure.container}` | "
                f"`{failure.path}` |"
            )
        print()

    _print_metric_table(
        "WARC timings",
        [
            ("total", [timing.total_ms for timing in warcs]),
            ("processing", [timing.process_ms for timing in warcs]),
            ("iterator/download", [timing.iterator_ms for timing in warcs]),
        ],
    )
    process_total = sum(timing.process_ms for timing in warcs)
    warc_total = sum(timing.total_ms for timing in warcs)
    print(f"Processing share: {_pct(process_total, warc_total):.1f}%")
    print()

    _print_metric_table(
        "Mapper chunk timings",
        [
            ("elapsed", [timing.elapsed_ms for timing in mappers]),
            ("jitter", [timing.jitter_ms for timing in mappers]),
            ("init", [timing.init_ms for timing in mappers]),
        ],
    )
    print()

    print(f"## Slowest {top_count} WARCs")
    print()
    print("| Total | Processing | Iterator/download | Records | Path |")
    print("| --- | --- | --- | --- | --- |")
    for timing in sorted(warcs, key=lambda row: row.total_ms, reverse=True)[:top_count]:
        print(
            "| "
            f"{_seconds(timing.total_ms)} | "
            f"{_seconds(timing.process_ms)} | "
            f"{_seconds(timing.iterator_ms)} | "
            f"{timing.records:,} | "
            f"`{timing.path}` |"
        )


def _print_metric_table(title: str, rows: List[tuple[str, List[int]]]) -> None:
    print(f"## {title}")
    print()
    print("| Metric | Sum | Mean | Median | P90 | Max |")
    print("| --- | --- | --- | --- | --- | --- |")
    for label, values in rows:
        print(
            "| "
            f"{label} | "
            f"{_seconds(sum(values))} | "
            f"{_seconds(statistics.mean(values))} | "
            f"{_seconds(statistics.median(values))} | "
            f"{_seconds(_percentile(values, 90))} | "
            f"{_seconds(max(values))} |"
        )
    print()


def _percentile(values: List[int], percentile: int) -> int:
    if not values:
        return 0
    index = max(0, min(len(values) - 1, int(len(values) * percentile / 100) - 1))
    return sorted(values)[index]


def _seconds(milliseconds: float) -> str:
    return f"{milliseconds / 1000:.1f}s"


def _pct(part: int, whole: int) -> float:
    return part / whole * 100 if whole else 0.0


if __name__ == "__main__":
    main()
