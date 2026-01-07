import json
import html
import urllib.parse

def generate_report(stats_file, output_file):
    """
    Generates an HTML report from stats.json.
    """
    with open(stats_file, 'r') as f:
        data = json.load(f)
        
    total_responses = data.get('total_responses', 0)
    # Support both 'notes' and legacy 'note_counts' (if any remain, though current stats.py uses 'notes')
    notes = data.get('notes', data.get('note_counts', {}))
    
    html_content = [
        "<!DOCTYPE html>",
        "<html lang=\"en\">",
        "<head>",
        "    <meta charset=\"UTF-8\">",
        "    <title>CC Lint Report</title>",
        "    <style>",
        "        body { font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }",
        "        .note { border-bottom: 1px solid #ccc; padding: 10px 0; }",
        "        .count { font-weight: bold; }",
        "        h2 { margin-bottom: 5px; }",
        "    </style>",
        "</head>",
        "<body>",
        "    <h1>Common Crawl Lint Statistics</h1>",
        f"    <p>Total Responses Analyzed: {total_responses:,}</p>"
    ]

    # Unsupported Headers Section
    unprocessed_counts = data.get('unprocessed_counts', {})
    if unprocessed_counts:
        html_content.append("    <div id=\"unprocessed\">")
        html_content.append("        <h2>Top 50 Unsupported Headers</h2>")
        html_content.append("        <table border=\"1\" cellpadding=\"5\" style=\"border-collapse: collapse; margin-bottom: 20px;\">")
        html_content.append("            <tr><th>Header Name</th><th>Count</th></tr>")
        
        # Sort by count descending and take top 50
        sorted_unprocessed = sorted(unprocessed_counts.items(), key=lambda item: item[1], reverse=True)[:50]
        
        for name, count in sorted_unprocessed:
             html_content.append(f"            <tr><td>{html.escape(name)}</td><td>{count:,}</td></tr>")
             
        html_content.append("        </table>")
        html_content.append("    </div>")

    html_content.append("    <div id=\"notes\">")
    
    # Sort notes by count (descending)
    sorted_notes = sorted(notes.items(), key=lambda item: item[1]['count'] if isinstance(item[1], dict) else item[1], reverse=True)
    
    for note_id, note_data in sorted_notes:
        # Normalize data (handle legacy int counts if stats.json mixed)
        if isinstance(note_data, int):
             count = note_data
             samples = []
        else:
             count = note_data.get('count', 0)
             samples = note_data.get('samples', [])
             
        html_content.append("        <div class=\"note\">")
        html_content.append(f"            <h2>{html.escape(note_id)}</h2>")
        html_content.append(f"            <p class=\"count\">Count: {count:,}</p>")
        
        if samples:
            html_content.append("            <ul>")
            for sample_entry in samples:
                # Handle both old (string) and new (dict) format during transition if needed
                if isinstance(sample_entry, dict) and 'url' in sample_entry:
                    sample_url = sample_entry['url']
                    note_vars = sample_entry.get('vars', {})
                    
                    # Format vars
                    var_str = ""
                    if note_vars:
                         var_str = f" <span style=\"color: #666; font-size: 0.9em;\">({', '.join(f'{k}={html.escape(str(v))}' for k, v in note_vars.items())})</span>"
                else:
                    sample_url = sample_entry
                    var_str = ""

                safe_sample_url = html.escape(sample_url)
                # URL encode the URI for the query parameter
                encoded_uri = urllib.parse.quote(sample_url)
                # Escape the full URL for HTML attribute safety
                redbot_link = html.escape(f"https://redbot.org/check?uri={encoded_uri}")
                html_content.append(f"                <li><a href=\"{redbot_link}\" target=\"_blank\">{safe_sample_url}</a>{var_str}</li>")
            html_content.append("            </ul>")
            
        # Display variable stats if available
        var_stats = note_data.get('vars', {})
        if var_stats:
            for var_name, counts in var_stats.items():
                html_content.append(f"            <h3>Variable: {html.escape(var_name)}</h3>")
                
                # Check if this is 'field_name' and we have field stats
                is_field_name = (var_name == 'field_name')
                field_counts = data.get('field_counts', {})
                
                if is_field_name:
                    html_content.append("            <table border=\"1\" cellpadding=\"5\" style=\"border-collapse: collapse; margin-bottom: 10px;\">")
                    html_content.append("                <tr><th>Value</th><th>Count</th><th>Total Occurrences</th><th>% of Occurrences</th></tr>")
                else:
                    html_content.append("            <table border=\"1\" cellpadding=\"5\" style=\"border-collapse: collapse; margin-bottom: 10px;\">")
                    html_content.append("                <tr><th>Value</th><th>Count</th></tr>")

                # Sort by count descending
                sorted_vals = sorted(counts.items(), key=lambda item: item[1], reverse=True)
                # Limit to top 25
                for val, val_count in sorted_vals[:25]:
                    row_html = f"                <tr><td>{html.escape(val)}</td><td>{val_count:,}</td>"
                    if is_field_name:
                        # Total occurrences of this field in the crawl (case-insensitive)
                        total_field_count = field_counts.get(val.lower(), 0)
                        if total_field_count > 0:
                            pct = (val_count / total_field_count) * 100
                            row_html += f"<td>{total_field_count:,}</td><td>{pct:.2f}%</td>"
                        else:
                            row_html += f"<td>{total_field_count}</td><td>-</td>"
                    
                    row_html += "</tr>"
                    html_content.append(row_html)
                    
                    # Check for detailed samples for this value
                    var_samples = note_data.get('var_samples', {}).get(var_name, {})
                    if val in var_samples:
                        html_content.append("                <tr>")
                        html_content.append(f"                    <td colspan=\"{4 if is_field_name else 2}\" style=\"padding-left: 20px; background-color: #f9f9f9;\">")
                        html_content.append("                        <strong>Samples:</strong>")
                        html_content.append("                        <ul style=\"margin: 5px 0;\">")
                        for s in var_samples[val]:
                             if isinstance(s, dict) and 'url' in s:
                                 url = s['url']
                                 note_vars = s.get('vars', {})
                                 # Try to find a 'value' var to verify syntax
                                 extra = ""
                                 if 'value' in note_vars:
                                     extra = f" (value: {html.escape(note_vars['value'])})"
                                 # Or just print all vars except trivial ones
                                 var_str = f" <span style=\"color: #666; font-size: 0.8em;\">({', '.join(f'{k}={html.escape(str(v))}' for k, v in note_vars.items())})</span>"
                                 
                                 encoded_uri = urllib.parse.quote(url)
                                 redbot_link = html.escape(f"https://redbot.org/check?uri={encoded_uri}")
                                 html_content.append(f"                            <li><a href=\"{redbot_link}\" target=\"_blank\">{html.escape(url)}</a>{var_str}</li>")
                        html_content.append("                        </ul>")
                        html_content.append("                    </td>")
                        html_content.append("                </tr>")
                    
                if len(sorted_vals) > 25:
                    colspan = "4" if is_field_name else "2"
                    html_content.append(f"                <tr><td colspan=\"{colspan}\" style=\"font-style: italic;\">... {len(sorted_vals) - 25} more ...</td></tr>")
                html_content.append("            </table>")

        html_content.append("        </div>")
        
    html_content.append("    </div>")

    # Missing Notes Section
    # Find all Notes
    import httplint
    from httplint.note import Note, levels
    
    def get_all_subclasses(cls):
        return set(cls.__subclasses__()).union(
            [s for c in cls.__subclasses__() for s in get_all_subclasses(c)])

    all_notes = get_all_subclasses(Note)
    possible_notes = set()
    for n in all_notes:
        if getattr(n, 'level', None) in [levels.WARN, levels.BAD]:
            possible_notes.add(n.__name__)
            
    seen_notes = set(notes.keys())
    missing_notes = sorted(list(possible_notes - seen_notes))
    
    if missing_notes:
        html_content.append("    <div id=\"missing_notes\">")
        html_content.append("        <h2>Unseen Notes</h2>")
        html_content.append("        <p>The following notes were not generated by any response:</p>")
        html_content.append("        <ul>")
        for note_name in missing_notes:
            html_content.append(f"            <li>{html.escape(note_name)}</li>")
        html_content.append("        </ul>")
        html_content.append("    </div>")

    html_content.append("</body>")
    html_content.append("</html>")
    
    with open(output_file, 'w') as f:
        f.write("\n".join(html_content))
