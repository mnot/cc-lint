"""Markdown renderer for cc-lint stats.

Produces a plain-text-friendly version of the same data the HTML report
surfaces, suitable for terminals, GitHub previews, and copy-paste into
chat. Output is intentionally narrower in scope than the HTML: the
markdown view focuses on the high-signal information (totals, top notes,
top headers) and elides interactive affordances like the unseen-notes
collapsibles or the per-(var, val) sample lists.
"""

from typing import Any, Dict, List, Optional

from cc_lint.hll import hll_estimate
from cc_lint.report.severity import (
    build_severity_index,
    classify_unseen,
    possible_note_ids,
)
from cc_lint.report.styles import METHODOLOGY_NOTE


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
    if not isinstance(note_data, dict):
        return []
    count = int(note_data.get("count", 0))

    heading_bits = [f"### `{severity.upper()}` `{note_id}`"]
    heading_bits.append(f"— {_fmt_count(count)} occurrences")
    sites_hll = note_data.get("sites_hll")
    if sites_hll:
        site_est = hll_estimate(sites_hll)
        if site_est > 0:
            heading_bits.append(f"(~{_fmt_count(site_est)} sites)")
    lines = [" ".join(heading_bits), ""]

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


def _render_notes_section(
    notes: Dict[str, Any],
    field_counts: Dict[str, int],
    severity_index: Dict[str, str],
) -> List[str]:
    if not notes:
        return []
    sorted_notes = sorted(
        notes.items(),
        key=lambda item: (
            item[1].get("count", 0) if isinstance(item[1], dict) else int(item[1])
        ),
        reverse=True,
    )
    lines = ["## Notes", ""]
    for note_id, data in sorted_notes:
        severity = severity_index.get(note_id, "warn")
        lines.extend(_render_note_block(note_id, data, severity, field_counts))
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
    total = 0
    for data in notes.values():
        if isinstance(data, dict):
            total += int(data.get("count", 0))
        else:
            total += int(data)
    return total


def render_markdown(data: Dict[str, Any]) -> str:
    severity_index = build_severity_index()
    possible_ids = possible_note_ids(severity_index)

    total_responses = int(data.get("total_responses", 0))
    notes = data.get("notes") or {}
    field_counts: Dict[str, int] = data.get("field_counts") or {}
    unprocessed_counts: Dict[str, int] = data.get("unprocessed_counts") or {}
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
    lines.extend(_render_notes_section(notes, field_counts, severity_index))
    lines.extend(
        _render_field_counts(
            field_counts, total_responses, bool(data.get("truncated_field_counts"))
        )
    )
    lines.extend(
        _render_unprocessed(
            unprocessed_counts, bool(data.get("truncated_unprocessed_counts"))
        )
    )
    lines.extend(_render_unseen(reachable_unseen, request_only, body_only))
    return "\n".join(lines).rstrip() + "\n"
