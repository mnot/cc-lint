import json
import html
import urllib.parse
from typing import Dict, List, Any, Set
from httplint.note import Note, levels
from cc_lint.types import NoteDataType


def _get_unprocessed_ids(unprocessed_counts: Dict[str, int]) -> List[tuple[str, int]]:
    return sorted(
        unprocessed_counts.items(), key=lambda item: item[1], reverse=True
    )[:50]


def _format_var_string(note_vars: Dict[str, Any]) -> str:
    if not note_vars:
        return ""
    items = [f"{k}={html.escape(str(v))}" for k, v in note_vars.items()]
    return f' <span style="color: #666; font-size: 0.9em;">({", ".join(items)})</span>'


def _generate_unsupported_section(unprocessed_counts: Dict[str, int]) -> List[str]:
    content: List[str] = []
    if unprocessed_counts:
        content.append('<div id="unprocessed">')
        content.append("    <h2>Top 50 Unsupported Headers</h2>")
        content.append(
            '    <table border="1" cellpadding="5" '
            'style="border-collapse: collapse; margin-bottom: 20px;">'
        )
        content.append("        <tr><th>Header Name</th><th>Count</th></tr>")

        for name, count in _get_unprocessed_ids(unprocessed_counts):
            content.append(
                f"        <tr><td>{html.escape(name)}</td><td>{count:,}</td></tr>"
            )

        content.append("    </table>")
        content.append("</div>")
    return content


def _generate_field_error_table(
    counts: Dict[str, int], note_data: NoteDataType
) -> List[str]:
    """
    Generate a hierarchical table for field_error (field -> errors).
    """
    content: List[str] = []

    # Parse and group the data
    grouped: Dict[str, List[tuple[str, int]]] = {}
    for key, count in counts.items():
        if ": " in key:
            field, error = key.split(": ", 1)
        else:
            field, error = key, ""

        if field not in grouped:
            grouped[field] = []
        grouped[field].append((error, count))

    # Sort fields by total count
    field_totals = {f: sum(c for _, c in errs) for f, errs in grouped.items()}
    sorted_fields = sorted(field_totals.items(), key=lambda item: item[1], reverse=True)

    content.append(
        '            <table border="1" cellpadding="5" '
        'style="border-collapse: collapse; margin-bottom: 10px; width: 100%;">'
    )
    content.append("                <tr><th>Field Name</th><th>Errors</th></tr>")

    for field, total in sorted_fields[:50]:
        errors = grouped[field]
        # Sort errors by count
        errors.sort(key=lambda item: item[1], reverse=True)

        # Format errors list
        error_items = []
        for err, count in errors:
            err_html = f"{html.escape(err)} ({count:,})"
            # Add samples if available
            # Note: samples are stored by the full key "field: error"
            full_key = f"{field}: {err}"
            var_samples = note_data.get("var_samples", {}).get("field_error", {})

            samples_html = ""
            if full_key in var_samples:
                sample_items = []
                for sample in var_samples[full_key]:
                    if isinstance(sample, dict) and "url" in sample:
                        url = sample["url"]
                        encoded_uri = urllib.parse.quote(url)
                        link = html.escape(f"https://redbot.org/check?uri={encoded_uri}")
                        sample_items.append(
                            f'<li><a href="{link}" target="_blank" '
                            f'style="color: #888; text-decoration: none;">'
                            f'{html.escape(url)}</a></li>'
                        )
                if sample_items:
                    samples_html = (
                        "<ul style='margin: 3px 0 3px 20px; font-size: 0.85em; "
                        f"list-style-type: circle;'>{''.join(sample_items)}</ul>"
                    )

            error_items.append(f"<li>{err_html}{samples_html}</li>")

        errors_html = f"<ul style='margin: 0; padding-left: 20px;'>{''.join(error_items)}</ul>"

        content.append(
            f"                <tr><td valign='top'><strong>{html.escape(field)}</strong><br>"
            f"<span style='font-size: 0.8em; color: #666;'>Total: {total:,}</span></td>"
            f"<td>{errors_html}</td></tr>"
        )

    if len(sorted_fields) > 50:
        content.append(
             f'                <tr><td colspan="2" style="font-style: italic;">... '
             f'{len(sorted_fields) - 50} more fields ...</td></tr>'
        )

    content.append("            </table>")
    return content


def _generate_variable_stats(
    note_data: NoteDataType, field_counts: Dict[str, int]
) -> List[str]:
    content: List[str] = []
    var_stats = note_data.get("vars", {})
    if not var_stats:
        return content

    for var_name, counts in var_stats.items():
        content.append(f"            <h3>Variable: {html.escape(var_name)}</h3>")

        if var_name == "field_error":
            content.extend(_generate_field_error_table(counts, note_data))
            continue

        is_field_name = var_name == "field_name"
        headers = "<tr><th>Value</th><th>Count</th>"
        if is_field_name:
            headers += "<th>Total Occurrences</th><th>% of Occurrences</th>"
        headers += "</tr>"

        content.append(
            '            <table border="1" cellpadding="5" '
            'style="border-collapse: collapse; margin-bottom: 10px;">'
        )
        content.append(f"                {headers}")

        sorted_vals = sorted(
            counts.items(), key=lambda item: item[1], reverse=True
        )

        for val, val_count in sorted_vals[:25]:
            row = f"                <tr><td>{html.escape(val)}</td><td>{val_count:,}</td>"
            if is_field_name:
                total = field_counts.get(val.lower(), 0)
                if total > 0:
                    pct = (val_count / total) * 100
                    row += f"<td>{total:,}</td><td>{pct:.2f}%</td>"
                else:
                    row += f"<td>{total}</td><td>-</td>"
            row += "</tr>"
            content.append(row)
            content.extend(_generate_sample_rows(note_data, var_name, val, is_field_name))

        if len(sorted_vals) > 25:
            colspan = "4" if is_field_name else "2"
            content.append(
                f'                <tr><td colspan="{colspan}" style="font-style: italic;">... '
                f'{len(sorted_vals) - 25} more ...</td></tr>'
            )
        content.append("            </table>")
    return content


def _generate_sample_rows(
    note_data: NoteDataType, var_name: str, val: str, is_field_name: bool
) -> List[str]:
    content = []
    var_samples = note_data.get("var_samples", {}).get(var_name, {})
    if val in var_samples:
        colspan = "4" if is_field_name else "2"
        content.append("                <tr>")
        content.append(
            f'                    <td colspan="{colspan}" '
            'style="padding-left: 20px; background-color: #f9f9f9;">'
        )
        content.append("                        <strong>Samples:</strong>")
        content.append('                        <ul style="margin: 5px 0;">')

        for sample in var_samples[val]:
            if isinstance(sample, dict) and "url" in sample:
                url = sample["url"]
                note_vars = sample.get("vars", {})
                var_str = _format_var_string(note_vars)  # Reusing formatting logic if suitable
                # Actually, simpler formatting for list items
                var_items = [f"{k}={html.escape(str(v))}" for k, v in note_vars.items()]
                var_str = (
                    f' <span style="color: #666; font-size: 0.8em;">({", ".join(var_items)})</span>'
                    if var_items else ""
                )

                encoded_uri = urllib.parse.quote(url)
                link = html.escape(f"https://redbot.org/check?uri={encoded_uri}")
                content.append(
                    f'                            <li><a href="{link}" target="_blank">'
                    f'{html.escape(url)}</a>{var_str}</li>'
                )
        content.append("                        </ul>")
        content.append("                    </td>")
        content.append("                </tr>")
    return content


def _generate_notes_section(notes: Dict[str, Any], field_counts: Dict[str, int]) -> List[str]:
    content = ['<div id="notes">']
    sorted_notes = sorted(
        notes.items(),
        key=lambda item: item[1]["count"] if isinstance(item[1], dict) else item[1],
        reverse=True,
    )

    for note_id, note_data in sorted_notes:
        if isinstance(note_data, int):
            count, samples = note_data, []
            note_data = {}
        else:
            count = note_data.get("count", 0)
            samples = note_data.get("samples", [])

        content.append('<div class="note">')
        content.append(f"            <h2>{html.escape(note_id)}</h2>")
        content.append(f'            <p class="count">Count: {count:,}</p>')

        if samples:
            content.append("            <ul>")
            for sample_entry in samples:
                if isinstance(sample_entry, dict) and "url" in sample_entry:
                    url = sample_entry["url"]
                    note_vars = sample_entry.get("vars", {})
                    var_str = _format_var_string(note_vars)
                else:
                    url = sample_entry
                    var_str = ""

                encoded_uri = urllib.parse.quote(url)
                link = html.escape(f"https://redbot.org/check?uri={encoded_uri}")
                content.append(
                    f'                <li><a href="{link}" target="_blank">'
                    f'{html.escape(url)}</a>{var_str}</li>'
                )
            content.append("            </ul>")

        content.extend(_generate_variable_stats(note_data, field_counts))
        content.append("        </div>")

    content.append("</div>")
    return content


def _get_all_subclasses(cls: Any) -> Set[Any]:
    return set(cls.__subclasses__()).union(
        [s for c in cls.__subclasses__() for s in _get_all_subclasses(c)]
    )


def _get_possible_notes() -> Set[str]:
    all_notes = _get_all_subclasses(Note)
    possible_notes = set()
    for note_cls in all_notes:
        if getattr(note_cls, "level", None) in [levels.WARN, levels.BAD]:
            possible_notes.add(note_cls.__name__)

    return possible_notes


def _generate_missing_notes_section(missing_notes: List[str]) -> List[str]:
    content = []
    if missing_notes:
        content.append('<div id="missing_notes">')
        content.append(f"        <h2>Unseen Notes ({len(missing_notes)})</h2>")
        content.append(
            "        <p>The following notes were not generated by any response:</p>"
        )
        content.append("        <ul>")
        for note_name in missing_notes:
            content.append(f"            <li>{html.escape(note_name)}</li>")
        content.append("        </ul>")
        content.append("</div>")
    return content


def generate_report(stats_file: str, output_file: str) -> None:
    """
    Generates an HTML report from stats.json.
    """
    with open(stats_file, "r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)

    total_responses = data.get("total_responses", 0)
    notes = data.get("notes", data.get("note_counts", {}))
    field_counts = data.get("field_counts", {})

    # Calculate total notes
    total_notes = 0
    for note_data in notes.values():
        if isinstance(note_data, int):
            total_notes += note_data
        else:
            total_notes += note_data.get("count", 0)

    # Calculate note stats
    possible_notes = _get_possible_notes()
    seen_notes = set(notes.keys())
    missing_notes = sorted(list(possible_notes - seen_notes))

    html_content = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '    <meta charset="UTF-8">',
        "    <title>CC Lint Report</title>",
        "    <style>",
        "        body { font-family: sans-serif; max-width: 800px; margin: 0 auto; ",
        "               padding: 20px; }",
        "        .note { border-bottom: 1px solid #ccc; padding: 10px 0; }",
        "        .count { font-weight: bold; }",
        "        h2 { margin-bottom: 5px; }",
        "    </style>",
        "</head>",
        "<body>",
        "    <h1>Common Crawl Lint Statistics</h1>",
        f"    <p>Total Responses Analyzed: {total_responses:,}</p>",
        f"    <p>Total Notes Generated: {total_notes:,}</p>",
        f"    <p>Total Note Types: {len(possible_notes)}</p>",
        f"    <p>Seen Note Types: {len(seen_notes)}</p>",
        f"    <p>Unseen Note Types: {len(missing_notes)}</p>",
    ]

    html_content.extend(_generate_unsupported_section(data.get("unprocessed_counts", {})))
    html_content.extend(_generate_notes_section(notes, field_counts))
    html_content.extend(_generate_missing_notes_section(missing_notes))

    html_content.append("</body>")
    html_content.append("</html>")

    with open(output_file, "w", encoding="utf-8") as file_handle:
        file_handle.write("\n".join(html_content))
