import json
import html
import urllib.parse
from httplint.note import Note, levels


def _get_unprocessed_ids(unprocessed_counts):
    return sorted(
        unprocessed_counts.items(), key=lambda item: item[1], reverse=True
    )[:50]


def _format_var_string(note_vars):
    if not note_vars:
        return ""
    items = [f"{k}={html.escape(str(v))}" for k, v in note_vars.items()]
    return f' <span style="color: #666; font-size: 0.9em;">({", ".join(items)})</span>'


def _generate_unsupported_section(unprocessed_counts):
    content = []
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


def _generate_variable_stats(note_data, field_counts):
    content = []
    var_stats = note_data.get("vars", {})
    if not var_stats:
        return content

    for var_name, counts in var_stats.items():
        content.append(f"            <h3>Variable: {html.escape(var_name)}</h3>")

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


def _generate_sample_rows(note_data, var_name, val, is_field_name):
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


def _generate_notes_section(notes, field_counts):
    content = ['<div id="notes">']
    sorted_notes = sorted(
        notes.items(),
        key=lambda item: item[1]["count"] if isinstance(item[1], dict) else item[1],
        reverse=True,
    )

    for note_id, note_data in sorted_notes:
        if isinstance(note_data, int):
            count, samples = note_data, []
            note_data = {}  # Empty dict to prevent errors if used later
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


def _get_all_subclasses(cls):
    return set(cls.__subclasses__()).union(
        [s for c in cls.__subclasses__() for s in _get_all_subclasses(c)]
    )


def _generate_missing_notes_section(seen_notes_keys):
    content = []
    all_notes = _get_all_subclasses(Note)
    possible_notes = set()
    for note_cls in all_notes:
        if getattr(note_cls, "level", None) in [levels.WARN, levels.BAD]:
            possible_notes.add(note_cls.__name__)

    missing_notes = sorted(list(possible_notes - set(seen_notes_keys)))

    if missing_notes:
        content.append('<div id="missing_notes">')
        content.append("        <h2>Unseen Notes</h2>")
        content.append(
            "        <p>The following notes were not generated by any response:</p>"
        )
        content.append("        <ul>")
        for note_name in missing_notes:
            content.append(f"            <li>{html.escape(note_name)}</li>")
        content.append("        </ul>")
        content.append("</div>")
    return content


def generate_report(stats_file, output_file):
    """
    Generates an HTML report from stats.json.
    """
    with open(stats_file, "r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)

    total_responses = data.get("total_responses", 0)
    notes = data.get("notes", data.get("note_counts", {}))
    field_counts = data.get("field_counts", {})

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
    ]

    html_content.extend(_generate_unsupported_section(data.get("unprocessed_counts", {})))
    html_content.extend(_generate_notes_section(notes, field_counts))
    html_content.extend(_generate_missing_notes_section(notes.keys()))

    html_content.append("</body>")
    html_content.append("</html>")

    with open(output_file, "w", encoding="utf-8") as file_handle:
        file_handle.write("\n".join(html_content))
