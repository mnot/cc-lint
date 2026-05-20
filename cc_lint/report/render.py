"""Top-level orchestration for the cc-lint HTML report."""

import json
from typing import Any, Dict

from cc_lint.hll import hll_estimate
from cc_lint.report.sections import (
    count_total_notes,
    render_field_counts_section,
    render_header_stats,
    render_missing_section,
    render_notes_section,
    render_run_context,
    render_unprocessed_section,
)
from cc_lint.report.severity import (
    build_severity_index,
    classify_unseen,
    possible_note_ids,
)
from cc_lint.report.styles import STYLE


def _build_html(data: Dict[str, Any]) -> str:
    severity_index = build_severity_index()
    possible_ids = possible_note_ids(severity_index)

    total_responses = int(data.get("total_responses", 0))
    notes = data.get("notes", data.get("note_counts", {})) or {}
    field_counts: Dict[str, int] = data.get("field_counts", {}) or {}
    unprocessed_counts: Dict[str, int] = data.get("unprocessed_counts", {}) or {}

    total_notes = count_total_notes(notes)
    seen_note_ids = set(notes.keys())
    reachable_unseen, request_only, body_only = classify_unseen(
        possible_ids, seen_note_ids
    )

    sites_hll = data.get("sites_hll")
    distinct_sites_estimate = (
        hll_estimate(sites_hll) if isinstance(sites_hll, list) and sites_hll else None
    )
    run_context = data.get("run_context") or {}
    finalized_at = data.get("finalized_at")

    body_parts = [
        render_header_stats(
            total_responses, total_notes, len(seen_note_ids), distinct_sites_estimate
        ),
        render_run_context(run_context, finalized_at),
        render_notes_section(notes, field_counts, severity_index),
        render_field_counts_section(
            field_counts, total_responses, bool(data.get("_truncated_field_counts"))
        ),
        render_unprocessed_section(
            unprocessed_counts, bool(data.get("_truncated_unprocessed_counts"))
        ),
        render_missing_section(reachable_unseen, request_only, body_only),
    ]

    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "  <title>CC Lint Report</title>\n"
        f"  <style>{STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{''.join(body_parts)}\n"
        "</body>\n"
        "</html>\n"
    )


def generate_report(stats_file: str, output_file: str) -> None:
    """Render ``stats_file`` (JSON) into a single-file HTML report."""
    with open(stats_file, "r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)

    html_text = _build_html(data)

    with open(output_file, "w", encoding="utf-8") as out_handle:
        out_handle.write(html_text)
