"""Finalize an EMR result directory into stats.json + report.html.

The reducer in cc_lint.emr.job emits sharded JSONProtocol records:

    "globals"\\t{"total_responses": ..., "field_counts": {...}, ...}
    "note:CC_DUP"\\t{"count": ..., "samples": [...], "vars": {...}, ...}
    "note:BAD_SYNTAX"\\t{...}
    ...

This finalizer assembles them into the unified stats.json shape that
cc_lint.report expects:

    {
      "total_responses": ...,
      "field_counts": {...},
      "unprocessed_counts": {...},
      "notes": {"CC_DUP": {...}, "BAD_SYNTAX": {...}, ...}
    }

With REDUCES > 1 the same key may appear in multiple part-* files
(one per reducer); _merge_globals and _merge_note do the final fold.

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

from cc_lint.emr.job import (
    GLOBALS_KEY,
    NOTE_KEY_PREFIX,
    _merge_globals,
    _merge_note,
    trim_stats_dict,
)
from cc_lint.report import generate_report


def _iter_records(results_dir: str) -> Iterator[Tuple[str, Dict[str, Any]]]:
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
    """Merge sharded reducer output into a single stats dict."""
    merged: Dict[str, Any] = {"notes": {}}
    note_counts: Dict[str, int] = {}
    saw_globals = False

    for key, value in _iter_records(results_dir):
        if key == GLOBALS_KEY:
            _merge_globals(merged, value)
            saw_globals = True
        elif key.startswith(NOTE_KEY_PREFIX):
            note_id = key[len(NOTE_KEY_PREFIX) :]
            note_counts[note_id] = note_counts.get(note_id, 0) + 1
            target = merged["notes"].setdefault(
                note_id, {"count": 0, "samples": [], "vars": {}}
            )
            _merge_note(target, value)
        else:
            print(f"WARN: ignoring unexpected key {key!r}", file=sys.stderr)

    if not saw_globals and not merged["notes"]:
        raise SystemExit(
            f"No sharded records found under {results_dir}. "
            f"Expected 'globals' and 'note:*' keys."
        )

    # Re-trim after the final union so the union-across-reducers can't sneak
    # past the per-mapper / per-reducer caps.
    trim_stats_dict(merged)
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
