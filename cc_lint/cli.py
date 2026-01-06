import click
import json
import gzip
import logging
from .crawling import get_warc_stream, iter_warc_records
from .linting import lint_record
from .stats import StatsCollector

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import click
import json
import gzip
import logging
from .crawling import get_warc_stream, iter_warc_records
from .linting import lint_record
from .stats import StatsCollector
from .report import generate_report

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@click.group()
def cli():
    pass

@cli.command(name='lint')
@click.option('--paths-file', required=True, help='Path to a local or S3 file containing WARC paths (e.g. paths.gz)')
@click.option('--limit', default=1, help='Number of WARC files to process')
@click.option('--output', default='stats.json', help='Output JSON file')
@click.option('--record-limit', default=0, help='Max records to process per WARC file (0 for all)')
@click.option('--cache-dir', default=None, help='Directory to cache downloaded WARC files')
def lint_cc(paths_file, limit, output, record_limit, cache_dir):
    """
    Run httplint on Common Crawl WARC files.
    """
    stats = StatsCollector()
    
    # Read paths file
    # This assumes paths_file is local for now, or we can add logic to fetch from S3
    logger.info(f"Reading paths from {paths_file}")
    
    # Simple handling: invalidation or different handling if it's S3 or local
    # For now, let's assume it's a local file (downloaded previously or just passed)
    # If it ends in .gz, open with gzip
    open_func = gzip.open if paths_file.endswith('.gz') else open
    
    warc_paths = []
    try:
        with open_func(paths_file, 'rt') as f:
            for line in f:
                warc_paths.append(line.strip())
    except FileNotFoundError:
        logger.error(f"Paths file {paths_file} not found.")
        return

    logger.info(f"Found {len(warc_paths)} WARC paths. Processing first {limit}.")

    count = 0
    for warc_path in warc_paths:
        if count >= limit:
            break
        
        logger.info(f"Processing {warc_path}")
        try:
            stream = get_warc_stream(warc_path, cache_dir=cache_dir)
            record_count = 0
            for record in iter_warc_records(stream):
                if record_limit > 0 and record_count >= record_limit:
                    break
                record_count += 1
                try:
                    linter = lint_record(record)
                    if linter:
                        stats.process_linter(linter)
                except Exception as e:
                    logger.warning(f"Error linting record in {warc_path}: {e}")
        except Exception as e:
            logger.error(f"Error streaming {warc_path}: {e}")
        
        count += 1

    # Output stats
    with open(output, 'w') as f:
        json.dump(stats.to_dict(), f, indent=2)
    
    logger.info(f"Done. Stats written to {output}")

@cli.command(name='report')
@click.option('--input', required=True, help='Input stats.json file')
@click.option('--output', required=True, help='Output HTML file')
def report_cc(input, output):
    """
    Generate an HTML report from statistics.
    """
    try:
        generate_report(input, output)
        logger.info(f"Report generated at {output}")
    except Exception as e:
        logger.error(f"Error generating report: {e}")

if __name__ == '__main__':
    cli()
