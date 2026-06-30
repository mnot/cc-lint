"""Top-level orchestration for the cc-lint reports.

Both the local CLI and the EMR finalizer pass an in-memory stats dict
(produced by ``StatsCollector.to_dict()`` or by merging sharded part-*
records). Each call produces HTML at the configured output path plus a
Markdown sibling at the same path with the extension swapped to ``.md``.
"""

import os
from typing import Any, Dict, Optional

from cc_lint.fingerprint import default_fingerprinter
from cc_lint.header_census import build_census
from cc_lint.hll import hll_estimate
from cc_lint.report.markdown import render_markdown
from cc_lint.report.sections import (
    TOC_SCRIPT,
    build_toc,
    count_total_notes,
    render_asn_section,
    render_cache_control_section,
    render_category_overview,
    render_census_section,
    render_cooccur_section,
    render_csp_section,
    render_field_counts_section,
    render_header_bytes_section,
    render_header_stats,
    render_health_summary,
    render_infrastructure_section,
    render_missing_section,
    render_note_cooccur_section,
    render_notes_section,
    render_run_context,
    render_transition_section,
    render_value_histograms_section,
    render_vary_section,
)
from cc_lint.report.severity import (
    build_category_index,
    build_severity_index,
    category_display_order,
    classify_unseen,
    possible_note_ids,
)
from cc_lint.report.styles import STYLE


def _build_html(data: Dict[str, Any]) -> str:
    severity_index = build_severity_index()
    category_index = build_category_index()
    category_order = category_display_order()
    possible_ids = possible_note_ids(severity_index)

    total_responses = int(data.get("total_responses", 0))
    notes = data.get("notes") or {}
    field_counts: Dict[str, int] = data.get("field_counts", {}) or {}
    census = build_census(
        data.get("unprocessed_counts") or {},
        data.get("field_bytes") or {},
        bool(data.get("truncated_unprocessed_counts")),
    )

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

    csp_sizes = data.get("csp_max_by_site") or {}
    severity_counts = data.get("severity_counts") or {}
    vary = data.get("vary") or {}
    cache_control = data.get("cache_control") or {}
    cooccur = data.get("cooccur") or {}
    note_cooccur = data.get("note_cooccur") or {}
    transition = data.get("transition") or {}

    layer_counts: Dict[str, int] = data.get("layer_counts") or {}
    field_counts_by_layer: Dict[str, Dict[str, int]] = (
        data.get("field_counts_by_layer") or {}
    )
    asn_counts: Dict[str, int] = data.get("asn_counts") or {}
    try:
        fingerprinter = default_fingerprinter()
        layer_roles = dict(fingerprinter.roles)
        asn_to_layer = dict(fingerprinter.asn_to_layer)
    except (OSError, ValueError):
        # Role labels are cosmetic; a missing/broken table must not block the
        # report. Fall back to blank roles.
        layer_roles = {}
        asn_to_layer = {}

    body_parts = [
        render_header_stats(
            total_responses, total_notes, len(seen_note_ids), distinct_sites_estimate
        ),
        render_run_context(run_context, finalized_at),
        render_health_summary(severity_counts),
        render_category_overview(notes, category_index, category_order),
        render_notes_section(
            notes,
            field_counts,
            severity_index,
            category_index,
            category_order,
            total_notes,
            distinct_sites_estimate,
            layer_counts,
        ),
        render_field_counts_section(
            field_counts, total_responses, bool(data.get("truncated_field_counts"))
        ),
        render_header_bytes_section(
            data.get("field_bytes") or {},
            data.get("header_block_hist") or {},
            int(data.get("total_header_bytes", 0)),
            total_responses,
            bool(data.get("truncated_field_bytes")),
        ),
        render_infrastructure_section(
            layer_counts,
            field_counts_by_layer,
            field_counts,
            total_responses,
            layer_roles,
            bool(data.get("truncated_field_counts_by_layer")),
        ),
        render_asn_section(
            asn_counts,
            total_responses,
            asn_to_layer,
            bool(data.get("truncated_asn_counts")),
        ),
        render_csp_section(csp_sizes),
        render_value_histograms_section(data.get("value_histograms") or {}),
        render_vary_section(vary),
        render_cache_control_section(cache_control),
        render_cooccur_section(cooccur, layer_roles),
        render_note_cooccur_section(note_cooccur, seen_note_ids),
        render_transition_section(transition),
        render_census_section(census),
        render_missing_section(
            reachable_unseen, request_only, body_only, total_responses
        ),
    ]

    # Hero + run-context lead; the table of contents sits between them and the
    # content sections (it derives its entries by scanning those sections, so
    # build it from the content slice only).
    lead_html = "".join(body_parts[:2])
    content_html = "".join(body_parts[2:])
    toc_html = build_toc(content_html)

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
        f"{lead_html}{toc_html}{content_html}\n"
        f"{TOC_SCRIPT}\n"
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
