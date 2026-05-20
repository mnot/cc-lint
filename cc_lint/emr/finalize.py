"""Finalize an EMR result directory into stats.json + report.html.

The reducer in cc_lint.emr.job emits exactly one ("summary", <stats
dict>) record per reduce task using JSONProtocol, so each part-* line
is::

    "summary"\\t{"total_responses": ..., "notes": {...}, ...}

When the reduces > 1 the summary records are not yet combined, so this
finalizer merges them before writing stats.json and rendering the
HTML report.

Usage:
    python -m cc_lint.emr.finalize <results-dir> <output.html>

stats.json is written alongside the HTML report.
"""

import argparse
import glob
import json
import os
import sys
from typing import Any, Dict, Iterator, Tuple

from cc_lint.emr.job import merge_stats_dict
from cc_lint.report import generate_report


def _iter_summary_records(results_dir: str) -> Iterator[Tuple[str, Dict[str, Any]]]:
    for part_path in sorted(glob.glob(os.path.join(results_dir, "part-*"))):
        with open(part_path, "r", encoding="utf-8") as part_file:
            for line in part_file:
                if not line.strip():
                    continue
                try:
                    key_str, value_str = line.split("\t", 1)
                    key = json.loads(key_str)
                    value = json.loads(value_str)
                except (ValueError, json.JSONDecodeError) as exc:
                    print(
                        f"WARN: skipping unparseable line in {part_path}: {exc}",
                        file=sys.stderr,
                    )
                    continue
                yield key, value


def merge_results(results_dir: str) -> Dict[str, Any]:
    """Merge every "summary" record under ``results_dir`` into one dict."""
    merged: Dict[str, Any] = {}
    seen_keys: Dict[str, int] = {}
    for key, value in _iter_summary_records(results_dir):
        seen_keys[key] = seen_keys.get(key, 0) + 1
        if key != "summary":
            print(f"WARN: ignoring unexpected key {key!r}", file=sys.stderr)
            continue
        merge_stats_dict(merged, value)

    if not merged:
        raise SystemExit(
            f"No 'summary' records found under {results_dir}. "
            f"Keys seen: {sorted(seen_keys)}"
        )
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge EMR part-* outputs and render the HTML report."
    )
    parser.add_argument(
        "results_dir",
        help="Directory containing part-* files synced from the EMR output dir",
    )
    parser.add_argument(
        "output_html",
        help="Path to write the rendered HTML report",
    )
    parser.add_argument(
        "--stats-json",
        default=None,
        help="Path to write the merged stats.json (defaults to <results-dir>/stats.json)",
    )
    args = parser.parse_args()

    stats = merge_results(args.results_dir)

    stats_path = args.stats_json or os.path.join(args.results_dir, "stats.json")
    with open(stats_path, "w", encoding="utf-8") as stats_file:
        json.dump(stats, stats_file, indent=2)
    print(f"Wrote merged stats to {stats_path}")

    generate_report(stats_path, args.output_html)
    print(f"Wrote report to {args.output_html}")


if __name__ == "__main__":
    main()
