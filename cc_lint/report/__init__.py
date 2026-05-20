"""HTML report renderer for cc-lint stats.json output.

This subpackage replaces the single-file cc_lint/report.py. The public
surface is unchanged: import ``generate_report`` from ``cc_lint.report``.
"""

from cc_lint.report.render import generate_report

__all__ = ["generate_report"]
