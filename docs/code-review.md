# cc-lint code review

Read-only audit, no behaviour changes. Findings are grouped by category and
prioritised within each. Severity legend: **H**igh = fix before next run, **M**edium
= fix in a follow-up, **L**ow = nice to have. Effort: **S**mall = single file
edit, **M**edium = multi-file refactor, **L**arge = cross-cutting change.

Audit scope: every file under `cc_lint/` plus the EMR Make targets and the
`tests/` directory. Items below are the **outstanding** findings; everything
from the original A/B/C/D/G1/G4 batches has been folded in via subsequent
commits.

## D. Shared helper opportunities (remaining)

**D3 (L / M). `cc_lint/types.py` is 15 lines.** With the new sharded keys and
run_context, more typed dicts could live here (`GlobalsPayload`,
`RunContext`, `WireRecord`). At the current scale not urgent, but if any
follow-up adds more wire shapes, do it then.

## E. Security smells

**E1 (M / Doc). EMR wheel bucket access control is a single point of trust.**
`Makefile`'s bootstrap step does `aws s3 sync $(WHEEL_S3_PATH) /tmp/wheels/`
then installs every wheel found there with `sudo pip --no-index`. Anyone who
can write to `$(WHEEL_S3_PATH)` can run code as root on every mapper. Document
the bucket-policy expectations in README.md and `cc-lint.example.mk` (the
wheel bucket should be operator-write, EMR-read, no public access).
*Partially addressed*: cc-lint.example.mk now has a note about the wheel
bucket access policy; README documentation still pending.

**E2 (M / S). `record.content_stream().read()` has no size cap.** A
pathological WAT record could be very large. We're protected by:
the warc-timeout (kills the child if reading takes too long), the
fork-isolated child (an OOM kills only the child), and the Hadoop
mapper memory cap. Defence in depth would be to cap the read at e.g.
8 MB; anything larger is malformed enough that we can drop the record
and increment a counter.

**E3 (L / Doc). Report HTML XSS surface.** Every user-supplied string passes
through `html.escape` and URLs additionally through `urllib.parse.quote`. The
test suite verifies this (test_url_escaping). Worth keeping the test as a
regression guard; consider adding one for HTML in note id keys (which
shouldn't happen but the renderer doesn't enforce it).

## F. Drift from cc-feeds conventions

**F1 (L / M). cc-feeds uses Jinja2 templates; cc-lint uses Python f-strings.**
Fine at current scale (~870-line `report/` subpackage). Worth revisiting if
the report grows. The Jinja2 path also lets non-engineers iterate on the HTML
without touching Python.

**F2 (L / S). cc-feeds has `feed_survey/analysis/` grouping**; cc-lint keeps
`stats.py` and `hll.py` flat. Two-file grouping is borderline; defer until a
third analysis module shows up.

**F3 (L / S). cc-feeds tests use `tests/conftest.py` for shared fixtures.**
cc-lint duplicates the `_linter_for` / `_attach_note` helper in
`test_stats.py` only. Not duplication today; will be if we add more
linter-driving test files.

## G. Other notes (remaining)

**G2 (L / S). `cc_lint/emr/job.py::CCLintJob` is 350+ lines.** Coherent but
substantial. Most of the bulk is `mapper`, `_process_warc_in_child`, and
`mapper_init` doing real work. Not worth splitting until something else
forces it.

**G3 (L / S). `tests/test_emr_warc_worker.py` only round-trips an empty
result.** It doesn't drive `process_warc_to_file` end-to-end with a mocked
`iter_wat_records`. The mocking complexity (httplint instantiation + WAT
record fakery) was deferred. Add when there's a regression that motivates
it; for now the integration coverage is implicit via the smoke tests we run
through `cc_lint.emr.finalize`.

## Resolved (kept here for the audit trail; see git log for the actual fixes)

- A1 — un-underscored cross-module merge helpers in `cc_lint.emr.job`.
- A2 — public/private intent in `report/sections.py` already documented
  via the module docstring after the report refactor.
- A3 — renamed `_truncated_*` wire keys to drop the underscore.
- A4 — bumped `__version__` to 0.1.0.
- B1 — narrowed broad `except` clauses in linting, top_sites, cli.
- B2 — removed the import-time DEBUG stderr write from `cc_lint/emr/job.py`.
- B3 — WAT JSON parse failures now log a warning with the exception type.
- C1 — dropped always-true `hasattr` guards in `cc_lint/stats.py`.
- C2 — dropped `note_counts` legacy fallback in `cc_lint/report/render.py`.
- C3 — dropped int fallback in `_render_note_card` and `count_total_notes`.
- C4 — dropped redundant `or 0` in `_build_run_context`.
- C5 — slimmed `cc-lint.example.mk` to override-only.
- D1 — consolidated WARC→WAT path rewrite into `cc_lint/cc_paths.py`.
- D2 — extracted `_as_latin1` helper in `cc_lint/linting.py`.
- G1 — split `lint_cc` into `_load_paths`, `_load_top_sites_set`,
  `_run_lint_loop`.
- G4 — added `cc_lint/__main__.py` for `python -m cc_lint`.
