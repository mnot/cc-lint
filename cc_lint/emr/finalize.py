"""Finalize an EMR result directory into the rendered report.

The reducer in cc_lint.emr.job emits sharded JSONProtocol records:

    "globals"\\t{"total_responses": ..., "field_counts": {...}, ...}
    "note:CC_DUP"\\t{"count": ..., "samples": [...], "vars": {...}, ...}
    "note:BAD_SYNTAX"\\t{...}
    ...

This finalizer assembles them into the unified stats dict shape that
cc_lint.report expects:

    {
      "total_responses": ...,
      "field_counts": {...},
      "unprocessed_counts": {...},
      "notes": {"CC_DUP": {...}, "BAD_SYNTAX": {...}, ...}
    }

With REDUCES > 1 the same key may appear in multiple part-* files
(one per reducer); merge_globals and merge_note do the final fold.

Usage:
    python -m cc_lint.emr.finalize <results-dir> <output.html>

HTML is written to ``<output.html>`` and a Markdown sibling is written to
``<output>.md`` (cc_lint.report.default_markdown_path).
"""

import argparse
import datetime
import glob
import json
import os
import sys
from typing import Any, Dict, Iterator, Tuple

from cc_lint.cache_control import merge_cache_control
from cc_lint.cooccur import merge_cooccur
from cc_lint.emr.job import (
    CACHE_CONTROL_KEY,
    COOCCUR_KEY,
    CSP_SIZES_KEY,
    GLOBALS_KEY,
    NOTE_KEY_PREFIX,
    VALUE_HISTOGRAMS_KEY,
    VARY_KEY,
    merge_csp_sizes,
    merge_globals,
    merge_note,
    merge_value_histograms,
    trim_stats_dict,
)
from cc_lint.report import default_markdown_path, render_report
from cc_lint.vary import merge_vary


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
    merged: Dict[str, Any] = {"notes": {}, "csp_max_by_site": {}}
    note_counts: Dict[str, int] = {}
    saw_globals = False

    for key, value in _iter_records(results_dir):
        if key == GLOBALS_KEY:
            merge_globals(merged, value)
            saw_globals = True
        elif key.startswith(NOTE_KEY_PREFIX):
            note_id = key[len(NOTE_KEY_PREFIX) :]
            note_counts[note_id] = note_counts.get(note_id, 0) + 1
            target = merged["notes"].setdefault(
                note_id, {"count": 0, "samples": [], "vars": {}}
            )
            merge_note(target, value)
        elif key == CSP_SIZES_KEY:
            merge_csp_sizes(merged["csp_max_by_site"], value)
        elif key == VARY_KEY:
            merge_vary(merged.setdefault("vary", {}), value)
        elif key == CACHE_CONTROL_KEY:
            merge_cache_control(merged.setdefault("cache_control", {}), value)
        elif key == VALUE_HISTOGRAMS_KEY:
            merge_value_histograms(merged.setdefault("value_histograms", {}), value)
        elif key == COOCCUR_KEY:
            merge_cooccur(merged.setdefault("cooccur", {}), value)
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
    merged["finalized_at"] = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge EMR part-* outputs and render the HTML + Markdown report."
    )
    parser.add_argument(
        "results_dir",
        help="Directory containing part-* files synced from the EMR output dir",
    )
    parser.add_argument(
        "output_html",
        help="Path to write the rendered HTML report",
    )
    args = parser.parse_args()

    stats = merge_results(args.results_dir)
    render_report(stats, args.output_html)
    md_path = default_markdown_path(args.output_html)
    print(f"Wrote HTML report to {args.output_html}")
    print(f"Wrote Markdown report to {md_path}")


if __name__ == "__main__":
    main()
