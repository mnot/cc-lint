"""HTML + Markdown report renderer for cc-lint stats.

Each call writes both an HTML report and a Markdown sibling alongside it.
Two entry points:

- :func:`generate_report`: file-based; reads stats from a JSON path.
- :func:`render_report`: takes the stats dict in memory.
"""

from cc_lint.report.render import default_markdown_path, generate_report, render_report

__all__ = ["default_markdown_path", "generate_report", "render_report"]
