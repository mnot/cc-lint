"""Markdown renderer for cc-lint stats.

Produces a plain-text-friendly version of the same data the HTML report
surfaces, suitable for terminals, GitHub previews, and copy-paste into
chat. Output is intentionally narrower in scope than the HTML: the
markdown view focuses on the high-signal information (totals, top notes,
top headers) and elides interactive affordances like the unseen-notes
collapsibles or the per-(var, val) sample lists.
"""

from typing import Any, Dict, List, Optional, Tuple

from cc_lint.hll import hll_estimate
from cc_lint.report.severity import (
    build_category_index,
    build_severity_index,
    build_summary_index,
    category_display_order,
    classify_unseen,
    possible_note_ids,
)
from cc_lint.report.styles import METHODOLOGY_NOTE

_NOTE_SUMMARIES = build_summary_index()


def _fmt_count(value: int) -> str:
    return f"{value:,}"


def _md_escape_pipe(value: str) -> str:
    """Escape pipe characters so markdown tables don't break."""
    return value.replace("|", "\\|")


def _render_run_context(
    run_context: Dict[str, Any], finalized_at: Optional[str]
) -> List[str]:
    parts: List[str] = []
    crawl_id = run_context.get("crawl_id") or ""
    if crawl_id:
        parts.append(f"**Crawl:** {crawl_id}")
    version = run_context.get("cc_lint_version") or ""
    if version:
        parts.append(f"**cc-lint:** v{version}")
    httplint_version = run_context.get("httplint_version") or ""
    if httplint_version:
        parts.append(f"**httplint:** v{httplint_version}")
    top_n = int(run_context.get("top_sites") or 0)
    if top_n:
        parts.append(f"**Top-sites filter:** Tranco top {_fmt_count(top_n)}")
    else:
        parts.append("**Top-sites filter:** none (full sample)")
    sample_n = int(run_context.get("sample_top_sites") or 0)
    if sample_n:
        parts.append(f"**Sample ceiling:** Tranco top {_fmt_count(sample_n)}")
    record_limit = int(run_context.get("record_limit") or 0)
    if record_limit:
        parts.append(f"**Records / WARC:** {_fmt_count(record_limit)}")
    warc_limit = int(run_context.get("warc_limit") or 0)
    if warc_limit:
        parts.append(f"**WARCs / mapper:** {_fmt_count(warc_limit)}")
    if finalized_at:
        parts.append(f"**Finalized:** {finalized_at}")

    if not parts:
        return []
    return [
        " | ".join(parts),
        "",
        "> " + METHODOLOGY_NOTE,
        "",
    ]


def _render_summary_table(
    total_responses: int,
    total_notes: int,
    seen_count: int,
    distinct_sites_estimate: Optional[int],
) -> List[str]:
    rows = [
        ("Responses analyzed", _fmt_count(total_responses)),
    ]
    if distinct_sites_estimate is not None:
        rows.append(
            ("Distinct sites analyzed", f"~{_fmt_count(distinct_sites_estimate)}")
        )
    rows.extend(
        [
            ("Note occurrences", _fmt_count(total_notes)),
            ("Distinct note types seen", _fmt_count(seen_count)),
        ]
    )
    lines = ["| Metric | Value |", "| --- | --- |"]
    for label, value in rows:
        lines.append(f"| {label} | {value} |")
    lines.append("")
    return lines


def _render_var_table(
    var_name: str,
    counts: Dict[str, int],
    field_counts: Dict[str, int],
) -> List[str]:
    is_field_name = var_name == "field_name"
    headers = ["Value", "Count"]
    if is_field_name:
        headers += ["Total", "%"]

    sorted_vals = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:25]
    lines = [f"#### {var_name}", ""]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for val, val_count in sorted_vals:
        cells = [_md_escape_pipe(val), _fmt_count(val_count)]
        if is_field_name:
            total = field_counts.get(val.lower(), 0)
            if total:
                pct = val_count / total * 100
                cells += [_fmt_count(total), f"{pct:.2f}%"]
            else:
                cells += [_fmt_count(total), "—"]
        lines.append("| " + " | ".join(cells) + " |")
    if len(counts) > 25:
        lines.append(f"_… {len(counts) - 25} more values not shown …_")
    lines.append("")
    return lines


def _render_note_block(
    note_id: str,
    note_data: Dict[str, Any],
    severity: str,
    field_counts: Dict[str, int],
) -> List[str]:
    count = int(note_data.get("count", 0))

    heading_bits = [f"### `{severity.upper()}` `{note_id}`"]
    heading_bits.append(f"— {_fmt_count(count)} occurrences")
    sites_hll = note_data.get("sites_hll")
    if sites_hll:
        site_est = hll_estimate(sites_hll)
        if site_est > 0:
            heading_bits.append(f"(~{_fmt_count(site_est)} sites)")
    lines = [" ".join(heading_bits), ""]

    summary_template = _NOTE_SUMMARIES.get(note_id, "")
    if summary_template:
        lines.append(summary_template)
        lines.append("")

    samples = note_data.get("samples") or []
    if samples:
        lines.append("Samples:")
        for sample in samples:
            url = sample.get("url", "")
            if not url:
                continue
            lines.append(f"- {url}")
        lines.append("")

    truncated_vars = note_data.get("truncated_vars") or {}
    var_stats = note_data.get("vars") or {}
    for var_name, counts in var_stats.items():
        if not counts:
            continue
        if truncated_vars.get(var_name):
            lines.append(
                f"_{var_name}: long tail elided during shuffle; head only._"
            )
        lines.extend(_render_var_table(var_name, counts, field_counts))
    return lines


_SEVERITY_ORDER_MD = {"bad": 4, "warn": 3, "info": 2, "good": 1}

_CATEGORY_LABELS_MD = {
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


def _pretty_category_md(category: str) -> str:
    return _CATEGORY_LABELS_MD.get(category, category.title())


def _render_health_section(severity_counts: Dict[str, int]) -> List[str]:
    if not severity_counts:
        return []
    total = sum(int(v) for v in severity_counts.values())
    if total <= 0:
        return []
    lines = [
        "## Response health",
        "",
        "Each response is bucketed by the most severe httplint finding it "
        "produced. Per-response, not per-site.",
        "",
        "| Severity | Responses | % |",
        "| --- | --- | --- |",
    ]
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
        lines.append(f"| `{label}` | {_fmt_count(count)} | {pct:.1f}% |")
    lines.append("")
    return lines


def _render_category_overview(
    notes: Dict[str, Any],
    category_index: Dict[str, str],
    category_order: List[str],
) -> List[str]:
    if not notes:
        return []
    by_category: Dict[str, Tuple[int, int]] = {}
    for note_id, data in notes.items():
        category = category_index.get(note_id, "UNCATEGORIZED")
        count = int(data.get("count", 0)) if isinstance(data, dict) else 0
        prev_occ, prev_types = by_category.get(category, (0, 0))
        by_category[category] = (prev_occ + count, prev_types + 1)
    if not by_category:
        return []
    seen_in_order = [c for c in category_order if c in by_category]
    for category in by_category:
        if category not in seen_in_order:
            seen_in_order.append(category)
    lines = [
        "## Findings by category",
        "",
        "| Category | Occurrences | Note types fired |",
        "| --- | --- | --- |",
    ]
    for category in seen_in_order:
        occurrences, types = by_category[category]
        lines.append(
            f"| {_pretty_category_md(category)} | {_fmt_count(occurrences)} | "
            f"{_fmt_count(types)} |"
        )
    lines.append("")
    return lines


def _render_notes_section(
    notes: Dict[str, Any],
    field_counts: Dict[str, int],
    severity_index: Dict[str, str],
    category_index: Dict[str, str],
    category_order: List[str],
) -> List[str]:
    if not notes:
        return []
    by_category: Dict[str, List[Tuple[str, Dict[str, Any], str]]] = {}
    for note_id, data in notes.items():
        category = category_index.get(note_id, "UNCATEGORIZED")
        severity = severity_index.get(note_id, "warn")
        by_category.setdefault(category, []).append((note_id, data, severity))
    ordered = [c for c in category_order if c in by_category]
    for category in by_category:
        if category not in ordered:
            ordered.append(category)
    lines = ["## Notes", ""]
    for category in ordered:
        entries = sorted(
            by_category[category],
            key=lambda triple: (
                -_SEVERITY_ORDER_MD.get(triple[2], 0),
                -int(triple[1].get("count", 0))
                if isinstance(triple[1], dict)
                else 0,
                triple[0],
            ),
        )
        total_occ = sum(
            int(d.get("count", 0)) if isinstance(d, dict) else 0
            for _, d, _s in entries
        )
        lines.append(
            f"### {_pretty_category_md(category)} "
            f"_({_fmt_count(total_occ)} occurrences, "
            f"{_fmt_count(len(entries))} note types)_"
        )
        lines.append("")
        for note_id, data, severity in entries:
            lines.extend(
                _render_note_block(note_id, data, severity, field_counts)
            )
    return lines


def _render_field_counts(
    field_counts: Dict[str, int], total_responses: int, truncated: bool
) -> List[str]:
    if not field_counts:
        return []
    lines = ["## Top Response Headers", ""]
    if truncated:
        lines.append("_Long tail elided during shuffle; head only._")
        lines.append("")
    lines.append("| Header | Count | % of responses |")
    lines.append("| --- | --- | --- |")
    top = sorted(field_counts.items(), key=lambda kv: kv[1], reverse=True)[:50]
    for name, count in top:
        pct = (count / total_responses * 100) if total_responses else 0
        lines.append(
            f"| {_md_escape_pipe(name)} | {_fmt_count(count)} | {pct:.1f}% |"
        )
    lines.append("")
    return lines


_CSP_BUCKETS = [
    (0, 0, "No CSP header"),
    (1, 99, "1-99 B"),
    (100, 499, "100-499 B"),
    (500, 999, "500-999 B"),
    (1000, 1999, "1000-1999 B"),
    (2000, 4999, "2000-4999 B"),
    (5000, 9999, "5000-9999 B"),
    (10000, None, "10000+ B"),
]


def _render_csp_section(csp_sizes: Dict[str, int]) -> List[str]:
    if not csp_sizes:
        return []
    total = len(csp_sizes)
    counts: List[int] = [0] * len(_CSP_BUCKETS)
    for size in csp_sizes.values():
        for idx, (low, high, _label) in enumerate(_CSP_BUCKETS):
            if size >= low and (high is None or size <= high):
                counts[idx] += 1
                break
    lines = [
        "## Content-Security-Policy size by site",
        "",
        "Maximum CSP header byte size each site served across all responses; "
        "each site counted once at its largest CSP.",
        "",
        "| CSP size | Sites | % of sites |",
        "| --- | --- | --- |",
    ]
    for idx, (_low, _high, label) in enumerate(_CSP_BUCKETS):
        count = counts[idx]
        pct = (count / total * 100) if total else 0
        lines.append(f"| {label} | {_fmt_count(count)} | {pct:.1f}% |")
    lines.append("")
    return lines


def _render_unprocessed(
    unprocessed_counts: Dict[str, int], truncated: bool
) -> List[str]:
    if not unprocessed_counts:
        return []
    lines = ["## Top Unsupported Headers", ""]
    if truncated:
        lines.append("_Long tail elided during shuffle; head only._")
        lines.append("")
    lines.append("| Header | Count |")
    lines.append("| --- | --- |")
    top = sorted(unprocessed_counts.items(), key=lambda kv: kv[1], reverse=True)[:50]
    for name, count in top:
        lines.append(f"| {_md_escape_pipe(name)} | {_fmt_count(count)} |")
    lines.append("")
    return lines


def _render_unseen(
    reachable_unseen: List[str],
    request_only: List[str],
    body_only: List[str],
) -> List[str]:
    if not (reachable_unseen or request_only or body_only):
        return []
    lines = ["## Unseen note types", ""]
    if reachable_unseen:
        lines.append(
            f"**Reachable but not triggered ({len(reachable_unseen)}):** "
            + ", ".join(f"`{n}`" for n in reachable_unseen)
        )
        lines.append("")
    if body_only:
        lines.append(
            f"**Body-only — unreachable in WAT mode ({len(body_only)}):** "
            + ", ".join(f"`{n}`" for n in body_only)
        )
        lines.append("")
    if request_only:
        lines.append(
            f"**Request-only — unreachable for response linting "
            f"({len(request_only)}):** "
            + ", ".join(f"`{n}`" for n in request_only)
        )
        lines.append("")
    return lines


def _count_total_notes(notes: Dict[str, Any]) -> int:
    return sum(int(data.get("count", 0)) for data in notes.values())


def render_markdown(data: Dict[str, Any]) -> str:
    severity_index = build_severity_index()
    category_index = build_category_index()
    category_order = category_display_order()
    possible_ids = possible_note_ids(severity_index)

    total_responses = int(data.get("total_responses", 0))
    notes = data.get("notes") or {}
    field_counts: Dict[str, int] = data.get("field_counts") or {}
    unprocessed_counts: Dict[str, int] = data.get("unprocessed_counts") or {}
    severity_counts = data.get("severity_counts") or {}
    total_notes = _count_total_notes(notes)
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

    lines: List[str] = ["# Common Crawl Response Lint", ""]
    lines.extend(_render_run_context(run_context, finalized_at))
    lines.extend(
        _render_summary_table(
            total_responses, total_notes, len(seen_note_ids), distinct_sites_estimate
        )
    )
    lines.extend(_render_health_section(severity_counts))
    lines.extend(
        _render_category_overview(notes, category_index, category_order)
    )
    lines.extend(
        _render_notes_section(
            notes, field_counts, severity_index, category_index, category_order
        )
    )
    lines.extend(
        _render_field_counts(
            field_counts, total_responses, bool(data.get("truncated_field_counts"))
        )
    )
    lines.extend(_render_csp_section(data.get("csp_max_by_site") or {}))
    lines.extend(
        _render_unprocessed(
            unprocessed_counts, bool(data.get("truncated_unprocessed_counts"))
        )
    )
    lines.extend(_render_unseen(reachable_unseen, request_only, body_only))
    return "\n".join(lines).rstrip() + "\n"
