"""HTML section renderers for the cc-lint report.

Each ``render_*`` returns a string fragment; the orchestrator in ``render.py``
concatenates them inside the page chrome. All user-supplied values pass
through :func:`html.escape`; the only literal HTML in these strings is the
fixed page structure.
"""

import html
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from cc_lint.hll import hll_estimate
from cc_lint.report.styles import METHODOLOGY_NOTE, TRUNCATED_NOTE


REDBOT_BASE = "https://redbot.org/check?uri="


# ---- formatting helpers ----------------------------------------------------


def _redbot_link(url: str) -> str:
    return REDBOT_BASE + urllib.parse.quote(url, safe="")


def _format_count(count: int) -> str:
    return f"{count:,}"


def _format_vars(note_vars: Dict[str, Any]) -> str:
    if not note_vars:
        return ""
    items = [
        f"{html.escape(str(k))}={html.escape(str(v))}" for k, v in note_vars.items()
    ]
    return f"<span class=\"vars\">{', '.join(items)}</span>"


def _sample_li(sample: Dict[str, Any]) -> str:
    url = sample.get("url", "")
    if not url:
        return ""
    return (
        f'<li><a href="{html.escape(_redbot_link(url))}" target="_blank" rel="noopener">'
        f"{html.escape(url)}</a>{_format_vars(sample.get('vars', {}))}</li>"
    )


def count_total_notes(notes: Dict[str, Any]) -> int:
    total = 0
    for data in notes.values():
        if isinstance(data, dict):
            total += int(data.get("count", 0))
        else:
            total += int(data)
    return total


# ---- top of report ---------------------------------------------------------


def _format_pill(label: str, value: str, modifier: str = "") -> str:
    cls = "run-pill" if not modifier else f"run-pill {modifier}"
    return (
        f'<div class="{cls}">'
        f'<span class="pill-label">{html.escape(label)}</span>'
        f'<span class="pill-value">{html.escape(value)}</span>'
        "</div>"
    )


def render_run_context(
    run_context: Dict[str, Any], finalized_at: Optional[str]
) -> str:
    pills: List[str] = []
    crawl_id = run_context.get("crawl_id") or ""
    if crawl_id:
        pills.append(_format_pill("Crawl", crawl_id))
    version = run_context.get("cc_lint_version") or ""
    if version:
        pills.append(_format_pill("cc-lint", f"v{version}"))
    top_n = int(run_context.get("top_sites") or 0)
    if top_n:
        pills.append(
            _format_pill(
                "Top-sites filter",
                f"Tranco top {_format_count(top_n)}",
                "pill-warning",
            )
        )
    else:
        pills.append(_format_pill("Top-sites filter", "none (full sample)"))
    sample_n = int(run_context.get("sample_top_sites") or 0)
    if sample_n:
        pills.append(
            _format_pill(
                "Sample ceiling",
                f"Tranco top {_format_count(sample_n)}",
            )
        )
    record_limit = int(run_context.get("record_limit") or 0)
    if record_limit:
        pills.append(
            _format_pill(
                "Records / WARC",
                _format_count(record_limit),
                "pill-warning",
            )
        )
    warc_limit = int(run_context.get("warc_limit") or 0)
    if warc_limit:
        pills.append(
            _format_pill(
                "WARCs / mapper",
                _format_count(warc_limit),
                "pill-warning",
            )
        )
    if finalized_at:
        pills.append(_format_pill("Finalized", finalized_at))

    if not pills:
        return ""
    return (
        '<section class="run-context">'
        f'<div class="run-pills">{"".join(pills)}</div>'
        f'<p class="methodology">{html.escape(METHODOLOGY_NOTE)}</p>'
        "</section>"
    )


def render_header_stats(
    total_responses: int,
    total_notes: int,
    seen_count: int,
    distinct_sites_estimate: Optional[int],
) -> str:
    sites_card = ""
    if distinct_sites_estimate is not None:
        sites_card = (
            f'<div><dt>Distinct sites analyzed</dt><dd>~{_format_count(distinct_sites_estimate)}'
            ' <small>HLL estimate</small></dd></div>'
        )
    return (
        '<header class="hero">'
        '<h1>Common Crawl Response Lint</h1>'
        '<dl class="stat-grid">'
        f'<div><dt>Responses analyzed</dt><dd>{_format_count(total_responses)}</dd></div>'
        f'{sites_card}'
        f'<div><dt>Note occurrences</dt><dd>{_format_count(total_notes)}'
        ' <small>across all responses</small></dd></div>'
        f'<div><dt>Distinct note types seen</dt><dd>{_format_count(seen_count)}</dd></div>'
        "</dl>"
        "</header>"
    )


# ---- per-note rendering ----------------------------------------------------


def _render_field_error_block(
    counts: Dict[str, int], var_samples: Dict[str, List[Dict[str, Any]]]
) -> str:
    """Render the special STRUCTURED_FIELD_PARSE_ERROR.field_error grouping."""
    grouped: Dict[str, List[Tuple[str, int]]] = {}
    for full_key, count in counts.items():
        field, _, error = full_key.partition(": ")
        grouped.setdefault(field, []).append((error or full_key, count))

    field_totals = {f: sum(c for _, c in errs) for f, errs in grouped.items()}
    sorted_fields = sorted(field_totals.items(), key=lambda item: item[1], reverse=True)

    rows: List[str] = []
    for field, total in sorted_fields[:50]:
        errors = sorted(grouped[field], key=lambda item: item[1], reverse=True)
        error_items: List[str] = []
        for err, count in errors:
            full_key = f"{field}: {err}" if err != field else err
            samples_html = ""
            samples = var_samples.get(full_key) or []
            if samples:
                items = "".join(_sample_li(s) for s in samples)
                samples_html = f"<ul class=\"samples\">{items}</ul>"
            error_items.append(
                f"<li><span class=\"err\">{html.escape(err)}</span> "
                f"<span class=\"muted\">({_format_count(count)})</span>{samples_html}</li>"
            )
        rows.append(
            "<tr>"
            f"<th scope=\"row\">{html.escape(field)}"
            f"<br><span class=\"muted\">{_format_count(total)}</span></th>"
            f"<td><ul class=\"errors\">{''.join(error_items)}</ul></td>"
            "</tr>"
        )
    overflow = ""
    if len(sorted_fields) > 50:
        overflow = (
            f"<tr><td colspan=\"2\" class=\"muted\">"
            f"… {len(sorted_fields) - 50} more fields not shown …</td></tr>"
        )
    return (
        '<table class="var-table field-error">'
        "<thead><tr><th>Field</th><th>Errors</th></tr></thead>"
        f"<tbody>{''.join(rows)}{overflow}</tbody>"
        "</table>"
    )


def _render_var_block(
    var_name: str,
    counts: Dict[str, int],
    var_samples: Dict[str, List[Dict[str, Any]]],
    field_counts: Dict[str, int],
) -> str:
    is_field_name = var_name == "field_name"
    sorted_vals = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)

    head_cells = ["Value", "Count"]
    if is_field_name:
        head_cells += ["Total occurrences", "% of occurrences"]
    header = "".join(f"<th>{c}</th>" for c in head_cells)

    rows: List[str] = []
    for val, val_count in sorted_vals[:25]:
        cells = [html.escape(val), _format_count(val_count)]
        if is_field_name:
            total = field_counts.get(val.lower(), 0)
            if total:
                pct = val_count / total * 100
                cells += [_format_count(total), f"{pct:.2f}%"]
            else:
                cells += [_format_count(total), "—"]
        cells_html = "".join(f"<td>{c}</td>" for c in cells)
        rows.append(f"<tr>{cells_html}</tr>")
        samples = var_samples.get(val) or []
        if samples:
            colspan = len(head_cells)
            sample_lis = "".join(_sample_li(s) for s in samples)
            rows.append(
                f"<tr class=\"samples-row\"><td colspan=\"{colspan}\">"
                f"<details><summary>Samples ({len(samples)})</summary>"
                f"<ul class=\"samples\">{sample_lis}</ul></details></td></tr>"
            )

    if len(sorted_vals) > 25:
        rows.append(
            f"<tr><td colspan=\"{len(head_cells)}\" class=\"muted\">"
            f"… {len(sorted_vals) - 25} more values not shown …</td></tr>"
        )

    return (
        f'<h4>{html.escape(var_name)}</h4>'
        f'<table class="var-table">'
        f"<thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _render_variable_stats(
    note_data: Dict[str, Any], field_counts: Dict[str, int]
) -> str:
    var_stats: Dict[str, Dict[str, int]] = note_data.get("vars", {}) or {}
    if not var_stats:
        return ""
    all_var_samples: Dict[str, Dict[str, List[Dict[str, Any]]]] = note_data.get(
        "var_samples", {}
    ) or {}
    truncated_vars = note_data.get("truncated_vars") or {}
    blocks: List[str] = []
    for var_name, counts in var_stats.items():
        var_samples = all_var_samples.get(var_name, {})
        if truncated_vars.get(var_name):
            blocks.append(
                f'<p class="muted truncated" data-var="{html.escape(var_name)}">'
                f"<strong>{html.escape(var_name)}</strong>: long tail elided "
                "during shuffle; only the retained head is shown.</p>"
            )
        if var_name == "field_error":
            blocks.append("<h4>field_error</h4>")
            blocks.append(_render_field_error_block(counts, var_samples))
        else:
            blocks.append(
                _render_var_block(var_name, counts, var_samples, field_counts)
            )
    return f'<div class="var-stats">{"".join(blocks)}</div>'


def _render_note_card(
    note_id: str,
    note_data: Dict[str, Any],
    severity: str,
    field_counts: Dict[str, int],
) -> str:
    count = note_data.get("count", 0) if isinstance(note_data, dict) else int(note_data)
    samples = note_data.get("samples", []) if isinstance(note_data, dict) else []

    sample_html = ""
    if samples:
        sample_html = (
            "<ul class=\"samples\">"
            f"{''.join(_sample_li(s) for s in samples)}"
            "</ul>"
        )

    var_html = ""
    if isinstance(note_data, dict):
        var_html = _render_variable_stats(note_data, field_counts)

    open_attr = " open" if count > 0 else ""
    count_title = (
        "Occurrences across responses. One response may contribute multiple "
        "occurrences for notes that fire per header or per directive."
    )
    sites_pill = ""
    sites_hll = note_data.get("sites_hll") if isinstance(note_data, dict) else None
    if sites_hll:
        site_est = hll_estimate(sites_hll)
        if site_est > 0:
            sites_pill = (
                f'<span class="note-sites" title="HyperLogLog estimate of '
                "distinct sites where this note fired.\">"
                f"~{_format_count(site_est)} sites</span>"
            )
    return (
        f'<details class="note severity-{severity}"{open_attr}>'
        f'<summary>'
        f'<span class="badge badge-{severity}">{severity.upper()}</span>'
        f'<span class="note-id">{html.escape(note_id)}</span>'
        f'{sites_pill}'
        f'<span class="note-count" title="{html.escape(count_title)}">'
        f'{_format_count(count)}</span>'
        "</summary>"
        f'<div class="note-body">{sample_html}{var_html}</div>'
        "</details>"
    )


def render_notes_section(
    notes: Dict[str, Any],
    field_counts: Dict[str, int],
    severity_index: Dict[str, str],
) -> str:
    sorted_notes = sorted(
        notes.items(),
        key=lambda item: (
            item[1].get("count", 0) if isinstance(item[1], dict) else int(item[1])
        ),
        reverse=True,
    )
    if not sorted_notes:
        return ""
    cards = [
        _render_note_card(
            note_id, data, severity_index.get(note_id, "warn"), field_counts
        )
        for note_id, data in sorted_notes
    ]
    return (
        '<section id="notes">'
        '<h2>Notes</h2>'
        f'<div class="note-list">{"".join(cards)}</div>'
        "</section>"
    )


# ---- field histograms ------------------------------------------------------


def render_field_counts_section(
    field_counts: Dict[str, int], total_responses: int, truncated: bool
) -> str:
    if not field_counts:
        return ""
    top = sorted(field_counts.items(), key=lambda kv: kv[1], reverse=True)[:50]
    rows: List[str] = []
    for name, count in top:
        pct = (count / total_responses * 100) if total_responses else 0
        rows.append(
            f"<tr><td>{html.escape(name)}</td>"
            f"<td>{_format_count(count)}</td>"
            f"<td>{pct:.1f}%</td></tr>"
        )
    return (
        '<section id="headers">'
        '<h2>Top Response Headers</h2>'
        '<p class="muted">Most common response header names, with the share of '
        "responses that included at least one instance.</p>"
        f"{TRUNCATED_NOTE if truncated else ''}"
        '<table class="data-table">'
        "<thead><tr><th>Header</th><th>Count</th><th>% of responses</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def render_unprocessed_section(
    unprocessed_counts: Dict[str, int], truncated: bool
) -> str:
    if not unprocessed_counts:
        return ""
    top = sorted(unprocessed_counts.items(), key=lambda kv: kv[1], reverse=True)[:50]
    rows = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{_format_count(count)}</td></tr>"
        for name, count in top
    )
    return (
        '<section id="unprocessed">'
        '<h2>Top Unsupported Headers</h2>'
        '<p class="muted">Header names httplint did not recognise, ranked by occurrence.</p>'
        f"{TRUNCATED_NOTE if truncated else ''}"
        '<table class="data-table">'
        "<thead><tr><th>Header</th><th>Count</th></tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
        "</section>"
    )


# ---- unseen notes ----------------------------------------------------------


def _render_unseen_subblock(title: str, body: str, notes: List[str]) -> str:
    if not notes:
        return ""
    items = "".join(f"<li>{html.escape(n)}</li>" for n in notes)
    return (
        '<details>'
        f'<summary><h3>{html.escape(title)} ({len(notes)})</h3></summary>'
        f'<p class="muted">{body}</p>'
        f'<ul class="missing-list">{items}</ul>'
        "</details>"
    )


def render_missing_section(
    reachable_unseen: List[str],
    request_only: List[str],
    body_only: List[str],
) -> str:
    if not (reachable_unseen or request_only or body_only):
        return ""
    blocks = [
        _render_unseen_subblock(
            "Reachable but not triggered",
            "httplint defines these warn/bad notes and cc-lint's response-header "
            "pipeline can reach them; none of them fired on any analysed response.",
            reachable_unseen,
        ),
        _render_unseen_subblock(
            "Body-only (not reachable in WAT mode)",
            "These notes only fire when the response body is fed to httplint. "
            "cc-lint reads WAT metadata records (response headers only), so they "
            "are unreachable without a future full-WARC mode.",
            body_only,
        ),
        _render_unseen_subblock(
            "Request-only (not reachable for response linting)",
            "These notes only fire from httplint's request-side code paths. "
            "cc-lint runs HttpResponseLinter against WAT response metadata, so "
            "they cannot fire on this pipeline.",
            request_only,
        ),
    ]
    return (
        '<section id="missing">'
        '<h2>Unseen note types</h2>'
        f'{"".join(blocks)}'
        "</section>"
    )
