"""Markdown renderer for cc-lint stats.

Produces a plain-text-friendly version of the same data the HTML report
surfaces, suitable for terminals, GitHub previews, copy-paste into chat,
and -- the primary use -- feeding to an LLM for analysis. Output is
narrower than the HTML in chrome (it elides interactive affordances like
the unseen-notes collapsibles), but it carries the same high-signal data,
including the per-field sample URLs and their captured on-the-wire header
values, which are exactly what downstream analysis needs.
"""

# pylint: disable=too-many-lines
# The Markdown and HTML renderers are deliberately one module each so the two
# views stay trivially diffable against one another; splitting this would only
# scatter closely-related report code without reducing coupling.

from typing import Any, Dict, List, Optional, Tuple

from cc_lint.cache_control import COUNT_FIELD as CC_COUNT_FIELD
from cc_lint.cache_control import count_nonstandard as cc_count_nonstandard
from cc_lint.cache_control import is_nonstandard_directive
from cc_lint.cooccur import (
    EMPTY_BUNDLE_LABEL,
    conditional_lifts,
    empty_bundle_count,
    layer_default_bundles,
    ranked_bundles,
    ranked_marginals,
)
from cc_lint.fingerprint import UNMATCHED, default_fingerprinter
from cc_lint.header_categories import categorize_header_bytes
from cc_lint.histograms import (
    BYTE_BUCKET_ORDER,
    LIFETIME_BUCKET_ORDER,
    bucket_order,
)
from cc_lint.hll import hll_estimate
from cc_lint.recipes import recipe_tokens
from cc_lint.report.severity import (
    build_category_index,
    build_severity_index,
    build_summary_index,
    category_display_order,
    classify_unseen,
    possible_note_ids,
)
from cc_lint.report.styles import METHODOLOGY_NOTE
from cc_lint.transition import transition_rows
from cc_lint.vary import (
    ACCEPT_ENCODING,
    AE_ONLY_LABEL,
    HIGH_INTEREST_AXES,
    count_nonstandard,
    factor_out,
    is_nonstandard_token,
    is_registered_field,
)

_NOTE_SUMMARIES = build_summary_index()

_VAR_LABELS_MD = {
    "directive_conflicts": "Directive → conflicts",
    "field_name_key": "Field name → key",
    "freshness_left_bucket": "Freshness remaining",
    "duration_bucket": "Duration",
    "cookie_value_size_bucket": "Cookie value size",
    "field_size_bucket": "Field size",
    "field_error": "Field → parse error",
}


def _fmt_count(value: int) -> str:
    return f"{value:,}"


def _md_escape_pipe(value: str) -> str:
    """Escape pipe characters so markdown tables don't break."""
    return value.replace("|", "\\|")


def _md_inline_code(value: str) -> str:
    """Wrap a value in an inline code span that survives embedded backticks.

    Untrusted on-the-wire header values can contain backticks; a plain
    ``` `value` ``` span would terminate early at the first one. Per
    CommonMark, delimit with a run of backticks one longer than the longest
    run inside the value, padding with a space when it starts or ends with a
    backtick.
    """
    longest = 0
    current = 0
    for char in value:
        if char == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    fence = "`" * (longest + 1)
    pad = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{pad}{value}{pad}{fence}"


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


def _fmt_byte_size_md(byte_size: int) -> str:
    if byte_size < 1024:
        return f"{byte_size} B"
    if byte_size < 1024 * 1024:
        return f"{byte_size / 1024:.1f} KB"
    return f"{byte_size / (1024 * 1024):.1f} MB"


def _render_value_samples(
    value_samples: Dict[str, List[Dict[str, Any]]], shown_values: List[str]
) -> List[str]:
    """Render per-field sample URLs + captured header values as a nested list.

    ``shown_values`` is the set of values whose rows the table actually showed,
    so samples for elided long-tail values aren't dangled without context.
    """
    blocks: List[str] = []
    for val in shown_values:
        samples = value_samples.get(val) or []
        urls = [s for s in samples if s.get("url")]
        if not urls:
            continue
        blocks.append(f"- {_md_inline_code(val)} — Samples ({_fmt_count(len(urls))})")
        for sample in urls:
            captured = sample.get("vars", {}).get("field_values")
            suffix = f" — {_md_inline_code(captured)}" if captured else ""
            blocks.append(f"  - {sample['url']}{suffix}")
    if not blocks:
        return []
    return ["Samples by value:", "", *blocks, ""]


def _render_var_table(
    var_name: str,
    counts: Dict[str, int],
    field_counts: Dict[str, int],
    largest_by_value: Optional[Dict[str, int]] = None,
    value_samples: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[str]:
    is_field_name = var_name == "field_name"
    headers = ["Value", "Note fires"]
    if is_field_name:
        headers += ["Header occurrences", "Fires per occurrence"]
    if largest_by_value:
        headers.append("Largest seen")

    bucket_seq = bucket_order(var_name)
    if bucket_seq is not None:
        order_index = {label: idx for idx, label in enumerate(bucket_seq)}
        sorted_vals = sorted(
            counts.items(), key=lambda kv: order_index.get(kv[0], len(bucket_seq))
        )[:25]
    else:
        sorted_vals = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:25]
    heading = _VAR_LABELS_MD.get(var_name, var_name)
    lines = [f"#### {heading}", ""]
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
        if largest_by_value:
            largest = largest_by_value.get(val)
            cells.append(_fmt_byte_size_md(largest) if largest else "—")
        lines.append("| " + " | ".join(cells) + " |")
    if len(counts) > 25:
        lines.append(f"_… {len(counts) - 25} more values not shown …_")
    lines.append("")
    if value_samples:
        lines.extend(
            _render_value_samples(value_samples, [val for val, _ in sorted_vals])
        )
    return lines


def _render_field_error_block(var_name: str, counts: Dict[str, int]) -> List[str]:
    """Render the STRUCTURED_FIELD_PARSE_ERROR.field_error grouping.

    Mirrors the HTML renderer: the "field: error" composite keys are grouped
    by field, ordered by per-field total, and capped at 50 fields. The HTML
    table puts each field's errors in a bulleted cell; here they are joined
    into one "Errors" cell as "error (count)" pairs.
    """
    grouped: Dict[str, List[Tuple[str, int]]] = {}
    for full_key, count in counts.items():
        field, _, error = full_key.partition(": ")
        grouped.setdefault(field, []).append((error or full_key, count))

    field_totals = {f: sum(c for _, c in errs) for f, errs in grouped.items()}
    sorted_fields = sorted(field_totals.items(), key=lambda item: item[1], reverse=True)

    heading = _VAR_LABELS_MD.get(var_name, var_name)
    lines = [
        f"#### {heading}",
        "",
        "| Field | Note fires | Errors |",
        "| --- | --- | --- |",
    ]
    for field, total in sorted_fields[:50]:
        errors = sorted(grouped[field], key=lambda item: item[1], reverse=True)
        err_text = "; ".join(
            f"{_md_escape_pipe(err)} ({_fmt_count(count)})" for err, count in errors
        )
        lines.append(f"| {_md_escape_pipe(field)} | {_fmt_count(total)} | {err_text} |")
    if len(sorted_fields) > 50:
        lines.append(f"_… {len(sorted_fields) - 50} more fields not shown …_")
    lines.append("")
    return lines


def _render_note_block(
    note_id: str,
    note_data: Dict[str, Any],
    severity: str,
    field_counts: Dict[str, int],
    distinct_sites_estimate: Optional[int] = None,
) -> List[str]:
    count = int(note_data.get("count", 0))

    heading_bits = [f"### `{severity.upper()}` `{note_id}`"]
    heading_bits.append(f"— {_fmt_count(count)} occurrences")
    sites_hll = note_data.get("sites_hll")
    if sites_hll:
        site_est = hll_estimate(sites_hll)
        if site_est > 0:
            if distinct_sites_estimate and distinct_sites_estimate > 0:
                share = site_est / distinct_sites_estimate * 100
                heading_bits.append(f"(~{_fmt_count(site_est)} sites, {share:.1f}%)")
            else:
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
    numeric_maxes: Dict[str, Dict[str, int]] = note_data.get("numeric_maxes") or {}
    field_size_max = numeric_maxes.get("field_size") or {}
    var_samples: Dict[str, Dict[str, List[Dict[str, Any]]]] = (
        note_data.get("var_samples") or {}
    )
    field_samples = var_samples.get("field_name") or {}
    var_stats = note_data.get("vars") or {}
    for var_name, counts in var_stats.items():
        if not counts:
            continue
        if truncated_vars.get(var_name):
            lines.append(f"_{var_name}: long tail elided during shuffle; head only._")
        if var_name == "field_error":
            lines.extend(_render_field_error_block(var_name, counts))
            continue
        largest = field_size_max if var_name == "field_name" else None
        samples = field_samples if var_name == "field_name" else None
        lines.extend(
            _render_var_table(var_name, counts, field_counts, largest, samples)
        )

    by_layer = note_data.get("by_layer") or {}
    if by_layer:
        ordered = sorted(by_layer.items(), key=lambda kv: kv[1], reverse=True)[:10]
        bits = []
        for layer, fired in ordered:
            layer_share = f"{fired / count * 100:.0f}%" if count else "—"
            bits.append(f"{layer} ({_fmt_count(fired)}, {layer_share})")
        lines.append("By infrastructure: " + ", ".join(bits))
        lines.append("")
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
    total_occ_all = sum(occ for occ, _ in by_category.values())
    lines = [
        "## Findings by category",
        "",
        "| Category | Occurrences | % | Note types fired |",
        "| --- | --- | --- | --- |",
    ]
    for category in seen_in_order:
        occurrences, types = by_category[category]
        share = (
            f"{occurrences / total_occ_all * 100:.1f}%" if total_occ_all > 0 else "—"
        )
        lines.append(
            f"| {_pretty_category_md(category)} | {_fmt_count(occurrences)} | "
            f"{share} | {_fmt_count(types)} |"
        )
    lines.append("")
    return lines


def _render_notes_section(  # pylint: disable=too-many-positional-arguments
    notes: Dict[str, Any],
    field_counts: Dict[str, int],
    severity_index: Dict[str, str],
    category_index: Dict[str, str],
    category_order: List[str],
    total_notes: int = 0,
    distinct_sites_estimate: Optional[int] = None,
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

        def _key(triple: Tuple[str, Dict[str, Any], str]) -> Tuple[int, int, int, str]:
            note_id, data, sev = triple
            if not isinstance(data, dict):
                return (0, 0, 0, note_id)
            sites_hll = data.get("sites_hll")
            sites = (
                hll_estimate(sites_hll)
                if isinstance(sites_hll, list) and sites_hll
                else 0
            )
            count = int(data.get("count", 0))
            return (-_SEVERITY_ORDER_MD.get(sev, 0), -sites, -count, note_id)

        entries = sorted(by_category[category], key=_key)
        total_occ = sum(
            int(d.get("count", 0)) if isinstance(d, dict) else 0 for _, d, _s in entries
        )
        cat_pct = ""
        if total_notes > 0:
            cat_pct = f", {total_occ / total_notes * 100:.1f}% of occurrences"
        lines.append(
            f"### {_pretty_category_md(category)} "
            f"_({_fmt_count(total_occ)} occurrences, "
            f"{_fmt_count(len(entries))} note types{cat_pct})_"
        )
        lines.append("")
        for note_id, data, severity in entries:
            lines.extend(
                _render_note_block(
                    note_id, data, severity, field_counts, distinct_sites_estimate
                )
            )
    return lines


def _render_field_counts(
    field_counts: Dict[str, int], total_responses: int, truncated: bool
) -> List[str]:
    if not field_counts:
        return []
    filtered = {
        name: count
        for name, count in field_counts.items()
        if not name.lower().startswith("x-crawler-")
    }
    if not filtered:
        return []
    lines = ["## Top Response Headers", ""]
    if truncated:
        lines.append("_Long tail elided during shuffle; head only._")
        lines.append("")
    lines.append("| Header | Count | % of responses |")
    lines.append("| --- | --- | --- |")
    top = sorted(filtered.items(), key=lambda kv: kv[1], reverse=True)[:50]
    for name, count in top:
        pct = (count / total_responses * 100) if total_responses else 0
        lines.append(f"| {_md_escape_pipe(name)} | {_fmt_count(count)} | {pct:.1f}% |")
    lines.append("")
    return lines


# Human labels + callouts for the byte-economics categories (issue #10).
# Kept in sync with sections._HEADER_CATEGORY_LABELS / _HEADER_BYTE_CALLOUTS.
_HEADER_CATEGORY_LABELS_MD = {
    "standard": "Standard (registered)",
    "deprecated": "Deprecated / obsoleted",
    "proprietary": "Proprietary (non-registered)",
}

_HEADER_BYTE_CALLOUTS_MD = [
    (
        "set-cookie",
        "Cost is upstream and recurring: echoed on every subsequent request.",
    ),
    ("content-security-policy", "The largest single policy header."),
]


def _render_header_bytes(
    field_bytes: Dict[str, int],
    header_block_hist: Dict[str, int],
    total_header_bytes: int,
    total_responses: int,
    truncated: bool,
) -> List[str]:
    """Header byte economics (issue #10): distribution, categories, top-by-bytes."""
    if not field_bytes and not total_header_bytes:
        return []
    mean_bytes = total_header_bytes / total_responses if total_responses else 0
    lines = [
        "## Header byte economics",
        "",
        "Uncompressed response-header bytes (`len(name) + len(value) + 4` for "
        "the `: ` and CRLF framing, per occurrence). HPACK/QPACK compress "
        "repeated headers, so this overstates on-the-wire cost; it measures "
        "origin / pre-compression intent. `x-crawler-*` headers excluded.",
        "",
        f"Mean header block: **{_fmt_byte_size_md(int(mean_bytes))}** per "
        f"response across {_fmt_count(total_responses)} responses.",
        "",
    ]

    dist_total = sum(header_block_hist.values())
    if dist_total:
        lines.append("### Header-block size distribution")
        lines.append("")
        lines.append("| Header-block size | Responses | % of responses |")
        lines.append("| --- | --- | --- |")
        for label in BYTE_BUCKET_ORDER:
            count = header_block_hist.get(label, 0)
            if not count:
                continue
            pct = count / dist_total * 100
            lines.append(f"| {label} | {_fmt_count(count)} | {pct:.1f}% |")
        lines.append("")

    categories = categorize_header_bytes(field_bytes)
    cat_total = sum(byte_total for _, byte_total in categories)
    if cat_total:
        lines.append("### Bytes by field category")
        lines.append("")
        if truncated:
            lines.append("_Long tail elided during shuffle; head only._")
            lines.append("")
        lines.append("| Category | Bytes | % of header bytes |")
        lines.append("| --- | --- | --- |")
        for category, byte_total in categories:
            label = _HEADER_CATEGORY_LABELS_MD.get(category, category)
            pct = byte_total / cat_total * 100
            lines.append(f"| {label} | {_fmt_byte_size_md(byte_total)} | {pct:.1f}% |")
        lines.append("")

        callouts = [
            (name, gloss, field_bytes.get(name, 0))
            for name, gloss in _HEADER_BYTE_CALLOUTS_MD
            if field_bytes.get(name, 0)
        ]
        if callouts:
            lines.append("### Notable single headers")
            lines.append("")
            lines.append("| Header | Bytes | % of header bytes | Note |")
            lines.append("| --- | --- | --- | --- |")
            for name, gloss, byte_total in callouts:
                pct = byte_total / cat_total * 100
                lines.append(
                    f"| `{_md_escape_pipe(name)}` | {_fmt_byte_size_md(byte_total)} "
                    f"| {pct:.1f}% | {_md_escape_pipe(gloss)} |"
                )
            lines.append("")

        lines.append("### Top Response Headers by bytes")
        lines.append("")
        if truncated:
            lines.append("_Long tail elided during shuffle; head only._")
            lines.append("")
        lines.append(
            "A different ranking than top-by-count: a header can be rare but "
            "huge, or ubiquitous but small."
        )
        lines.append("")
        lines.append("| Header | Bytes | % of header bytes |")
        lines.append("| --- | --- | --- |")
        top = sorted(field_bytes.items(), key=lambda kv: kv[1], reverse=True)[:50]
        for name, byte_total in top:
            pct = byte_total / cat_total * 100
            lines.append(
                f"| {_md_escape_pipe(name)} | {_fmt_byte_size_md(byte_total)} "
                f"| {pct:.1f}% |"
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


# Ordered (key, heading) pairs for the corpus-wide numeric-header histograms
# (issue #8). Mirrors sections._VALUE_HISTOGRAM_LABELS so the HTML and Markdown
# renderers stay in sync.
_VALUE_HISTOGRAM_LABELS: List[Tuple[str, str]] = [
    ("cache_control_max_age", "Cache-Control: max-age"),
    ("cache_control_s_maxage", "Cache-Control: s-maxage"),
    ("age", "Age"),
    ("hsts_max_age", "Strict-Transport-Security: max-age"),
    ("cookie_lifetime", "Cookie lifetime (Max-Age / Expires)"),
    ("freshness_lifetime", "Computed freshness lifetime"),
    ("expires_date_delta", "Expires − Date delta"),
]


def _render_value_histograms(value_histograms: Dict[str, Dict[str, int]]) -> List[str]:
    if not value_histograms:
        return []
    blocks: List[str] = []
    for key, heading in _VALUE_HISTOGRAM_LABELS:
        counts = value_histograms.get(key) or {}
        total = sum(counts.values())
        if total == 0:
            continue
        blocks.append(f"### {heading}")
        blocks.append("")
        blocks.append(f"Total occurrences: {_fmt_count(total)}.")
        blocks.append("")
        blocks.append("| Value | Occurrences | % |")
        blocks.append("| --- | --- | --- |")
        for label in LIFETIME_BUCKET_ORDER:
            count = counts.get(label, 0)
            pct = count / total * 100  # total > 0: guarded above
            blocks.append(f"| {label} | {_fmt_count(count)} | {pct:.1f}% |")
        blocks.append("")
    if not blocks:
        return []
    lines = [
        "## Numeric header value distributions",
        "",
        "Per-occurrence distributions of numeric and temporal header values "
        "across all analysed responses that carried the field. Lifetimes share "
        "a log-scaled bucket set, so the histograms are comparable. The "
        '"negative" bucket holds anomalies (a value that parsed negative, or '
        'an Expires that predates Date); "0" is a distinct deliberate '
        '"do not reuse" signal.',
        "",
    ]
    lines.extend(blocks)
    return lines


def _vary_pct(count: int, denom: int) -> str:
    return f"{count / denom * 100:.2f}%" if denom else "—"


def _vary_sites(hlls: Dict[str, Any], key: str) -> str:
    registers = hlls.get(key)
    if isinstance(registers, list) and registers:
        est = hll_estimate(registers)
        if est > 0:
            return f"~{_fmt_count(est)}"
    return "—"


def _recipe_label_md(recipe: str) -> str:
    """Recipe string with non-standard tokens bolded inline.

    Mirrors the HTML renderer's ``vary-synthetic`` highlighting so both
    views flag *which* tokens are synthetic, not just how many (the
    aggregate count is the adjacent column). The AE-only placeholder is
    not a token list, so it renders verbatim.
    """
    if recipe == AE_ONLY_LABEL:
        return _md_escape_pipe(recipe)
    parts = []
    for token in recipe_tokens(recipe):
        escaped = _md_escape_pipe(token)
        parts.append(f"**{escaped}**" if is_nonstandard_token(token) else escaped)
    return ", ".join(parts)


def _render_recipe_md(recipe_dict: Dict[str, Any], denom: int) -> List[str]:
    occ: Dict[str, int] = recipe_dict.get("occ", {})
    hlls: Dict[str, Any] = recipe_dict.get("hlls", {})
    if not occ:
        return ["_No data._", ""]
    ordered = sorted(occ.items(), key=lambda kv: kv[1], reverse=True)
    lines = [
        "| Recipe | Responses | % of Vary | Sites | Synthetic tokens |",
        "| --- | --- | --- | --- | --- |",
    ]
    for recipe, count in ordered[:25]:
        synth = 0 if recipe == AE_ONLY_LABEL else count_nonstandard(recipe)
        lines.append(
            f"| {_recipe_label_md(recipe)} | {_fmt_count(count)} | "
            f"{_vary_pct(count, denom)} | {_vary_sites(hlls, recipe)} | "
            f"{_fmt_count(synth) if synth else '—'} |"
        )
    if len(ordered) > 25:
        lines.append(f"_… {len(ordered) - 25} more recipes not shown …_")
    lines.append("")
    return lines


def _render_marginal_md(
    entries: List[Tuple[str, int]],
    hlls: Dict[str, Any],
    denom: int,
    show_registered: bool,
    limit: int = 25,
) -> List[str]:
    if not entries:
        return ["_None observed._", ""]
    headers = ["Field-name", "Responses", "% of Vary", "Sites"]
    if show_registered:
        headers.append("Registered?")
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for token, count in entries[:limit]:
        cells = [
            _md_escape_pipe(token),
            _fmt_count(count),
            _vary_pct(count, denom),
            _vary_sites(hlls, token),
        ]
        if show_registered:
            cells.append("yes" if is_registered_field(token) else "no")
        lines.append("| " + " | ".join(cells) + " |")
    if len(entries) > limit:
        lines.append(f"_… {len(entries) - limit} more not shown …_")
    lines.append("")
    return lines


def _render_infrastructure(
    layer_counts: Dict[str, int],
    field_counts_by_layer: Dict[str, Dict[str, int]],
    field_counts: Dict[str, int],
    total_responses: int,
    truncated: bool = False,
) -> List[str]:
    if not layer_counts:
        return []
    try:
        roles = dict(default_fingerprinter().roles)
    except (OSError, ValueError):
        roles = {}
    unmatched = int(layer_counts.get(UNMATCHED, 0))
    matched = {k: int(v) for k, v in layer_counts.items() if k != UNMATCHED}
    fingerprinted = max(0, total_responses - unmatched)
    coverage = (fingerprinted / total_responses * 100) if total_responses else 0
    lines = [
        "## Infrastructure",
        "",
        "Best-effort fingerprint of the CDN / server / framework / platform "
        "behind each response, from signal headers. A response can match "
        "several layers, so these counts overlap. "
        f"Fingerprinted {coverage:.1f}% of responses; "
        f"{_fmt_count(unmatched)} matched no known layer.",
        "",
    ]
    if matched:
        lines.append("| Layer | Role | Responses | % of responses |")
        lines.append("| --- | --- | --- | --- |")
        for layer, count in sorted(matched.items(), key=lambda kv: kv[1], reverse=True):
            pct = (count / total_responses * 100) if total_responses else 0
            lines.append(
                f"| {layer} | {roles.get(layer, '')} | {_fmt_count(count)} | "
                f"{pct:.1f}% |"
            )
        lines.append("")
    if field_counts_by_layer:
        ranked = sorted(
            field_counts_by_layer.items(),
            key=lambda kv: sum(kv[1].values()),
            reverse=True,
        )
        body: List[str] = []
        for name, layers in ranked[:25]:
            if name.lower().startswith("x-crawler-"):
                continue
            total = field_counts.get(name.lower(), sum(layers.values()))
            top = sorted(layers.items(), key=lambda kv: kv[1], reverse=True)[:6]
            bits = [
                f"{layer} {count / total * 100:.0f}%" if total else f"{layer} —"
                for layer, count in top
            ]
            body.append(
                f"| {_md_escape_pipe(name)} | {_md_escape_pipe(', '.join(bits))} |"
            )
        if body:
            lines.append("### Headers by infrastructure")
            lines.append("")
            lines.append(
                "Share of each header's occurrences seen on each layer "
                "(layers overlap)."
            )
            lines.append("")
            if truncated:
                lines.append("_Long tail elided during shuffle; head only._")
                lines.append("")
            lines.append("| Header | Layers (share of occurrences) |")
            lines.append("| --- | --- |")
            lines.extend(body)
            lines.append("")
    return lines


def _render_asn(
    asn_counts: Dict[str, int], total_responses: int, truncated: bool = False
) -> List[str]:
    if not asn_counts:
        return []
    try:
        asn_to_layer = dict(default_fingerprinter().asn_to_layer)
    except (OSError, ValueError):
        asn_to_layer = {}
    ranked = sorted(asn_counts.items(), key=lambda kv: kv[1], reverse=True)[:50]
    lines = [
        "## Top networks (ASN)",
        "",
        "Autonomous System the crawl-time IP resolved to, by response count. "
        "Networks without a layer label are not yet in the fingerprint table.",
        "",
    ]
    if truncated:
        lines.append("_Long tail elided during shuffle; head only._")
        lines.append("")
    lines.append("| ASN | Layer | Responses | % of responses |")
    lines.append("| --- | --- | --- | --- |")
    for asn_str, count in ranked:
        try:
            label = asn_to_layer.get(int(asn_str), "")
        except ValueError:
            label = ""
        pct = (count / total_responses * 100) if total_responses else 0
        lines.append(f"| AS{asn_str} | {label} | {_fmt_count(count)} | {pct:.1f}% |")
    lines.append("")
    return lines


def _render_vary_section(vary: Dict[str, Any]) -> List[str]:
    if not vary:
        return []
    denom = int(vary.get("responses_with_vary", 0))
    if denom <= 0:
        return []
    recipes: Dict[str, Any] = vary.get("recipes") or {}
    marginals: Dict[str, Any] = vary.get("marginals") or {}
    marg_occ: Dict[str, int] = marginals.get("occ", {})
    marg_hlls: Dict[str, Any] = marginals.get("hlls", {})

    lines = [
        "## Vary composition",
        "",
        f"Composition of the `Vary` header across the {_fmt_count(denom)} "
        "responses that carried one. A *recipe* is the full lowercased, "
        "deduped, sorted set of field-names in a response's `Vary`. "
        "*Responses* is occurrence-weighted; *Sites* is a HyperLogLog "
        "estimate of distinct operators.",
        "",
        "### Top Vary recipes",
        "",
    ]
    if vary.get("recipes_truncated"):
        lines.append("_Long tail elided during shuffle; head only._")
        lines.append("")
    lines.extend(_render_recipe_md(recipes, denom))

    lines.append("### Top Vary recipes (Accept-Encoding factored out)")
    lines.append("")
    factored = factor_out(recipes, ACCEPT_ENCODING, AE_ONLY_LABEL)
    lines.extend(_render_recipe_md(factored, denom))

    lines.append("### High-interest axes")
    lines.append("")
    axes_entries = [(axis, marg_occ.get(axis, 0)) for axis in HIGH_INTEREST_AXES]
    lines.extend(
        _render_marginal_md(axes_entries, marg_hlls, denom, show_registered=False)
    )

    lines.append("### Vary field-names (marginals)")
    lines.append("")
    if vary.get("marginals_truncated"):
        lines.append("_Long tail elided during shuffle; head only._")
        lines.append("")
    top_marginals = sorted(marg_occ.items(), key=lambda kv: kv[1], reverse=True)
    lines.extend(
        _render_marginal_md(top_marginals, marg_hlls, denom, show_registered=True)
    )

    lines.append("### Non-standard Vary tokens")
    lines.append("")
    lines.append(
        "Tokens httplint's field registry does not recognise (≈ not "
        "IANA-registered), approximating the synthetic cache-key population. "
        "A **lower bound**: some synthetic schemes are consumed at the edge "
        "and never appear in `Vary`, and the classification is approximate."
    )
    lines.append("")
    nonstandard = sorted(
        ((t, c) for t, c in marg_occ.items() if is_nonstandard_token(t)),
        key=lambda kv: kv[1],
        reverse=True,
    )
    lines.extend(
        _render_marginal_md(nonstandard, marg_hlls, denom, show_registered=False)
    )
    return lines


def _cc_recipe_label_md(recipe: str) -> str:
    """Recipe string with non-standard directives bolded inline.

    Mirrors the HTML renderer's ``cc-synthetic`` highlighting so both views
    flag *which* directives are non-standard, not just how many.
    """
    parts = []
    for token in recipe_tokens(recipe):
        escaped = _md_escape_pipe(token)
        parts.append(f"**{escaped}**" if is_nonstandard_directive(token) else escaped)
    return ", ".join(parts)


def _render_cc_recipe_md(recipe_dict: Dict[str, Any], denom: int) -> List[str]:
    occ: Dict[str, int] = recipe_dict.get("occ", {})
    hlls: Dict[str, Any] = recipe_dict.get("hlls", {})
    if not occ:
        return ["_No data._", ""]
    ordered = sorted(occ.items(), key=lambda kv: kv[1], reverse=True)
    lines = [
        "| Recipe | Responses | % of Cache-Control | Sites | Non-standard |",
        "| --- | --- | --- | --- | --- |",
    ]
    for recipe, count in ordered[:25]:
        nonstd = cc_count_nonstandard(recipe)
        lines.append(
            f"| {_cc_recipe_label_md(recipe)} | {_fmt_count(count)} | "
            f"{_vary_pct(count, denom)} | {_vary_sites(hlls, recipe)} | "
            f"{_fmt_count(nonstd) if nonstd else '—'} |"
        )
    if len(ordered) > 25:
        lines.append(f"_… {len(ordered) - 25} more recipes not shown …_")
    lines.append("")
    return lines


def _render_cc_marginal_md(
    entries: List[Tuple[str, int]],
    hlls: Dict[str, Any],
    denom: int,
    show_standard: bool,
    limit: int = 30,
) -> List[str]:
    if not entries:
        return ["_None observed._", ""]
    headers = ["Directive", "Responses", "% of Cache-Control", "Sites"]
    if show_standard:
        headers.append("Standard?")
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for directive, count in entries[:limit]:
        cells = [
            _md_escape_pipe(directive),
            _fmt_count(count),
            _vary_pct(count, denom),
            _vary_sites(hlls, directive),
        ]
        if show_standard:
            cells.append("no" if is_nonstandard_directive(directive) else "yes")
        lines.append("| " + " | ".join(cells) + " |")
    if len(entries) > limit:
        lines.append(f"_… {len(entries) - limit} more not shown …_")
    lines.append("")
    return lines


def _render_cache_control_section(cache_control: Dict[str, Any]) -> List[str]:
    if not cache_control:
        return []
    denom = int(cache_control.get(CC_COUNT_FIELD, 0))
    if denom <= 0:
        return []
    recipes: Dict[str, Any] = cache_control.get("recipes") or {}
    marginals: Dict[str, Any] = cache_control.get("marginals") or {}
    marg_occ: Dict[str, int] = marginals.get("occ", {})
    marg_hlls: Dict[str, Any] = marginals.get("hlls", {})

    lines = [
        "## Cache-Control recipes",
        "",
        f"Composition of `Cache-Control` across the {_fmt_count(denom)} "
        "responses that carried one. A *recipe* is the normalised, deduped, "
        "sorted set of directives, with every value collapsed to `=N` (so "
        "`max-age=0` and `max-age=31536000` share one recipe). This shows the "
        "whole shape operators ship, where `CC_CONFLICTING` flags only the "
        "conflict. *Responses* is occurrence-weighted; *Sites* is a "
        "HyperLogLog estimate of distinct operators.",
        "",
        "### Top Cache-Control recipes",
        "",
    ]
    if cache_control.get("recipes_truncated"):
        lines.extend(["_Long tail elided during shuffle; head only._", ""])
    lines.extend(_render_cc_recipe_md(recipes, denom))

    lines.extend(
        [
            "### Cache-Control directives (marginals)",
            "",
            "Prevalence of each directive, regardless of value or combination, "
            "as a share of responses carrying `Cache-Control`.",
            "",
        ]
    )
    if cache_control.get("marginals_truncated"):
        lines.extend(["_Long tail elided during shuffle; head only._", ""])
    top_marginals = sorted(marg_occ.items(), key=lambda kv: kv[1], reverse=True)
    lines.extend(_render_cc_marginal_md(top_marginals, marg_hlls, denom, True))

    lines.extend(
        [
            "### Non-standard directives",
            "",
            "Directives outside httplint's RFC 9111 registry — vendor knobs, "
            "edge-CDN extensions, and typos. Approximate: a registered "
            "directive httplint lacks a parser for would also land here.",
            "",
        ]
    )
    nonstandard = sorted(
        ((d, c) for d, c in marg_occ.items() if is_nonstandard_directive(d)),
        key=lambda kv: kv[1],
        reverse=True,
    )
    lines.extend(_render_cc_marginal_md(nonstandard, marg_hlls, denom, False))
    return lines


def _cooccur_pct(count: int, denom: int) -> str:
    return f"{count / denom * 100:.2f}%" if denom else "—"


def _cooccur_sites(hlls: Dict[str, Any], key: str) -> str:
    registers = hlls.get(key)
    if isinstance(registers, list) and registers:
        est = hll_estimate(registers)
        if est > 0:
            return f"~{_fmt_count(est)}"
    return "—"


def _bundle_label_md(bundle: str) -> str:
    if bundle == EMPTY_BUNDLE_LABEL:
        return f"_{_md_escape_pipe(bundle)}_"
    return _md_escape_pipe(bundle)


def _render_cooccur_section(cooccur: Dict[str, Any]) -> List[str]:
    if not cooccur:
        return []
    denom = int(cooccur.get("responses", 0))
    if denom <= 0:
        return []
    bundle_hlls: Dict[str, Any] = (cooccur.get("bundles") or {}).get("hlls", {})
    marg_hlls: Dict[str, Any] = (cooccur.get("marginals") or {}).get("hlls", {})
    none_count = empty_bundle_count(cooccur)

    lines = [
        "## Header co-occurrence",
        "",
        "Which security / policy response headers travel together. A *bundle* "
        "is the set of those headers present on a response (from a fixed "
        f"curated alphabet); `{EMPTY_BUNDLE_LABEL}` means none were present. "
        "*Responses* is occurrence-weighted; *Sites* is a HyperLogLog estimate "
        f"of distinct operators. Shares are of all {_fmt_count(denom)} "
        "responses.",
        "",
        f"**{_cooccur_pct(none_count, denom)}** of responses "
        f"({_fmt_count(none_count)}) carried **no** security header from the "
        "tracked set.",
        "",
        "### Top header bundles",
        "",
    ]
    if cooccur.get("bundles_truncated"):
        lines.append("_Long tail elided during shuffle; head only._")
        lines.append("")
    lines.append("| Bundle | Responses | % of responses | Sites |")
    lines.append("| --- | --- | --- | --- |")
    bundles = ranked_bundles(cooccur)
    for bundle, count in bundles[:25]:
        lines.append(
            f"| {_bundle_label_md(bundle)} | {_fmt_count(count)} | "
            f"{_cooccur_pct(count, denom)} | {_cooccur_sites(bundle_hlls, bundle)} |"
        )
    if len(bundles) > 25:
        lines.append(f"_… {len(bundles) - 25} more bundles not shown …_")
    lines.append("")

    lines.append("### Conditional lifts")
    lines.append("")
    lines.append(
        "For the most common co-occurring pairs: `P(A|B)` is the share of "
        "responses carrying B that also carry A. **Lift** > 1 means the two "
        "appear together more than independent prevalence predicts."
    )
    lines.append("")
    lifts = conditional_lifts(cooccur, 25)
    if lifts:
        lines.append(
            "| Header A | Header B | Co-occurrences | P(A|B) | P(B|A) | Lift |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for row in lifts:
            lift = f"{row['lift']:.1f}×" if row["lift"] else "—"
            lines.append(
                f"| {_md_escape_pipe(str(row['a']))} | "
                f"{_md_escape_pipe(str(row['b']))} | "
                f"{_fmt_count(int(row['joint']))} | "
                f"{row['p_a_given_b'] * 100:.1f}% | "
                f"{row['p_b_given_a'] * 100:.1f}% | {lift} |"
            )
    else:
        lines.append("_No co-occurring pairs observed._")
    lines.append("")

    lines.append("### Per-header prevalence")
    lines.append("")
    if cooccur.get("marginals_truncated"):
        lines.append("_Long tail elided during shuffle; head only._")
        lines.append("")
    lines.append("| Header | Responses | % of responses | Sites |")
    lines.append("| --- | --- | --- | --- |")
    for name, count in ranked_marginals(cooccur):
        lines.append(
            f"| {_md_escape_pipe(name)} | {_fmt_count(count)} | "
            f"{_cooccur_pct(count, denom)} | {_cooccur_sites(marg_hlls, name)} |"
        )
    lines.append("")

    lines.append("### Default header set by infrastructure")
    lines.append("")
    lines.append(
        "Each fingerprint layer's **modal** bundle — the single most common "
        "security-header set among the responses attributed to it — and that "
        "bundle's share of the layer's fingerprinted responses. A high share "
        "means the platform ships that set by default. Layers overlap."
    )
    lines.append("")
    layer_defaults = layer_default_bundles(cooccur)
    if layer_defaults:
        try:
            roles = dict(default_fingerprinter().roles)
        except (OSError, ValueError):
            roles = {}
        lines.append("| Layer | Role | Responses | Default bundle | Share |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in layer_defaults[:25]:
            layer = str(row["layer"])
            lines.append(
                f"| {_md_escape_pipe(layer)} | "
                f"{_md_escape_pipe(roles.get(layer, ''))} | "
                f"{_fmt_count(int(row['responses']))} | "
                f"{_bundle_label_md(str(row['bundle']))} | "
                f"{row['share'] * 100:.1f}% |"
            )
    else:
        lines.append("_No fingerprinted responses to condition on._")
    lines.append("")
    return lines


def _render_note_cooccur_section(note_cooccur: Dict[str, Any]) -> List[str]:
    if not note_cooccur:
        return []
    denom = int(note_cooccur.get("responses", 0))
    if denom <= 0:
        return []
    bundle_hlls: Dict[str, Any] = (note_cooccur.get("bundles") or {}).get("hlls", {})
    none_count = empty_bundle_count(note_cooccur)

    lines = [
        "## Finding co-occurrence",
        "",
        "Which findings clump on the same response — testing whether defects "
        "cluster rather than scatter. A *cluster* is the set of defect notes "
        f"(`bad` / `warn`) that fired on a response; `{EMPTY_BUNDLE_LABEL}` "
        "means none did. Mechanical parent/child note pairs (a sub-finding and "
        "its parent always co-occur) are excluded from the pairs below. "
        "*Responses* is occurrence-weighted; *Sites* is a HyperLogLog estimate "
        f"of distinct operators. Shares are of all {_fmt_count(denom)} "
        "responses.",
        "",
        f"**{_cooccur_pct(none_count, denom)}** of responses "
        f"({_fmt_count(none_count)}) produced **no** `bad`/`warn` finding.",
        "",
        "### Top finding clusters",
        "",
    ]
    if note_cooccur.get("bundles_truncated"):
        lines.append("_Long tail elided during shuffle; head only._")
        lines.append("")
    lines.append("| Cluster | Responses | % of responses | Sites |")
    lines.append("| --- | --- | --- | --- |")
    bundles = ranked_bundles(note_cooccur)
    for bundle, count in bundles[:25]:
        lines.append(
            f"| {_bundle_label_md(bundle)} | {_fmt_count(count)} | "
            f"{_cooccur_pct(count, denom)} | {_cooccur_sites(bundle_hlls, bundle)} |"
        )
    if len(bundles) > 25:
        lines.append(f"_… {len(bundles) - 25} more clusters not shown …_")
    lines.append("")

    lines.append("### Conditional lifts")
    lines.append("")
    lines.append(
        "For the most common co-occurring finding pairs: `P(A|B)` is the share "
        "of responses with finding B that also carry A. **Lift** > 1 means the "
        "two fire together more than independent prevalence predicts — the "
        "clumping signal."
    )
    lines.append("")
    lifts = conditional_lifts(note_cooccur, 25)
    if lifts:
        lines.append(
            "| Finding A | Finding B | Co-occurrences | P(A|B) | P(B|A) | Lift |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for row in lifts:
            lift = f"{row['lift']:.1f}×" if row["lift"] else "—"
            lines.append(
                f"| {_md_escape_pipe(str(row['a']))} | "
                f"{_md_escape_pipe(str(row['b']))} | "
                f"{_fmt_count(int(row['joint']))} | "
                f"{row['p_a_given_b'] * 100:.1f}% | "
                f"{row['p_b_given_a'] * 100:.1f}% | {lift} |"
            )
    else:
        lines.append("_No co-occurring finding pairs observed._")
    lines.append("")
    return lines


def _transition_pct(count: int, denom: int) -> str:
    return f"{count / denom * 100:.2f}%" if denom else "—"


def _transition_sites(count: Optional[int]) -> str:
    return f"~{_fmt_count(count)}" if count else "—"


def _transition_pair_label(row: Dict[str, Any]) -> str:
    legacy = _md_escape_pipe(str(row["legacy_label"]))
    modern = _md_escape_pipe(str(row["modern_label"]))
    return f"**{legacy}** → {modern}"


def _render_transition_section(transition: Dict[str, Any]) -> List[str]:
    """Render the legacy/modern transition (transition tax) section (issue #11)."""
    if not transition:
        return []
    denom = int(transition.get("responses", 0))
    if denom <= 0:
        return []
    rows = transition_rows(transition)
    lines = [
        "## Legacy → modern transitions",
        "",
        "For a curated set of deprecated/replacement header pairs, how far the "
        "corpus has moved from the legacy header to its modern equivalent. Each "
        "response is binned per pair: *both* sides present (a site mid-migration, "
        "paying the dual-emit cost), *modern only*, *legacy only*, or neither. "
        "**Modern share** is `modern / (modern + legacy)` over the legacy+modern "
        "presence signal — a *both* response counts on each side, and responses "
        "carrying neither are excluded so they can't drown the signal. Detection "
        "reads inside `CSP` and `Cache-Control` where the modern side is a "
        f"directive, not a header. Shares are of all {_fmt_count(denom)} "
        "analysed responses.",
        "",
        "| Pair (legacy → modern) | Both | Modern only | Legacy only | Modern share | Sites both |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        pair_label = _transition_pair_label(row)
        if row["legacy_only_pair"]:
            share = "n/a (no replacement)"
        elif row["ratio"] is None:
            share = "—"
        else:
            share = f"{row['ratio'] * 100:.1f}%"
        lines.append(
            f"| {pair_label} | "
            f"{_fmt_count(row['both'])} ({_transition_pct(row['both'], denom)}) | "
            f"{_fmt_count(row['modern_only'])} | "
            f"{_fmt_count(row['legacy_only'])} | "
            f"{share} | {_transition_sites(row['both_sites'])} |"
        )
    lines.append("")
    lines.append("### Per-site view")
    lines.append("")
    lines.append(
        "Distinct operators emitting each side (the meaningful unit for "
        "adoption), with the modern share computed over sites."
    )
    lines.append("")
    lines.append(
        "| Pair (legacy → modern) | Legacy sites | Modern sites | Modern share (sites) |"
    )
    lines.append("| --- | --- | --- | --- |")
    for row in rows:
        pair_label = _transition_pair_label(row)
        if row["legacy_only_pair"]:
            site_share = "n/a (no replacement)"
        elif row["site_ratio"] is None:
            site_share = "—"
        else:
            site_share = f"{row['site_ratio'] * 100:.1f}%"
        lines.append(
            f"| {pair_label} | "
            f"{_transition_sites(row['legacy_sites'])} | "
            f"{_transition_sites(row['modern_sites'])} | "
            f"{site_share} |"
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
            f"({len(request_only)}):** " + ", ".join(f"`{n}`" for n in request_only)
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
    lines.extend(_render_category_overview(notes, category_index, category_order))
    lines.extend(
        _render_notes_section(
            notes,
            field_counts,
            severity_index,
            category_index,
            category_order,
            total_notes,
            distinct_sites_estimate,
        )
    )
    lines.extend(
        _render_field_counts(
            field_counts, total_responses, bool(data.get("truncated_field_counts"))
        )
    )
    lines.extend(
        _render_header_bytes(
            data.get("field_bytes") or {},
            data.get("header_block_hist") or {},
            int(data.get("total_header_bytes", 0)),
            total_responses,
            bool(data.get("truncated_field_bytes")),
        )
    )
    lines.extend(
        _render_infrastructure(
            data.get("layer_counts") or {},
            data.get("field_counts_by_layer") or {},
            field_counts,
            total_responses,
            bool(data.get("truncated_field_counts_by_layer")),
        )
    )
    lines.extend(
        _render_asn(
            data.get("asn_counts") or {},
            total_responses,
            bool(data.get("truncated_asn_counts")),
        )
    )
    lines.extend(_render_csp_section(data.get("csp_max_by_site") or {}))
    lines.extend(_render_value_histograms(data.get("value_histograms") or {}))
    lines.extend(_render_vary_section(data.get("vary") or {}))
    lines.extend(_render_cache_control_section(data.get("cache_control") or {}))
    lines.extend(_render_cooccur_section(data.get("cooccur") or {}))
    lines.extend(_render_note_cooccur_section(data.get("note_cooccur") or {}))
    lines.extend(_render_transition_section(data.get("transition") or {}))
    lines.extend(
        _render_unprocessed(
            unprocessed_counts, bool(data.get("truncated_unprocessed_counts"))
        )
    )
    lines.extend(_render_unseen(reachable_unseen, request_only, body_only))
    return "\n".join(lines).rstrip() + "\n"
