import gzip
import json
import logging
from typing import Optional, Set

import click

from .crawling import get_warc_stream, iter_warc_records
from .linting import lint_record
from .report import generate_report
from .stats import StatsCollector
from .top_sites import get_top_sites_path, is_in_top_sites, load_top_sites

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@click.group()
def cli() -> None:
    pass


@cli.command(name="lint")
@click.option(
    "--paths-file",
    required=True,
    help="Path to a local file containing WARC paths (one per line, optionally .gz)",
)
@click.option("--limit", default=1, help="Number of WARC files to process")
@click.option("--output", default="stats.json", help="Output JSON file")
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
def lint_cc(  # pylint: disable=too-many-positional-arguments
    paths_file: str,
    limit: int,
    output: str,
    record_limit: int,
    cache_dir: Optional[str],
    top_sites: Optional[int],
) -> None:
    """Run httplint on Common Crawl WARC files."""
    stats = StatsCollector()

    tranco_path = None
    if top_sites:
        ts_cache = cache_dir if cache_dir else "tranco_cache"
        tranco_path = get_top_sites_path(ts_cache)

    logger.info("Reading paths from %s", paths_file)
    open_func = gzip.open if paths_file.endswith(".gz") else open

    warc_paths = []
    try:
        with open_func(paths_file, "rt") as path_file:
            for line in path_file:
                warc_paths.append(line.strip())
    except FileNotFoundError:
        logger.error("Paths file %s not found.", paths_file)
        return

    logger.info("Found %s WARC paths. Processing first %s.", len(warc_paths), limit)

    top_sites_set = None
    if top_sites and tranco_path:
        top_sites_set = load_top_sites(tranco_path, top_sites)

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

    with open(output, "w", encoding="utf-8") as out_file:
        json.dump(stats.to_dict(), out_file, indent=2)

    logger.info("Done. Stats written to %s", output)


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
                if top_sites_set is not None and not is_in_top_sites(
                    linter.base_uri, top_sites_set
                ):
                    continue
                stats.process_linter(linter)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Error linting record in %s: %s", warc_path, exc)
                continue
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Error streaming %s: %s", warc_path, exc)


@cli.command(name="report")
@click.option("--input", "input_file", required=True, help="Input stats.json file")
@click.option("--output", required=True, help="Output HTML file")
def report_cc(input_file: str, output: str) -> None:
    """Generate an HTML report from statistics."""
    try:
        generate_report(input_file, output)
        logger.info("Report generated at %s", output)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Error generating report: %s", exc)


if __name__ == "__main__":
    cli()
