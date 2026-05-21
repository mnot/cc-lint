"""HTML + Markdown report renderer for cc-lint stats.

Each call writes both an HTML report and a Markdown sibling alongside it.
Single entry point: :func:`render_report` takes the in-memory stats dict
that ``StatsCollector.to_dict()`` produces (and that ``cc_lint.emr.finalize``
assembles from sharded part-* records).
"""

from cc_lint.report.render import default_markdown_path, render_report

__all__ = ["default_markdown_path", "render_report"]
