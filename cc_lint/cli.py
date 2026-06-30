import gzip
import logging
from typing import List, Optional, Set

import click

from .crawling import get_warc_stream, iter_warc_records
from .ipasn import load_ipasn
from .linting import lint_record
from .report import default_markdown_path, render_report
from .stats import StatsCollector
from .top_sites import get_top_sites_path, is_in_top_sites, load_top_sites

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@click.group()
def cli() -> None:
    pass


def _load_paths(paths_file: str) -> Optional[List[str]]:
    """Read WARC paths (plain or gzipped) into a list. Returns None on missing file."""
    open_func = gzip.open if paths_file.endswith(".gz") else open
    try:
        with open_func(paths_file, "rt") as path_file:
            return [line.strip() for line in path_file]
    except FileNotFoundError:
        logger.error("Paths file %s not found.", paths_file)
        return None


def _load_top_sites_set(
    top_sites: Optional[int], cache_dir: Optional[str]
) -> Optional[Set[str]]:
    """Load the Tranco top-N hostname set, downloading on first use."""
    if not top_sites:
        return None
    ts_cache = cache_dir if cache_dir else "tranco_cache"
    tranco_path = get_top_sites_path(ts_cache)
    return load_top_sites(tranco_path, top_sites)


def _run_lint_loop(  # pylint: disable=too-many-positional-arguments
    warc_paths: List[str],
    limit: int,
    record_limit: int,
    cache_dir: Optional[str],
    top_sites_set: Optional[Set[str]],
    stats: StatsCollector,
) -> None:
    """Drive the per-WARC linting loop, swallowing KeyboardInterrupt cleanly."""
    count = 0
    try:
        for warc_path in warc_paths:
            if count >= limit:
                break
            _process_single_warc(
                warc_path, record_limit, cache_dir, top_sites_set, stats
            )
            count += 1
    except KeyboardInterrupt:
        logger.warning("Interrupted by user. Saving partial results...")


@cli.command(name="lint")
@click.option(
    "--paths-file",
    required=True,
    help="Path to a local file containing WARC paths (one per line, optionally .gz)",
)
@click.option("--limit", default=1, help="Number of WARC files to process")
@click.option(
    "--output",
    default="report.html",
    help="Output HTML report path. A Markdown sibling is written alongside.",
)
@click.option(
    "--record-limit",
    default=0,
    help="Max records to process per WARC file (0 for all)",
)
@click.option(
    "--cache-dir", default=None, help="Directory to cache downloaded WARC files"
)
@click.option(
    "--top-sites", type=int, default=None, help="Filter records to top N websites"
)
@click.option(
    "--ipasn-path",
    default=None,
    help=(
        "Path to a CAIDA pfx2as IP->ASN table for ASN-based infrastructure "
        "fingerprinting. When unset, fingerprinting is header-only."
    ),
)
def lint_cc(  # pylint: disable=too-many-positional-arguments
    paths_file: str,
    limit: int,
    output: str,
    record_limit: int,
    cache_dir: Optional[str],
    top_sites: Optional[int],
    ipasn_path: Optional[str],
) -> None:
    """Run httplint on Common Crawl WAT files and render an HTML + Markdown report."""
    logger.info("Reading paths from %s", paths_file)
    warc_paths = _load_paths(paths_file)
    if warc_paths is None:
        return
    logger.info("Found %s WARC paths. Processing first %s.", len(warc_paths), limit)

    top_sites_set = _load_top_sites_set(top_sites, cache_dir)
    ipasn = load_ipasn(ipasn_path) if ipasn_path else None

    stats = StatsCollector(ipasn=ipasn)
    _run_lint_loop(warc_paths, limit, record_limit, cache_dir, top_sites_set, stats)

    render_report(stats.to_dict(), output)
    logger.info("Done. Wrote %s and %s", output, default_markdown_path(output))


def _process_single_warc(
    warc_path: str,
    record_limit: int,
    cache_dir: Optional[str],
    top_sites_set: Optional[Set[str]],
    stats: StatsCollector,
) -> None:
    """Process a single WARC file: stream, lint records, update stats."""
    logger.info("Processing %s", warc_path)
    try:
        stream = get_warc_stream(warc_path, cache_dir=cache_dir)
        record_count = 0
        for record in iter_warc_records(stream):
            if 0 < record_limit <= record_count:
                break
            record_count += 1
            try:
                linter = lint_record(record)
                if not linter:
                    continue
                base_uri = getattr(linter, "base_uri", None)
                if top_sites_set is not None and not is_in_top_sites(
                    base_uri, top_sites_set
                ):
                    continue
                stats.process_linter(linter)
            except Exception as exc:  # pylint: disable=broad-except
                # Broad on purpose: one bad record must not abort the WARC.
                logger.warning(
                    "Error linting record in %s (%s): %s",
                    warc_path,
                    type(exc).__name__,
                    exc,
                )
                continue
    except Exception as exc:  # pylint: disable=broad-except
        # Broad on purpose: one bad WARC must not abort the run.
        logger.error("Error streaming %s (%s): %s", warc_path, type(exc).__name__, exc)


if __name__ == "__main__":
    cli()
