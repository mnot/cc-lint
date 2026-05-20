# cc-lint code review

Read-only audit, no behaviour changes. Findings are grouped by category and
prioritised within each. Severity legend: **H**igh = fix before next run, **M**edium
= fix in a follow-up, **L**ow = nice to have. Effort: **S**mall = single file
edit, **M**edium = multi-file refactor, **L**arge = cross-cutting change.

Audit scope: every file under `cc_lint/` plus the EMR Make targets and the
`tests/` directory. 64 passing tests, strict mypy, pylint 10.00/10 at the
time of writing.

## A. API surface and naming

**A1 (M / S). Inconsistent public/private boundaries in `cc_lint.emr.job`.**
The module exports a mix: `merge_stats_dict` and `trim_stats_dict` are public,
but `_merge_globals`, `_merge_note`, `_failure_bucket`, `_sample_key`,
`_trim_globals`, `_trim_note`, and the constants `GLOBALS_KEY`/`NOTE_KEY_PREFIX`
all carry a leading underscore. Yet `cc_lint.emr.finalize` imports the
underscored ones directly. Either declare them public (drop the underscore on
`_merge_globals`, `_merge_note`) or move the shared merge helpers into a
private `cc_lint/emr/_merge.py` that finalize and job both consume. Current
state lies about the contract.

**A2 (L / S). Same issue in `cc_lint.report.sections`.** Most renderers are
`_render_*` (private), but `render_run_context`, `render_header_stats`,
`render_notes_section`, `render_field_counts_section`,
`render_unprocessed_section`, `render_missing_section`, and `count_total_notes`
are public for the orchestrator. The split is fine; just be deliberate. Either
treat the whole module as internal (everything underscored, the only public
import comes via `__init__.py`) or accept the public/private mix and document
it at the top of `sections.py`.

**A3 (L / S). `_truncated_*` keys leak the underscore convention into the wire
format.** The JSON shipped through mrjob shuffle and persisted in `stats.json`
contains keys like `_truncated_field_counts: true`. Underscore prefix is a
Python convention, not a JSON convention. Either rename to `truncated_*`
without the underscore, or move all such meta-fields under a nested
`meta: {truncated_field_counts: true, ...}` so the convention is "this is
metadata about the data" rather than "this is private."

**A4 (L / S). `__version__ = "0.0.1"` has never been bumped.** The Makefile
includes `bump-semver` / `bump-calver` machinery via `Makefile.pyproject`; nothing
has triggered it. Cut a real release tag at the next milestone so the version
string in the run-context pill is meaningful.

## B. Error handling consistency

**B1 (M / S). Broad `except Exception` in linting and top_sites.** Eight call
sites swallow exceptions with `# pylint: disable=broad-except` and either
return None or log a one-line warning. Most are defensible (one bad record
shouldn't break a whole WAT) but the pattern is undisciplined. Audit each:
- `cc_lint/linting.py:28` (`_lint_wat_record` JSON parse) — narrow to
  `json.JSONDecodeError, AttributeError`.
- `cc_lint/linting.py:53,122` (`dateutil.parser.parse`) — narrow to
  `ValueError, TypeError, dateutil.parser.ParserError`.
- `cc_lint/top_sites.py:59` (`load_top_sites` CSV read) — narrow to
  `OSError, ValueError`.
- `cc_lint/top_sites.py:87` (`normalize_site` `urlparse`) — `urlparse` doesn't
  raise on bad input, only ValueError on extreme IPv6 cases. Narrow.
- `cc_lint/cli.py:119,122,134` — fine to keep broad since the CLI must not
  abort on a bad WARC, but log the exception type explicitly.

**B2 (M / S). `cc_lint/emr/job.py:64-65` leaks a debug print to stderr at
import.** `sys.stderr.write("DEBUG: Python interpreter started successfully\n")`
runs unconditionally when the module is imported, including in unit tests. The
intent is EMR-cluster diagnostics — restrict to `if __name__ == "__main__"`
guarded code in `main()`, or wrap behind an env var.

**B3 (L / S). Silent JSON parse failures in `_lint_wat_record`.** When the WAT
envelope is malformed, the function returns `None` with no counter increment.
For diagnostics it's useful to know how many records failed to parse vs how
many were just not response records. Bump an `_ERROR_LINTING` style counter
on the parse failure path.

## C. Dead code and stale fallbacks

**C1 (L / S). `cc_lint/stats.py:71` `hasattr(note, "vars")` is always true.**
`Note.__init__` always sets `self.vars`. The `hasattr` guard is dead. Same
pattern in two other places in `stats.py` (search for `hasattr(note, "vars")`
and `hasattr(linter, "headers")` — `linter.headers` is also always set).

**C2 (L / S). `cc_lint/report/render.py:30` `data.get("notes", data.get("note_counts", {}))`.**
`note_counts` is a legacy key from before the EMR rewrite; the current
pipeline never emits it. Drop the fallback.

**C3 (L / S). `cc_lint/report/sections.py:355` plain-int note data.** `_render_note_card`
handles `isinstance(note_data, dict) else int(note_data)` for backward compat.
The current emit shape always passes a dict. Drop the int fallback.

**C4 (L / S). `cc_lint/emr/job.py::_build_run_context` `getattr(..., 0) or 0`.**
The trailing `or 0` is redundant when the default is already `0`. Simplify
to `int(getattr(options, "...", 0))`.

**C5 (L / S). `cc-lint.example.mk` is ~85% duplicate of `cc-lint.defaults.mk`.**
The Makefile already does `include cc-lint.defaults.mk` then `-include
$(CONFIG)`. The example file only needs to override the YOUR-BUCKET S3 paths
(plus any per-environment values the operator wants pinned). Trim it.

## D. Shared helper opportunities

**D1 (M / S). Duplicate WARC → WAT path rewrite.** `cc_lint/crawling.py::_warc_to_wat`
and `cc_lint/emr/warc_source.py::warc_path_to_wat` do the same thing. Move
to one of them (probably `cc_lint/top_sites.py` doesn't fit; consider a new
`cc_lint/cc_paths.py` or fold into `crawling.py` and import from
`warc_source`).

**D2 (L / S). `encode("latin1", errors="replace")` repeated four times in
`cc_lint/linting.py`.** Tiny helper `_as_latin1(value: Any) -> bytes` would
DRY it and document the encoding intent. Cosmetic.

**D3 (L / M). `cc_lint/types.py` is 15 lines.** With the new sharded keys and
run_context, more typed dicts could live here (`GlobalsPayload`,
`RunContext`, `WireRecord`). At the current scale not urgent, but if any
follow-up adds more wire shapes, do it then.

## E. Security smells

**E1 (M / Doc).  EMR wheel bucket access control is a single point of trust.**
`Makefile`'s bootstrap step does `aws s3 sync $(WHEEL_S3_PATH) /tmp/wheels/`
then installs every wheel found there with `sudo pip --no-index`. Anyone who
can write to `$(WHEEL_S3_PATH)` can run code as root on every mapper. Document
the bucket-policy expectations in README.md and `cc-lint.example.mk` (the
wheel bucket should be operator-write, EMR-read, no public access).

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

## G. Other notes

**G1 (M / S). `cc_lint/cli.py::lint_cc` has 4 responsibilities.** Read paths,
optionally load Tranco, loop over WARCs, write JSON. Each block is small; a
straightforward extract would yield `_load_paths`, `_load_top_sites_set`,
`_run_lint_loop`, and a thin orchestrator. Improves readability and gives
the per-WARC handling a unit-testable entry point.

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

**G4 (L / S). No `__main__.py`.** Running `python -m cc_lint` won't work
today; the entry point is the `cc-lint` console script from `pyproject.toml`
or `python -m cc_lint.cli`. Minor convenience: add `cc_lint/__main__.py`
that imports and dispatches to `cli.cli()`.

## H. Suggested follow-up order

If the goal is one focused cleanup commit set, suggested ordering:

1. **B2** (remove the debug `sys.stderr.write` at module import)
2. **C1**, **C2**, **C3**, **C4** (dead branches, single-file edits each)
3. **A1** (un-underscore `_merge_globals` / `_merge_note` and friends)
4. **D1** (consolidate WARC → WAT path rewrite)
5. **C5** (slim `cc-lint.example.mk`)
6. **A3** (decide on `_truncated_*` vs `meta.truncated`)
7. **B1** (narrow except clauses; mechanical but tedious)
8. **G1** (split `lint_cc`)
9. **E1**, **E2** (security docs + WAT read cap)
10. **A4** (version bump when shipping)

A2, D2, D3, F1-F3, G2-G4 are deferable to the point of being optional.
