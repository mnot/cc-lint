"""HTML section renderers for the cc-lint report.

Each ``render_*`` returns a string fragment; the orchestrator in ``render.py``
concatenates them inside the page chrome. All user-supplied values pass
through :func:`html.escape`; the only literal HTML in these strings is the
fixed page structure.
"""

# pylint: disable=too-many-lines
# A flat collection of independent section renderers; splitting it would only
# scatter closely-related report code across modules without reducing coupling.

import html
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from cc_lint.fingerprint import UNMATCHED
from cc_lint.histograms import LIFETIME_BUCKET_ORDER, bucket_order
from cc_lint.hll import hll_estimate
from cc_lint.report.severity import build_summary_index
from cc_lint.report.styles import METHODOLOGY_NOTE, TRUNCATED_NOTE
from cc_lint.vary import (
    ACCEPT_ENCODING,
    AE_ONLY_LABEL,
    HIGH_INTEREST_AXES,
    count_nonstandard,
    factor_out,
    is_nonstandard_token,
    is_registered_field,
    recipe_tokens,
)

REDBOT_BASE = "https://redbot.org/check?uri="

_NOTE_SUMMARIES = build_summary_index()

_VAR_LABELS = {
    "directive_conflicts": "Directive → conflicts",
    "field_name_key": "Field name → key",
    "freshness_left_bucket": "Freshness remaining",
    "duration_bucket": "Duration",
    "cookie_value_size_bucket": "Cookie value size",
    "field_size_bucket": "Field size",
    "field_error": "Field → parse error",
}


def _var_heading(var_name: str) -> str:
    return _VAR_LABELS.get(var_name, var_name)


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


def _sample_li_html(url: str, trailing_html: str) -> str:
    """Render a sample list item: a redbot link to ``url`` plus trailing HTML.

    The single place the sample ``<li>`` / redbot-link markup lives, so the
    note-level and per-field renderers can't drift on escaping, rel/target, or
    the redbot URL. ``trailing_html`` is already-escaped markup appended after
    the link (the vars span or the captured-value code element).
    """
    if not url:
        return ""
    return (
        f'<li><a href="{html.escape(_redbot_link(url))}" target="_blank" rel="noopener">'
        f"{html.escape(url)}</a>{trailing_html}</li>"
    )


def _sample_li(sample: Dict[str, Any]) -> str:
    return _sample_li_html(sample.get("url", ""), _format_vars(sample.get("vars", {})))


def _field_sample_li(sample: Dict[str, Any]) -> str:
    """Render a per-field sample, surfacing the captured malformed value.

    Unlike :func:`_sample_li`, this shows only the header value(s) seen on the
    wire (``field_values``) rather than the full note-vars dump, so the reader
    can characterise a field's malformed population at a glance.
    """
    raw_values = sample.get("vars", {}).get("field_values")
    value_html = ""
    if raw_values:
        value_html = f'<code class="field-val">{html.escape(str(raw_values))}</code>'
    return _sample_li_html(sample.get("url", ""), value_html)


def _field_samples_details(samples: List[Dict[str, Any]]) -> str:
    """Collapsible list of per-field sample URLs + captured values, or ""."""
    items = "".join(_field_sample_li(s) for s in samples)
    if not items:
        return ""
    return (
        '<details class="field-samples">'
        f"<summary>Samples ({_format_count(len(samples))})</summary>"
        f'<ul class="samples">{items}</ul>'
        "</details>"
    )


def count_total_notes(notes: Dict[str, Any]) -> int:
    return sum(int(data.get("count", 0)) for data in notes.values())


# ---- top of report ---------------------------------------------------------


def _format_pill(label: str, value: str, modifier: str = "") -> str:
    cls = "run-pill" if not modifier else f"run-pill {modifier}"
    return (
        f'<div class="{cls}">'
        f'<span class="pill-label">{html.escape(label)}</span>'
        f'<span class="pill-value">{html.escape(value)}</span>'
        "</div>"
    )


def render_run_context(run_context: Dict[str, Any], finalized_at: Optional[str]) -> str:
    pills: List[str] = []
    crawl_id = run_context.get("crawl_id") or ""
    if crawl_id:
        pills.append(_format_pill("Crawl", crawl_id))
    version = run_context.get("cc_lint_version") or ""
    if version:
        pills.append(_format_pill("cc-lint", f"v{version}"))
    httplint_version = run_context.get("httplint_version") or ""
    if httplint_version:
        pills.append(_format_pill("httplint", f"v{httplint_version}"))
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
            f"<div><dt>Distinct sites analyzed</dt><dd>~{_format_count(distinct_sites_estimate)}"
            " <small>HLL estimate</small></dd></div>"
        )
    return (
        '<header class="hero">'
        "<h1>Common Crawl Response Lint</h1>"
        '<dl class="stat-grid">'
        f"<div><dt>Responses analyzed</dt><dd>{_format_count(total_responses)}</dd></div>"
        f"{sites_card}"
        f"<div><dt>Note occurrences</dt><dd>{_format_count(total_notes)}"
        " <small>across all responses</small></dd></div>"
        f"<div><dt>Distinct note types seen</dt><dd>{_format_count(seen_count)}</dd></div>"
        "</dl>"
        "</header>"
    )


# ---- per-note rendering ----------------------------------------------------


def _render_field_error_block(counts: Dict[str, int]) -> str:
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
            error_items.append(
                f'<li><span class="err">{html.escape(err)}</span> '
                f'<span class="muted">({_format_count(count)})</span></li>'
            )
        rows.append(
            "<tr>"
            f'<th scope="row">{html.escape(field)}'
            f'<br><span class="muted">{_format_count(total)}</span></th>'
            f"<td><ul class=\"errors\">{''.join(error_items)}</ul></td>"
            "</tr>"
        )
    overflow = ""
    if len(sorted_fields) > 50:
        overflow = (
            f'<tr><td colspan="2" class="muted">'
            f"… {len(sorted_fields) - 50} more fields not shown …</td></tr>"
        )
    return (
        '<table class="var-table field-error">'
        "<thead><tr><th>Field</th><th>Errors</th></tr></thead>"
        f"<tbody>{''.join(rows)}{overflow}</tbody>"
        "</table>"
    )


def _format_byte_size(byte_size: int) -> str:
    if byte_size < 1024:
        return f"{byte_size} B"
    if byte_size < 1024 * 1024:
        return f"{byte_size / 1024:.1f} KB"
    return f"{byte_size / (1024 * 1024):.1f} MB"


def _render_var_block(
    var_name: str,
    counts: Dict[str, int],
    field_counts: Dict[str, int],
    largest_by_value: Optional[Dict[str, int]] = None,
    value_samples: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> str:
    value_samples = value_samples or {}
    is_field_name = var_name == "field_name"
    bucket_seq = bucket_order(var_name)
    if bucket_seq is not None:
        order_index = {label: idx for idx, label in enumerate(bucket_seq)}
        sorted_vals = sorted(
            counts.items(), key=lambda kv: order_index.get(kv[0], len(bucket_seq))
        )
    else:
        sorted_vals = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)

    head_cells: List[Tuple[str, str]] = [
        ("Value", "Value of the variable."),
        ("Note fires", "Times this note fired with this value."),
    ]
    if is_field_name:
        head_cells += [
            (
                "Header occurrences",
                "Total times this header appeared on any analysed response.",
            ),
            (
                "Fires per occurrence",
                "Note fires divided by header occurrences. List-valued or "
                "directive-valued headers (e.g. Clear-Site-Data, "
                "Cache-Control) can exceed 100% because httplint fires the "
                "note once per offending token, not once per header.",
            ),
        ]
    if largest_by_value:
        head_cells.append(
            ("Largest seen", "Largest value observed for this entry across the crawl.")
        )
    header = "".join(
        f'<th title="{html.escape(t)}">{html.escape(c)}</th>' for c, t in head_cells
    )

    rows: List[str] = []
    for val, val_count in sorted_vals[:25]:
        value_cell = html.escape(val)
        samples = value_samples.get(val)
        if samples:
            value_cell += _field_samples_details(samples)
        cells = [value_cell, _format_count(val_count)]
        if is_field_name:
            total = field_counts.get(val.lower(), 0)
            if total:
                pct = val_count / total * 100
                cells += [_format_count(total), f"{pct:.2f}%"]
            else:
                cells += [_format_count(total), "—"]
        if largest_by_value:
            largest = largest_by_value.get(val)
            cells.append(_format_byte_size(largest) if largest else "—")
        cells_html = "".join(f"<td>{c}</td>" for c in cells)
        rows.append(f"<tr>{cells_html}</tr>")

    if len(sorted_vals) > 25:
        rows.append(
            f'<tr><td colspan="{len(head_cells)}" class="muted">'
            f"… {len(sorted_vals) - 25} more values not shown …</td></tr>"
        )

    return (
        f"<h4>{html.escape(_var_heading(var_name))}</h4>"
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
    truncated_vars = note_data.get("truncated_vars") or {}
    numeric_maxes: Dict[str, Dict[str, int]] = note_data.get("numeric_maxes") or {}
    # Currently the only configured max is field_size, keyed by field_name.
    field_size_max = numeric_maxes.get("field_size") or {}
    var_samples: Dict[str, Dict[str, List[Dict[str, Any]]]] = (
        note_data.get("var_samples") or {}
    )
    field_samples = var_samples.get("field_name") or {}
    blocks: List[str] = []
    for var_name, counts in var_stats.items():
        if truncated_vars.get(var_name):
            blocks.append(
                f'<p class="muted truncated" data-var="{html.escape(var_name)}">'
                f"<strong>{html.escape(var_name)}</strong>: long tail elided "
                "during shuffle; only the retained head is shown.</p>"
            )
        if var_name == "field_error":
            blocks.append(f"<h4>{html.escape(_var_heading(var_name))}</h4>")
            blocks.append(_render_field_error_block(counts))
        else:
            largest_by_value = field_size_max if var_name == "field_name" else None
            value_samples = field_samples if var_name == "field_name" else None
            blocks.append(
                _render_var_block(
                    var_name, counts, field_counts, largest_by_value, value_samples
                )
            )
    return f'<div class="var-stats">{"".join(blocks)}</div>'


def _render_note_layers(by_layer: Dict[str, int], note_count: int) -> str:
    """Compact per-note infrastructure breakdown (issue #4).

    Shows the share of this note's occurrences seen on each fingerprint
    layer. Layers overlap (a response can be cloudflare + nginx + nextjs),
    so the shares need not sum to 100%.
    """
    if not by_layer:
        return ""
    ordered = sorted(by_layer.items(), key=lambda kv: kv[1], reverse=True)
    rows: List[str] = []
    for layer, fired in ordered[:10]:
        share = f"{fired / note_count * 100:.1f}%" if note_count else "—"
        rows.append(
            f"<tr><td>{html.escape(layer)}</td>"
            f"<td>{_format_count(fired)}</td><td>{share}</td></tr>"
        )
    if len(ordered) > 10:
        rows.append(
            f'<tr><td colspan="3" class="muted">… {len(ordered) - 10} more '
            "layers not shown …</td></tr>"
        )
    return (
        '<div class="note-layers"><h4 title="Layers overlap; a response can '
        'match several, so shares need not sum to 100%.">By infrastructure</h4>'
        '<table class="var-table">'
        "<thead><tr><th>Layer</th><th>Note fires</th>"
        "<th>Share of occurrences</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _render_note_card(
    note_id: str,
    note_data: Dict[str, Any],
    severity: str,
    field_counts: Dict[str, int],
    distinct_sites_estimate: Optional[int] = None,
) -> str:
    count = int(note_data.get("count", 0))
    samples = note_data.get("samples") or []

    summary_template = _NOTE_SUMMARIES.get(note_id, "")
    summary_html = ""
    if summary_template:
        summary_html = f'<p class="note-summary">{html.escape(summary_template)}</p>'

    sample_html = ""
    if samples:
        items = "".join(_sample_li(s) for s in samples)
        sample_html = (
            '<details class="note-samples">'
            f"<summary>Samples ({_format_count(len(samples))})</summary>"
            f'<ul class="samples">{items}</ul>'
            "</details>"
        )

    var_html = _render_variable_stats(note_data, field_counts)
    layers_html = _render_note_layers(note_data.get("by_layer") or {}, count)

    open_attr = " open" if count > 0 else ""
    count_title = (
        "Occurrences across responses. One response may contribute multiple "
        "occurrences for notes that fire per header or per directive."
    )
    sites_pill = ""
    sites_hll = note_data.get("sites_hll")
    if sites_hll:
        site_est = hll_estimate(sites_hll)
        if site_est > 0:
            pct = ""
            if distinct_sites_estimate and distinct_sites_estimate > 0:
                share = site_est / distinct_sites_estimate * 100
                pct = f" ({share:.1f}%)"
            sites_pill = (
                f'<span class="note-sites" title="HyperLogLog estimate of '
                "distinct sites where this note fired, as a share of "
                'all distinct sites analyzed.">'
                f"~{_format_count(site_est)} sites{pct}</span>"
            )
    return (
        f'<details class="note severity-{severity}"{open_attr}>'
        f"<summary>"
        f'<span class="badge badge-{severity}">{severity.upper()}</span>'
        f'<span class="note-id">{html.escape(note_id)}</span>'
        f"{sites_pill}"
        f'<span class="note-count" title="{html.escape(count_title)}">'
        f"{_format_count(count)}</span>"
        "</summary>"
        f'<div class="note-body">{summary_html}{sample_html}{var_html}'
        f"{layers_html}</div>"
        "</details>"
    )


_SEVERITY_ORDER = {"bad": 4, "warn": 3, "info": 2, "good": 1}


def _note_sort_key(item: Tuple[str, Dict[str, Any]]) -> Tuple[int, int, int, str]:
    """Sort within a category: severity desc, then site cardinality desc,
    then occurrence count desc, then id asc.
    Notes seen on more sites surface above notes with many occurrences from a
    handful of noisy sites.
    """
    note_id, data = item
    if not isinstance(data, dict):
        return (0, 0, 0, note_id)
    severity = data.get("_severity", "warn")
    count = int(data.get("count", 0))
    sites_hll = data.get("sites_hll")
    sites = hll_estimate(sites_hll) if isinstance(sites_hll, list) and sites_hll else 0
    return (-_SEVERITY_ORDER.get(severity, 0), -sites, -count, note_id)


def render_notes_section(  # pylint: disable=too-many-positional-arguments
    notes: Dict[str, Any],
    field_counts: Dict[str, int],
    severity_index: Dict[str, str],
    category_index: Dict[str, str],
    category_order: List[str],
    total_notes: int = 0,
    distinct_sites_estimate: Optional[int] = None,
) -> str:
    """Render notes grouped by httplint category.

    Notes whose class isn't in the category index (test shims or unknown
    classes) are bucketed under "UNCATEGORIZED" at the end.
    """
    if not notes:
        return ""

    # Group note_id -> data by category. Decorate each note with its
    # severity so the sort key can read it without crossing back to the
    # severity index.
    by_category: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for note_id, data in notes.items():
        category = category_index.get(note_id, "UNCATEGORIZED")
        severity = severity_index.get(note_id, "warn")
        if isinstance(data, dict):
            data["_severity"] = severity
        by_category.setdefault(category, []).append((note_id, data))

    sections: List[str] = []
    ordered_categories = [c for c in category_order if c in by_category]
    # Any categories not in the configured order (e.g. UNCATEGORIZED).
    for category in by_category:
        if category not in ordered_categories:
            ordered_categories.append(category)

    for category in ordered_categories:
        entries = sorted(by_category[category], key=_note_sort_key)
        total_occ = sum(
            int(d.get("count", 0)) if isinstance(d, dict) else 0 for _, d in entries
        )
        cards = [
            _render_note_card(
                note_id,
                data,
                severity_index.get(note_id, "warn"),
                field_counts,
                distinct_sites_estimate,
            )
            for note_id, data in entries
        ]
        cat_pct = ""
        if total_notes > 0:
            cat_pct = f" ({total_occ / total_notes * 100:.1f}% of occurrences)"
        sections.append(
            f'<section class="note-category" id="cat-{html.escape(category.lower())}">'
            f"<h3>{html.escape(_pretty_category(category))} "
            f'<span class="cat-totals">{_format_count(total_occ)} occurrences '
            f"across {_format_count(len(entries))} note types{cat_pct}</span></h3>"
            f'<div class="note-list">{"".join(cards)}</div>'
            "</section>"
        )

    return '<section id="notes">' "<h2>Notes</h2>" f'{"".join(sections)}' "</section>"


_CATEGORY_LABELS = {
    "GENERAL": "General",
    "CONNECTION": "Connection",
    "SECURITY": "Browser security",
    "CORS": "Cross-origin resource sharing",
    "COOKIES": "Cookies",
    "CONNEG": "Content negotiation",
    "CACHING": "Caching",
    "VALIDATION": "Validation",
    "RANGE": "Partial content",
    "UNCATEGORIZED": "Uncategorized",
}


def _pretty_category(category: str) -> str:
    return _CATEGORY_LABELS.get(category, category.title())


def render_health_summary(severity_counts: Dict[str, int]) -> str:
    """Render the per-response health rollup as a horizontal bar.

    severity_counts has buckets: bad / warn / info / good / clean. The
    counts are per-response, not per-site; together they sum to
    total_responses analysed.
    """
    if not severity_counts:
        return ""
    total = sum(int(v) for v in severity_counts.values())
    if total <= 0:
        return ""
    rows = []
    bar_segments = []
    for severity, label in (
        ("bad", "BAD"),
        ("warn", "WARN"),
        ("info", "INFO"),
        ("good", "GOOD"),
        ("clean", "Clean"),
    ):
        count = int(severity_counts.get(severity, 0))
        if count == 0:
            continue
        pct = count / total * 100
        rows.append(
            f"<tr>"
            f'<td><span class="badge badge-{severity}">{html.escape(label)}</span></td>'
            f"<td>{_format_count(count)}</td>"
            f"<td>{pct:.1f}%</td>"
            f"</tr>"
        )
        bar_segments.append(
            f'<span class="health-seg health-seg-{severity}" '
            f'style="width:{pct:.3f}%" title="{html.escape(label)}: '
            f'{pct:.1f}%"></span>'
        )
    return (
        '<section id="health">'
        "<h2>Response health</h2>"
        '<p class="muted">Each response is bucketed by the most severe '
        "httplint finding it produced. <em>Clean</em> means httplint found "
        "nothing worth reporting on that response. Per-response, not "
        "per-site &mdash; popular sites contribute more responses.</p>"
        f'<div class="health-bar">{"".join(bar_segments)}</div>'
        '<table class="data-table">'
        "<thead><tr><th>Severity</th><th>Responses</th><th>%</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def render_category_overview(
    notes: Dict[str, Any], category_index: Dict[str, str], category_order: List[str]
) -> str:
    """Compact table mapping each category to its total occurrences."""
    if not notes:
        return ""
    by_category: Dict[str, Tuple[int, int]] = {}  # name -> (occurrences, note_count)
    for note_id, data in notes.items():
        category = category_index.get(note_id, "UNCATEGORIZED")
        count = int(data.get("count", 0)) if isinstance(data, dict) else 0
        prev_occ, prev_types = by_category.get(category, (0, 0))
        by_category[category] = (prev_occ + count, prev_types + 1)

    if not by_category:
        return ""
    # Render in the configured order, then append any unseen categories.
    seen_in_order = [c for c in category_order if c in by_category]
    for category in by_category:
        if category not in seen_in_order:
            seen_in_order.append(category)

    total_occ = sum(occ for occ, _ in by_category.values())
    rows: List[str] = []
    for category in seen_in_order:
        occurrences, note_types = by_category[category]
        anchor = f"cat-{html.escape(category.lower())}"
        share = f"{occurrences / total_occ * 100:.1f}%" if total_occ > 0 else "—"
        rows.append(
            f"<tr>"
            f'<td><a href="#{anchor}">{html.escape(_pretty_category(category))}</a></td>'
            f"<td>{_format_count(occurrences)}</td>"
            f"<td>{share}</td>"
            f"<td>{_format_count(note_types)}</td>"
            f"</tr>"
        )
    return (
        '<section id="categories">'
        "<h2>Findings by category</h2>"
        '<table class="data-table">'
        "<thead><tr><th>Category</th><th>Occurrences</th><th>%</th>"
        "<th>Note types fired</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


# ---- field histograms ------------------------------------------------------


def render_field_counts_section(
    field_counts: Dict[str, int], total_responses: int, truncated: bool
) -> str:
    if not field_counts:
        return ""
    filtered = {
        name: count
        for name, count in field_counts.items()
        if not name.lower().startswith("x-crawler-")
    }
    if not filtered:
        return ""
    top = sorted(filtered.items(), key=lambda kv: kv[1], reverse=True)[:50]
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
        "<h2>Top Response Headers</h2>"
        '<p class="muted">Most common response header names, with the share of '
        "responses that included at least one instance.</p>"
        f"{TRUNCATED_NOTE if truncated else ''}"
        '<table class="data-table">'
        "<thead><tr><th>Header</th><th>Count</th><th>% of responses</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


CSP_BUCKETS = [
    (0, 0, "No CSP header"),
    (1, 99, "1-99 B"),
    (100, 499, "100-499 B"),
    (500, 999, "500-999 B"),
    (1000, 1999, "1000-1999 B"),
    (2000, 4999, "2000-4999 B"),
    (5000, 9999, "5000-9999 B"),
    (10000, None, "10000+ B"),
]


def _bucket_csp_sizes(csp_sizes: Dict[str, int]) -> List[Tuple[str, int]]:
    """Bucket per-site max CSP sizes into the configured ranges.

    Returns a list of (label, count) in bucket order.
    """
    buckets: List[int] = [0] * len(CSP_BUCKETS)
    for size in csp_sizes.values():
        for idx, (low, high, _label) in enumerate(CSP_BUCKETS):
            if size >= low and (high is None or size <= high):
                buckets[idx] += 1
                break
    return [(CSP_BUCKETS[i][2], buckets[i]) for i in range(len(CSP_BUCKETS))]


def render_csp_section(csp_sizes: Dict[str, int]) -> str:
    if not csp_sizes:
        return ""
    total = len(csp_sizes)
    rows: List[str] = []
    for label, count in _bucket_csp_sizes(csp_sizes):
        pct = (count / total * 100) if total else 0
        bar_width = int(pct * 2)  # 200px max
        rows.append(
            f"<tr>"
            f"<td>{html.escape(label)}</td>"
            f"<td>{_format_count(count)}</td>"
            f"<td>{pct:.1f}%</td>"
            f'<td><span class="csp-bar" style="width:{bar_width}px"></span></td>'
            f"</tr>"
        )
    return (
        '<section id="csp">'
        "<h2>Content-Security-Policy size by site</h2>"
        '<p class="muted">Distribution of the maximum CSP header byte size '
        "each site served, across all responses analyzed. A site appears in "
        "exactly one bucket -- the largest CSP it ever returned, regardless "
        "of how many WAT files it appeared in. "
        f"Total sites with header data: {_format_count(total)}.</p>"
        '<table class="data-table csp-table">'
        "<thead><tr><th>CSP size</th><th>Sites</th><th>% of sites</th>"
        "<th></th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


# ---- numeric header value histograms (issue #8) ---------------------------

# Ordered (key, heading) pairs for the corpus-wide numeric-header histograms.
# Order and labels mirror cc_lint.stats.VALUE_HISTOGRAMS; markdown.py keeps an
# identical list so the two renderers stay in sync.
_VALUE_HISTOGRAM_LABELS: List[Tuple[str, str]] = [
    ("cache_control_max_age", "Cache-Control: max-age"),
    ("cache_control_s_maxage", "Cache-Control: s-maxage"),
    ("age", "Age"),
    ("hsts_max_age", "Strict-Transport-Security: max-age"),
    ("cookie_lifetime", "Cookie lifetime (Max-Age / Expires)"),
    ("freshness_lifetime", "Computed freshness lifetime"),
    ("expires_date_delta", "Expires − Date delta"),
]


def _render_value_histogram_table(heading: str, counts: Dict[str, int]) -> str:
    total = sum(counts.values())
    if total == 0:
        return ""
    rows: List[str] = []
    for label in LIFETIME_BUCKET_ORDER:
        count = counts.get(label, 0)
        pct = count / total * 100  # total > 0: guarded above
        bar_width = int(pct * 2)  # 200px max
        rows.append(
            f"<tr>"
            f"<td>{html.escape(label)}</td>"
            f"<td>{_format_count(count)}</td>"
            f"<td>{pct:.1f}%</td>"
            f'<td><span class="csp-bar" style="width:{bar_width}px"></span></td>'
            f"</tr>"
        )
    return (
        f"<h3>{html.escape(heading)}</h3>"
        f'<p class="muted">Total occurrences: {_format_count(total)}.</p>'
        '<table class="data-table csp-table">'
        "<thead><tr><th>Value</th><th>Occurrences</th><th>%</th>"
        "<th></th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def render_value_histograms_section(value_histograms: Dict[str, Dict[str, int]]) -> str:
    """Corpus-wide distributions of numeric/temporal header values (issue #8)."""
    if not value_histograms:
        return ""
    tables = [
        _render_value_histogram_table(heading, value_histograms[key])
        for key, heading in _VALUE_HISTOGRAM_LABELS
        if value_histograms.get(key)
    ]
    tables = [t for t in tables if t]
    if not tables:
        return ""
    return (
        '<section id="value-histograms">'
        "<h2>Numeric header value distributions</h2>"
        '<p class="muted">Per-occurrence distributions of numeric and temporal '
        "header values across all analysed responses that carried the field. "
        "Lifetimes use a shared log-scaled bucket set, so the histograms are "
        'comparable. The "negative" bucket holds anomalies (a value that '
        "parsed negative, or an Expires that predates Date — stale on "
        'arrival); "0" is a distinct deliberate "do not reuse" signal.</p>'
        f"{''.join(tables)}"
        "</section>"
    )


def _render_field_by_layer(
    field_counts_by_layer: Dict[str, Dict[str, int]],
    field_counts: Dict[str, int],
    truncated: bool,
) -> str:
    """Per-header infrastructure distribution (issue #4).

    Answers "which infrastructure emits header H" for the highest-volume
    headers. Shares are relative to each header's total occurrences; layers
    overlap, so a single occurrence can count under several and the per-header
    shares need not sum to 100%.
    """
    if not field_counts_by_layer:
        return ""
    ranked = sorted(
        field_counts_by_layer.items(),
        key=lambda kv: sum(kv[1].values()),
        reverse=True,
    )
    rows: List[str] = []
    for name, layers in ranked[:25]:
        if name.lower().startswith("x-crawler-"):
            continue
        total = field_counts.get(name.lower(), sum(layers.values()))
        layer_bits: List[str] = []
        for layer, count in sorted(layers.items(), key=lambda kv: kv[1], reverse=True)[
            :6
        ]:
            share = f"{count / total * 100:.0f}%" if total else "—"
            layer_bits.append(
                f'<span class="layer-chip">{html.escape(layer)} '
                f'<span class="muted">{share}</span></span>'
            )
        rows.append(
            "<tr>"
            f'<th scope="row">{html.escape(name)}</th>'
            f"<td>{' '.join(layer_bits)}</td>"
            "</tr>"
        )
    if not rows:
        return ""
    return (
        "<h3>Headers by infrastructure</h3>"
        '<p class="muted">For the highest-volume response headers, the share '
        "of each header's occurrences seen on each fingerprint layer. Layers "
        "overlap, so shares need not sum to 100%.</p>"
        f"{TRUNCATED_NOTE if truncated else ''}"
        '<table class="data-table">'
        "<thead><tr><th>Header</th><th>Layers (share of occurrences)</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_infrastructure_section(  # pylint: disable=too-many-positional-arguments
    layer_counts: Dict[str, int],
    field_counts_by_layer: Dict[str, Dict[str, int]],
    field_counts: Dict[str, int],
    total_responses: int,
    roles: Dict[str, str],
    truncated_fcbl: bool,
) -> str:
    """Infrastructure fingerprint overview (issue #4).

    A per-layer response table plus a fingerprint-coverage line, followed by
    the per-header layer breakdown. Layers are best-effort and overlap.
    """
    if not layer_counts:
        return ""
    unmatched = int(layer_counts.get(UNMATCHED, 0))
    matched = {k: int(v) for k, v in layer_counts.items() if k != UNMATCHED}
    fingerprinted = max(0, total_responses - unmatched)
    coverage = (fingerprinted / total_responses * 100) if total_responses else 0

    rows: List[str] = []
    for layer, count in sorted(matched.items(), key=lambda kv: kv[1], reverse=True):
        role = roles.get(layer, "")
        pct = (count / total_responses * 100) if total_responses else 0
        rows.append(
            "<tr>"
            f"<td>{html.escape(layer)}</td>"
            f"<td>{html.escape(role)}</td>"
            f"<td>{_format_count(count)}</td>"
            f"<td>{pct:.1f}%</td>"
            "</tr>"
        )
    table = ""
    if rows:
        table = (
            '<table class="data-table">'
            "<thead><tr><th>Layer</th><th>Role</th><th>Responses</th>"
            "<th>% of responses</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    fcbl_html = _render_field_by_layer(
        field_counts_by_layer, field_counts, truncated_fcbl
    )
    return (
        '<section id="infrastructure">'
        "<h2>Infrastructure</h2>"
        '<p class="muted">Best-effort fingerprint of the CDN / server / '
        "framework / platform behind each response, from signal headers. A "
        "response can match several layers (a stack), so the counts below "
        "overlap and need not sum to the response total. "
        f"Fingerprinted {coverage:.1f}% of responses; "
        f"{_format_count(unmatched)} matched no known layer.</p>"
        f"{table}{fcbl_html}"
        "</section>"
    )


def render_asn_section(
    asn_counts: Dict[str, int],
    total_responses: int,
    asn_to_layer: Dict[int, str],
    truncated: bool,
) -> str:
    """Top networks (ASN) by response count (issue #4).

    Surfaces the big networks the crawler connected to, including ones not yet
    in the fingerprint table -- a discovery aid for tuning the table. Known
    ASNs are annotated with the layer they map to.
    """
    if not asn_counts:
        return ""
    ranked = sorted(asn_counts.items(), key=lambda kv: kv[1], reverse=True)[:50]
    rows: List[str] = []
    for asn_str, count in ranked:
        try:
            label = asn_to_layer.get(int(asn_str), "")
        except ValueError:
            label = ""
        pct = (count / total_responses * 100) if total_responses else 0
        rows.append(
            "<tr>"
            f"<td>AS{html.escape(asn_str)}</td>"
            f"<td>{html.escape(label)}</td>"
            f"<td>{_format_count(count)}</td>"
            f"<td>{pct:.1f}%</td>"
            "</tr>"
        )
    return (
        '<section id="asn">'
        "<h2>Top networks (ASN)</h2>"
        '<p class="muted">Autonomous System the crawl-time IP resolved to, by '
        "response count. Reflects the outermost network the crawler reached "
        "(a fronting CDN, or the origin's host). Networks without a layer "
        "label are not yet in the fingerprint table.</p>"
        f"{TRUNCATED_NOTE if truncated else ''}"
        '<table class="data-table">'
        "<thead><tr><th>ASN</th><th>Layer</th><th>Responses</th>"
        "<th>% of responses</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
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
        "<h2>Top Unsupported Headers</h2>"
        '<p class="muted">Header names httplint did not recognise, ranked by occurrence.</p>'
        f"{TRUNCATED_NOTE if truncated else ''}"
        '<table class="data-table">'
        "<thead><tr><th>Header</th><th>Count</th></tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
        "</section>"
    )


# ---- Vary composition ------------------------------------------------------


def _pct_of_vary(count: int, denom: int) -> str:
    return f"{count / denom * 100:.2f}%" if denom else "—"


def _hll_sites(hlls: Dict[str, Any], key: str) -> Optional[int]:
    registers = hlls.get(key)
    if isinstance(registers, list) and registers:
        est = hll_estimate(registers)
        return est if est > 0 else None
    return None


def _recipe_label_html(recipe: str) -> str:
    if recipe == AE_ONLY_LABEL:
        return f"<em>{html.escape(recipe)}</em>"
    parts: List[str] = []
    for token in recipe_tokens(recipe):
        escaped = html.escape(token)
        if is_nonstandard_token(token):
            parts.append(
                '<span class="vary-synthetic" '
                'title="non-standard / synthetic token">'
                f"{escaped}</span>"
            )
        else:
            parts.append(escaped)
    return ", ".join(parts)


def _render_recipe_table(recipe_dict: Dict[str, Any], denom: int) -> str:
    occ: Dict[str, int] = recipe_dict.get("occ", {})
    hlls: Dict[str, Any] = recipe_dict.get("hlls", {})
    if not occ:
        return '<p class="muted">No data.</p>'
    ordered = sorted(occ.items(), key=lambda kv: kv[1], reverse=True)
    head = ["Recipe", "Responses", "% of Vary", "Sites", "Synthetic tokens"]
    header = "".join(f"<th>{html.escape(h)}</th>" for h in head)
    rows: List[str] = []
    for recipe, count in ordered[:25]:
        sites = _hll_sites(hlls, recipe)
        synth = 0 if recipe == AE_ONLY_LABEL else count_nonstandard(recipe)
        cells = [
            _recipe_label_html(recipe),
            _format_count(count),
            _pct_of_vary(count, denom),
            f"~{_format_count(sites)}" if sites else "—",
            _format_count(synth) if synth else "—",
        ]
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    if len(ordered) > 25:
        rows.append(
            f'<tr><td colspan="{len(head)}" class="muted">'
            f"… {len(ordered) - 25} more recipes not shown …</td></tr>"
        )
    return (
        '<table class="data-table vary-table">'
        f"<thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _render_marginal_table(
    entries: List[Tuple[str, int]],
    hlls: Dict[str, Any],
    denom: int,
    show_registered: bool,
    limit: int = 25,
) -> str:
    if not entries:
        return '<p class="muted">None observed.</p>'
    head = ["Field-name", "Responses", "% of Vary", "Sites"]
    if show_registered:
        head.append("Registered?")
    header = "".join(f"<th>{html.escape(h)}</th>" for h in head)
    rows: List[str] = []
    for token, count in entries[:limit]:
        sites = _hll_sites(hlls, token)
        cells = [
            html.escape(token),
            _format_count(count),
            _pct_of_vary(count, denom),
            f"~{_format_count(sites)}" if sites else "—",
        ]
        if show_registered:
            cells.append("yes" if is_registered_field(token) else "no")
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    if len(entries) > limit:
        rows.append(
            f'<tr><td colspan="{len(head)}" class="muted">'
            f"… {len(entries) - limit} more not shown …</td></tr>"
        )
    return (
        '<table class="data-table vary-table">'
        f"<thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def render_vary_section(vary: Dict[str, Any]) -> str:
    """Render the Vary composition section (issue #3)."""
    if not vary:
        return ""
    denom = int(vary.get("responses_with_vary", 0))
    if denom <= 0:
        return ""
    recipes: Dict[str, Any] = vary.get("recipes") or {}
    marginals: Dict[str, Any] = vary.get("marginals") or {}
    marg_occ: Dict[str, int] = marginals.get("occ", {})
    marg_hlls: Dict[str, Any] = marginals.get("hlls", {})

    blocks: List[str] = [
        '<p class="muted">Composition of <code>Vary</code> across the '
        f"{_format_count(denom)} responses that carried one. A "
        "<strong>recipe</strong> is the lowercased, deduped, sorted set of "
        "field-names in a response's <code>Vary</code> &mdash; where "
        "synthetic cache-key engineering shows up. <em>Responses</em> is "
        "occurrence-weighted (one CDN origin can emit a recipe across "
        "millions of responses); <em>Sites</em> is a HyperLogLog estimate of "
        "distinct operators.</p>",
        "<h3>Top Vary recipes</h3>",
    ]
    if vary.get("recipes_truncated"):
        blocks.append(TRUNCATED_NOTE)
    blocks.append(_render_recipe_table(recipes, denom))
    blocks.append("<h3>Top Vary recipes (Accept-Encoding factored out)</h3>")
    blocks.append(
        '<p class="muted"><code>Accept-Encoding</code> appears in the large '
        "majority of <code>Vary</code> headers and flattens the ranking. "
        "Here it is removed from each recipe and the remainders re-merged "
        "(site counts union exactly), so the meaningful axes surface.</p>"
    )
    factored = factor_out(recipes, ACCEPT_ENCODING, AE_ONLY_LABEL)
    blocks.append(_render_recipe_table(factored, denom))
    blocks.append(
        "<h3>High-interest axes</h3>"
        '<p class="muted">Prevalence of the cache-key / availability axes, as '
        "a share of responses carrying <code>Vary</code>.</p>"
    )
    axes_entries = [(axis, marg_occ.get(axis, 0)) for axis in HIGH_INTEREST_AXES]
    blocks.append(
        _render_marginal_table(axes_entries, marg_hlls, denom, show_registered=False)
    )
    blocks.append("<h3>Vary field-names (marginals)</h3>")
    if vary.get("marginals_truncated"):
        blocks.append(TRUNCATED_NOTE)
    top_marginals = sorted(marg_occ.items(), key=lambda kv: kv[1], reverse=True)
    blocks.append(
        _render_marginal_table(top_marginals, marg_hlls, denom, show_registered=True)
    )
    blocks.append(
        "<h3>Non-standard Vary tokens</h3>"
        '<p class="muted">Tokens httplint\'s field registry does not '
        "recognise (≈ not IANA-registered), approximating the synthetic "
        "cache-key population. A <strong>lower bound</strong>: some schemes "
        "are consumed at the edge and never appear in <code>Vary</code>, and "
        "the classification is approximate (a real request header httplint "
        "lacks a parser for also lands here).</p>"
    )
    nonstandard = sorted(
        ((t, c) for t, c in marg_occ.items() if is_nonstandard_token(t)),
        key=lambda kv: kv[1],
        reverse=True,
    )
    blocks.append(
        _render_marginal_table(nonstandard, marg_hlls, denom, show_registered=False)
    )

    return (
        '<section id="vary">'
        "<h2>Vary composition</h2>"
        f"{''.join(blocks)}"
        "</section>"
    )


# ---- unseen notes ----------------------------------------------------------


def _render_unseen_subblock(title: str, body: str, notes: List[str]) -> str:
    if not notes:
        return ""
    items = "".join(f"<li>{html.escape(n)}</li>" for n in notes)
    return (
        "<details>"
        f"<summary><h3>{html.escape(title)} ({len(notes)})</h3></summary>"
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
        "<h2>Unseen note types</h2>"
        f'{"".join(blocks)}'
        "</section>"
    )
