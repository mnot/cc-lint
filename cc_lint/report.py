"""Render a stats.json into a single-file HTML report.

The renderer is intentionally framework-free: one inline stylesheet, plain
HTML, no JavaScript. Collapsible sections use native ``<details>``. Light
and dark themes are derived from the browser's prefers-color-scheme.
"""

import html
import json
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple

from httplint.note import Note, levels

from cc_lint.hll import hll_estimate


# ---- Severity classification ------------------------------------------------


def _all_note_subclasses(cls: Any) -> Set[Any]:
    return set(cls.__subclasses__()).union(
        {s for c in cls.__subclasses__() for s in _all_note_subclasses(c)}
    )


def _build_severity_index() -> Dict[str, str]:
    index: Dict[str, str] = {}
    for note_cls in _all_note_subclasses(Note):
        # Skip classes from our own test modules that subclass Note as a shim.
        if note_cls.__module__.startswith("tests."):
            continue
        level = getattr(note_cls, "level", None)
        if level == levels.BAD:
            index[note_cls.__name__] = "bad"
        elif level == levels.WARN:
            index[note_cls.__name__] = "warn"
    return index


def _possible_note_ids(severity_index: Dict[str, str]) -> Set[str]:
    return set(severity_index.keys())


# Notes whose firing paths in httplint are only reached when linting a request
# (cc-lint feeds an HttpResponseLinter from WAT response metadata, so these
# can never fire on our pipeline).
REQUEST_ONLY_NOTES: Set[str] = {
    "MISSING_USER_AGENT",
    "REQUEST_CONTENT_NOT_DEFINED",
    "URI_BAD_SYNTAX",
    "URI_TOO_LONG",
    "RESPONSE_HDR_IN_REQUEST",
    "CORS_PREFLIGHT_REQUEST",
    "CORS_PREFLIGHT_REQ_METHOD_WRONG",
    "CORS_PREFLIGHT_REQ_NO_ORIGIN",
    "CORS_PREFLIGHT_REQ_NO_METHOD",
}

# Notes that only fire when the response body is fed to the linter. cc-lint's
# WAT pipeline reads headers only; the body-derived findings below cannot fire
# until/unless we add a full-WARC mode. Listed explicitly so they don't bloat
# the Unseen list.
BODY_ONLY_NOTES: Set[str] = {
    "CHARSET_MISMATCH",
    "CHARSET_IMPLICIT_MISMATCH",
    "CHARSET_UNDECODABLE",
    "BAD_GZIP",
    "BAD_BROTLI",
    "BAD_ZLIB",
    "DECOMPRESSION_LIMIT",
    "CL_INCORRECT",
}


def _classify_unseen(
    possible_ids: Set[str], seen_ids: Set[str]
) -> Tuple[List[str], List[str], List[str]]:
    """Split unseen note ids into reachable / request-only / body-only buckets."""
    unseen = possible_ids - seen_ids
    request_only = sorted(unseen & REQUEST_ONLY_NOTES)
    body_only = sorted(unseen & BODY_ONLY_NOTES)
    reachable_unseen = sorted(unseen - REQUEST_ONLY_NOTES - BODY_ONLY_NOTES)
    return reachable_unseen, request_only, body_only


# ---- HTML helpers -----------------------------------------------------------


REDBOT_BASE = "https://redbot.org/check?uri="


def _redbot_link(url: str) -> str:
    return REDBOT_BASE + urllib.parse.quote(url, safe="")


def _format_count(count: int) -> str:
    return f"{count:,}"


def _format_vars(note_vars: Dict[str, Any]) -> str:
    if not note_vars:
        return ""
    items = [f"{html.escape(str(k))}={html.escape(str(v))}" for k, v in note_vars.items()]
    return f"<span class=\"vars\">{', '.join(items)}</span>"


def _sample_li(sample: Dict[str, Any]) -> str:
    url = sample.get("url", "")
    if not url:
        return ""
    return (
        f'<li><a href="{html.escape(_redbot_link(url))}" target="_blank" rel="noopener">'
        f"{html.escape(url)}</a>{_format_vars(sample.get('vars', {}))}</li>"
    )


# ---- Section renderers ------------------------------------------------------


METHODOLOGY_NOTE = (
    "Percentages describe this Common Crawl result set, not the entire web. "
    "Counts reflect what Common Crawl fetched (after robots.txt, WAF, paywall, "
    "and geofence exclusions), scoped to the Tranco top-sites filter "
    "configured for this run."
)


def _format_pill(label: str, value: str, modifier: str = "") -> str:
    cls = "run-pill" if not modifier else f"run-pill {modifier}"
    return (
        f'<div class="{cls}">'
        f'<span class="pill-label">{html.escape(label)}</span>'
        f'<span class="pill-value">{html.escape(value)}</span>'
        "</div>"
    )


def _render_run_context(
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


def _render_header_stats(
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


def _render_variable_stats(note_data: Dict[str, Any], field_counts: Dict[str, int]) -> str:
    var_stats: Dict[str, Dict[str, int]] = note_data.get("vars", {}) or {}
    if not var_stats:
        return ""
    all_var_samples: Dict[str, Dict[str, List[Dict[str, Any]]]] = note_data.get(
        "var_samples", {}
    ) or {}
    truncated_vars = note_data.get("_truncated_vars") or {}
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
            blocks.append(_render_var_block(var_name, counts, var_samples, field_counts))
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


def _render_notes_section(
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


_TRUNCATED_NOTE = (
    '<p class="muted truncated">The long tail of rare values was elided during '
    "shuffle to keep cluster memory bounded; counts and percentages below "
    "describe the retained head only.</p>"
)


def _render_field_counts_section(
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
        f"{_TRUNCATED_NOTE if truncated else ''}"
        '<table class="data-table">'
        "<thead><tr><th>Header</th><th>Count</th><th>% of responses</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _render_unprocessed_section(
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
        f"{_TRUNCATED_NOTE if truncated else ''}"
        '<table class="data-table">'
        "<thead><tr><th>Header</th><th>Count</th></tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
        "</section>"
    )


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


def _render_missing_section(
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


# ---- CSS --------------------------------------------------------------------


STYLE = """
  :root {
    --bg: #fafafa;
    --fg: #1c1c1c;
    --muted: #5f6168;
    --card: #ffffff;
    --card-border: #e7e7ea;
    --link: #1f4ed8;
    --warn-bg: #fff8e1;
    --warn-fg: #8a5400;
    --warn-border: #f0c356;
    --bad-bg: #fdecec;
    --bad-fg: #a31515;
    --bad-border: #e9a0a0;
    --row-alt: #f5f5f7;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0f1115;
      --fg: #e7e8ea;
      --muted: #9aa0a6;
      --card: #16181d;
      --card-border: #2a2d33;
      --link: #8ab4ff;
      --warn-bg: #2a2218;
      --warn-fg: #ffcb74;
      --warn-border: #5b4626;
      --bad-bg: #2b1818;
      --bad-fg: #ff9c9c;
      --bad-border: #5a2828;
      --row-alt: #1a1c22;
    }
  }
  * { box-sizing: border-box; }
  html { background: var(--bg); color: var(--fg); }
  body {
    font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    margin: 0 auto;
    max-width: 64rem;
    padding: 2rem 1.25rem 4rem;
  }
  a { color: var(--link); text-decoration: none; }
  a:hover { text-decoration: underline; }
  h1, h2, h3, h4 { line-height: 1.25; margin: 0 0 .5rem; }
  h1 { font-size: 1.75rem; }
  h2 { font-size: 1.25rem; margin-top: 2rem; border-bottom: 1px solid var(--card-border); padding-bottom: .25rem; }
  h3 { font-size: 1rem; margin-top: 1rem; }
  h4 { font-size: .9rem; font-weight: 600; margin-top: 1rem; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
  .muted { color: var(--muted); font-size: .85em; }
  .vars { color: var(--muted); font-size: .85em; margin-left: .25em; }

  .hero { padding: 1rem 0 1.5rem; border-bottom: 1px solid var(--card-border); margin-bottom: 1.5rem; }
  .stat-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
    gap: 1rem;
    margin: 1rem 0 0;
  }
  .stat-grid div {
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: .5rem;
    padding: .75rem 1rem;
  }
  .stat-grid dt { font-size: .8em; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
  .stat-grid dd { margin: .25rem 0 0; font-size: 1.5rem; font-weight: 600; }
  .stat-grid dd small { font-size: .65em; font-weight: 400; color: var(--muted); }

  .note-list { display: flex; flex-direction: column; gap: .5rem; }
  details.note {
    background: var(--card);
    border: 1px solid var(--card-border);
    border-left: 4px solid var(--card-border);
    border-radius: .375rem;
    padding: 0;
  }
  details.note.severity-bad { border-left-color: var(--bad-border); }
  details.note.severity-warn { border-left-color: var(--warn-border); }
  details.note > summary {
    cursor: pointer;
    padding: .5rem .75rem;
    display: flex;
    align-items: center;
    gap: .5rem;
    list-style: none;
  }
  details.note > summary::-webkit-details-marker { display: none; }
  details.note > summary::before { content: "▸"; color: var(--muted); transition: transform .15s; }
  details.note[open] > summary::before { transform: rotate(90deg); display: inline-block; }
  .badge {
    display: inline-block;
    padding: .1rem .4rem;
    border-radius: .25rem;
    font-size: .7em;
    font-weight: 600;
    letter-spacing: .04em;
    border: 1px solid;
  }
  .badge-bad { background: var(--bad-bg); color: var(--bad-fg); border-color: var(--bad-border); }
  .badge-warn { background: var(--warn-bg); color: var(--warn-fg); border-color: var(--warn-border); }
  .note-id { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: .9em; }
  .note-sites {
    background: var(--row-alt);
    border: 1px solid var(--card-border);
    border-radius: .25rem;
    color: var(--muted);
    font-size: .75em;
    padding: .05rem .35rem;
    font-variant-numeric: tabular-nums;
  }
  .note-count { margin-left: auto; font-variant-numeric: tabular-nums; color: var(--muted); }
  .note-body { padding: .25rem .75rem .75rem; }
  .note-body > ul.samples { margin: 0 0 .75rem; }

  ul.samples { margin: .5rem 0; padding-left: 1.25rem; }
  ul.samples li { word-break: break-all; line-height: 1.4; }
  ul.errors { margin: 0; padding-left: 1.25rem; }
  ul.errors > li { margin: .25rem 0; }
  ul.errors .err { font-family: ui-monospace, "SF Mono", Menlo, monospace; }

  table.var-table, table.data-table {
    border-collapse: collapse;
    width: 100%;
    margin: .5rem 0 1rem;
    font-size: .9em;
  }
  table.var-table th, table.var-table td,
  table.data-table th, table.data-table td {
    text-align: left;
    padding: .35rem .5rem;
    border-bottom: 1px solid var(--card-border);
    vertical-align: top;
  }
  table.var-table thead th, table.data-table thead th {
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
    font-size: .75em;
    letter-spacing: .04em;
    border-bottom: 2px solid var(--card-border);
  }
  table.var-table tbody tr:nth-child(odd):not(.samples-row) { background: var(--row-alt); }
  table.var-table .samples-row > td { background: transparent; padding-top: 0; padding-bottom: .5rem; }
  table.var-table .samples-row details > summary { cursor: pointer; color: var(--muted); font-size: .85em; }

  .missing-list { columns: 2 14rem; column-gap: 1.25rem; font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: .85em; }
  .missing-list li { break-inside: avoid; }

  .truncated {
    background: var(--warn-bg);
    border-left: 3px solid var(--warn-border);
    color: var(--warn-fg);
    margin: .5rem 0;
    padding: .35rem .6rem;
    border-radius: 0 .25rem .25rem 0;
  }

  section.run-context { margin-top: 1rem; }
  .run-pills { display: flex; flex-wrap: wrap; gap: .4rem; margin: 0 0 .75rem; }
  .run-pill {
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 999px;
    padding: .15rem .65rem;
    display: inline-flex;
    align-items: baseline;
    gap: .4rem;
    font-size: .82em;
  }
  .run-pill.pill-warning {
    background: var(--warn-bg);
    border-color: var(--warn-border);
    color: var(--warn-fg);
  }
  .pill-label { color: var(--muted); font-size: .9em; }
  .pill-value { font-weight: 600; font-variant-numeric: tabular-nums; }
  .methodology {
    color: var(--muted);
    font-size: .85em;
    margin: 0 0 .5rem;
    max-width: 56rem;
  }

  section { margin-top: 2.5rem; }
  section:first-of-type { margin-top: 0; }
"""


# ---- Top-level --------------------------------------------------------------


def _count_total_notes(notes: Dict[str, Any]) -> int:
    total = 0
    for data in notes.values():
        if isinstance(data, dict):
            total += int(data.get("count", 0))
        else:
            total += int(data)
    return total


def _build_html(data: Dict[str, Any]) -> str:
    severity_index = _build_severity_index()
    possible_note_ids = _possible_note_ids(severity_index)

    total_responses = int(data.get("total_responses", 0))
    notes = data.get("notes", data.get("note_counts", {})) or {}
    field_counts: Dict[str, int] = data.get("field_counts", {}) or {}
    unprocessed_counts: Dict[str, int] = data.get("unprocessed_counts", {}) or {}

    total_notes = _count_total_notes(notes)
    seen_note_ids = set(notes.keys())
    reachable_unseen, request_only, body_only = _classify_unseen(
        possible_note_ids, seen_note_ids
    )

    sites_hll = data.get("sites_hll")
    distinct_sites_estimate = (
        hll_estimate(sites_hll) if isinstance(sites_hll, list) and sites_hll else None
    )
    run_context = data.get("run_context") or {}
    finalized_at = data.get("finalized_at")

    body_parts = [
        _render_header_stats(
            total_responses, total_notes, len(seen_note_ids), distinct_sites_estimate
        ),
        _render_run_context(run_context, finalized_at),
        _render_notes_section(notes, field_counts, severity_index),
        _render_field_counts_section(
            field_counts, total_responses, bool(data.get("_truncated_field_counts"))
        ),
        _render_unprocessed_section(
            unprocessed_counts, bool(data.get("_truncated_unprocessed_counts"))
        ),
        _render_missing_section(reachable_unseen, request_only, body_only),
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
