"""Top-level orchestration for the cc-lint reports.

Two callers feed this module: the CLI passes a stats.json file path; the
EMR finalizer passes the in-memory dict it just merged from part-* shards.
Both paths produce HTML at the configured output path plus a Markdown
sibling at the same path with the extension swapped to ``.md``.
"""

import json
import os
from typing import Any, Dict, Optional

from cc_lint.hll import hll_estimate
from cc_lint.report.markdown import render_markdown
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
            field_counts, total_responses, bool(data.get("truncated_field_counts"))
        ),
        render_unprocessed_section(
            unprocessed_counts, bool(data.get("truncated_unprocessed_counts"))
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


def default_markdown_path(html_path: str) -> str:
    """Derive the sibling Markdown path next to ``html_path``.

    ``report.html`` -> ``report.md``; if ``html_path`` has no extension
    we append ``.md`` rather than overwriting the original filename.
    """
    root, ext = os.path.splitext(html_path)
    if not ext:
        return html_path + ".md"
    return root + ".md"


def render_report(
    data: Dict[str, Any],
    html_path: str,
    markdown_path: Optional[str] = None,
) -> None:
    """Render an in-memory stats dict to HTML + Markdown.

    HTML is written to ``html_path``; Markdown is written to
    ``markdown_path`` (defaulting to ``default_markdown_path(html_path)``).
    """
    html_text = _build_html(data)
    md_text = render_markdown(data)
    with open(html_path, "w", encoding="utf-8") as out_handle:
        out_handle.write(html_text)
    md_path = markdown_path or default_markdown_path(html_path)
    with open(md_path, "w", encoding="utf-8") as out_handle:
        out_handle.write(md_text)


def generate_report(stats_file: str, output_file: str) -> None:
    """Render ``stats_file`` (JSON) into HTML + Markdown reports.

    Kept as a thin file-loading wrapper around :func:`render_report` so
    the CLI's ``cc-lint report --input stats.json --output report.html``
    path keeps working.
    """
    with open(stats_file, "r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)
    render_report(data, output_file)
