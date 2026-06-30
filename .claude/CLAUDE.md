# cc-lint — operating notes for Claude

cc-lint runs [httplint](https://github.com/mnot/httplint) against Common Crawl
WAT records on Amazon EMR via mrjob, then renders an HTML + Markdown report.
This file captures the constraints that aren't obvious from the code. Read it
before making non-trivial changes.

## Scale and cost

A single full run processes ~123M responses across ~50k distinct sites and
yields ~1.2B note occurrences (CC-MAIN-2026-12 filtered to Tranco top-100k).
Wall time on the prod fleet (30 c5.xlarge cores) is ~11 hours; on-demand
spend is ~$60. Every commit can move that number. Calibrate with
`make test-emr` before changing knobs that affect prod.

The test pipeline (`make test-emr`, 5–15 cores, 200 WATs, ~6 min) is
deliberately set to the same `LOCAL_TOP_N=100000` as production so per-WAT
cost is representative. Don't widen the test/prod gap "to make the test
faster" — you'll lose the only honest extrapolation we have.

`LIMIT=N make test-emr` controls both the upload cap and `--limit`; defaults
to 1 (one WAT total) which is intentional, not a bug — the bare `make
test-emr` is a smoke test.

## The shuffle is the budget

Every byte that crosses from mapper to reducer pays Hadoop shuffle cost ×
1600 mappers × 10 reducers. The pipeline holds the line in several places —
breaking any of them turns an 11-hour run into a memory disaster:

- **Per-mapper top-K trim** before emit. `TOP_K_VAR_VALUES=2000` per
  `vars[var_name]` dict; `TOP_K_FIELD_COUNTS=5000` for field/unprocessed
  counts (and for the per-header `field_bytes` byte-economics dict, #10);
  `TOP_K_CSP_SITES=100000` for the per-site CSP-size dict; `TOP_K_RECIPES=2000`
  per Vary *and* Cache-Control recipe/marginal dict (reused for the header
  *and* note co-occurrence bundle/marginal/pair dicts too); `TOP_K_ASN=5000` for the
  per-ASN count dict (`truncated_asn_counts` flag). Adding a new tracked dict
  means adding a trim path and a `truncated_*` flag.
- **Sharded reducer keys** (`cc_lint.emr.job`): the mapper does NOT emit a
  single "stats" record. It emits `GLOBALS_KEY`, `NOTE_KEY_PREFIX:<note_id>`
  per note, `CSP_SIZES_KEY`, `VARY_KEY`, `CACHE_CONTROL_KEY`, `COOCCUR_KEY`,
  `NOTE_COOCCUR_KEY`, and `TRANSITION_KEY`. Adding a new top-level aggregation
  usually means a new shard key; do not stuff it into globals.
- **Bounded dicts skip the trim path**. The transition-tax block
  (`cc_lint.transition`, `TRANSITION_KEY`) is the exception to the "new dict
  = new trim path + `truncated_*` flag" rule: its key space is
  `len(TRANSITION_PAIRS) × 4 categories`, a compile-time constant, so it has
  no long tail to cap and the reducer just folds it. If you add a config-bounded
  aggregation, document *why* it's untrimmed; only corpus-growing dicts need a trim.
- **HLL precision**: `HLL_P_GLOBAL=12` (4096 registers), `HLL_P_PER_NOTE=8`
  (256 registers), `HLL_P_RECIPE=6` (64 registers). Per-note HLLs are
  deliberately less precise to keep shuffle bounded across ~150 notes.
  Per-(var, value) HLLs multiply by tracked-value cardinality — think hard
  before adding them. The Vary breakdown does add them (per-recipe and
  per-field-name site HLLs), so it pays for the cost two ways: the
  `TOP_K_RECIPES` trim caps the count, and the high-cardinality recipe HLLs
  drop to the coarse `HLL_P_RECIPE` precision (a recipe's site count is a
  ranking signal, not a headline). Per-field *marginal* HLLs stay at
  `HLL_P_PER_NOTE` — their key space is bounded and they carry the headline
  axes. Any future per-value HLL set should follow the same discipline.
- **Histograms are categorical labels, not raw numerics**. See
  `cc_lint/histograms.py`. A raw `freshness_left` value in seconds would
  shred the var dict; the bucketed label is one of 8 strings.
- **Hadoop counter cardinality cap** (~120). Failure counters are bucketed
  (`warc_exit_zero`, `warc_signal_sigkill`, …) — never emit a counter per
  exit code / exception type.

## Merge contract (don't drop fields)

`StatsCollector.to_dict()` produces up to 19 fields; five are conditional —
`vary` is emitted only when a response carried a `Vary` header,
`cache_control` only when one carried a `Cache-Control` header, and
`value_histograms` only when a tracked numeric header was seen — while
`cooccur` (the security-header co-occurrence block), `note_cooccur` (the
finding/note co-occurrence block, issue #7), and `transition` (the
legacy/modern dual-emit / transition-tax block, issue #11) are emitted
whenever there were any responses. `merge_stats_dict()` in `cc_lint/emr/job.py`
must merge **every** field (`merge_cooccur` covers the bundle/marginal/pair
dicts and the per-bundle `by_layer` infra breakdown — and is reused for
`note_cooccur`, which carries the same shape minus `by_layer`; `merge_transition`
covers the per-pair, per-category occurrence + site-HLL dicts; `merge_cache_control`
and `merge_vary` cover their recipe/marginal dicts; `_merge_header_bytes`
covers the #10 byte-economics trio — `field_bytes`, `header_block_hist`, and
the `total_header_bytes` scalar — which ride the `globals` shard), and
`merge_note()` must
merge every per-note field — anything missed is silently dropped at
mapper-aggregation time, the reducer never sees it, and the report shows
zeros. We had this exact bug; the regression test in
`tests/test_emr_job.py::test_merges_every_to_dict_field` exists to catch it.

If you add a field to `to_dict()` (or to per-note data), update both merges
and extend that test.

## Per-WAT worker isolation

Each WAT runs in a fork-isolated child via `multiprocessing.get_context()`,
so a segfault / OOM / hang in httplint or warcio takes out one WAT, not the
mapper. `--warc-timeout` (15 min) caps wall clock per WAT; the Hadoop
`mapreduce.task.timeout=1800000` is the outer net. boto3 uses adaptive retry
with `max_attempts=5`. Each mapper sleeps a random 0–30s before its first S3
fetch to break burst clustering across 1600 simultaneous mappers.

## Reports

- HTML (`cc_lint/report/sections.py`) and Markdown (`cc_lint/report/markdown.py`)
  render the same data and must stay in sync — every new field shows up in
  both. They live in one module each on purpose; resist splitting.
- Note summary templates come from `httplint.note.Note._summary` via
  `build_summary_index()` in `cc_lint/report/severity.py`. We surface the
  template raw (with `%(var)s` placeholders intact) — the variable values
  appear in the per-var tables below.
- `_VAR_LABELS` / `_VAR_LABELS_MD` map synthetic var names
  (`directive_conflicts`, `field_name_key`, `*_bucket`, …) to human-friendly
  headings. Add new synthetic vars to both.
- The "Top Response Headers" view filters `x-crawler-*` — those are
  Common-Crawl-injected, not part of the upstream HTTP response. Apply the
  same filter to any new header view.
- Notes sort by severity desc, then per-note site HLL estimate desc, then
  occurrence count desc. Adding a noisy single-site note shouldn't drown out
  a broadly-fired one.
- The non-standard header census (`cc_lint/header_census.py`, #12) is a
  *report-time* derivation — no mapper or shuffle change. It clusters the
  proprietary header names from the merged `unprocessed_counts` head (the
  dedicated non-standard top-K, deeper-tailed than `field_counts`) by inferred
  vendor (name-based, via `Fingerprinter.vendor_for_name` reusing the #4
  signal table + its new `name_prefixes`), by semantic family
  (`header_families.toml`), and by auto-derived literal prefix. Byte share
  comes from the #10 `field_bytes` dict. Cluster totals are head-only lower
  bounds; accurate per-cluster totals would need the cluster key computed at
  map time (a deliberate future enhancement). `build_census` is called once in
  each renderer and its output is deterministically ordered + lowercased so a
  future longitudinal "what's new this crawl" diff (the part-b of #12 that is
  gated on multi-crawl runs) is a set-difference, not a reformat.

## httplint pin

Imports must use `from httplint.message import HttpResponseLinter`, not the
top-level `from httplint import …` — the installed wheel doesn't re-export
from `__init__`. The pin in `pyproject.toml` is `>=2026.5.2`; bump it
alongside dependent code, not in isolation.

The `BODY_ONLY_NOTES` set in `cc_lint/report/severity.py` is filtered
against `possible_note_ids()` at render time, so stale entries are harmless
— but they will surface in tests that assert on the rendered Unseen
section. If you see a test pinning a specific note name, that name is real
in the currently-installed wheel.

## Local-only config in `mrjob.conf` / `mrjob-test.conf`

These are gitignored, like `cc-lint.mk`. The repo tracks
`mrjob.conf.example` / `mrjob-test.conf.example` with a
`cloud_log_dir: s3://YOUR-BUCKET/cc-lint/emr-logs/` placeholder; an operator
`cp`s each to the real filename and fills in their bucket. Edit the example
when changing the committed EMR knobs (instance fleet, jobconf); the real
`mrjob.conf` is a local copy and never committed.

## Conventions

- Tests / mypy / pylint must all pass: `make test typecheck lint`. pylint
  is held at 10.00/10. Mypy is strict. No `# type: ignore` without a
  comment justifying it.
- Commits: keep-a-changelog prefixes — `Added:`, `Changed:`, `Fixed:`,
  `Removed:` — past tense. `Makefile.pyproject` greps these to assemble
  the release changelog (`make changelog.md`); commits using `Add:`,
  `Fix:`, `Refactor:`, `Test:`, `Docs:`, `Build:` are invisible to the
  release tooling, so prefer the four canonical prefixes whenever the
  change fits one. Subject ≤72 cols. The body explains *why*. Include
  the `Co-Authored-By: Claude Opus 4.8` trailer.
- No backward-compat shims, no half-finished implementations, no
  speculative abstractions. The codebase prefers "delete cleanly" to
  "deprecate gradually" — fine here because there are no external
  consumers.
- The `--ultrareview` / multi-agent workflows are not used here; one Claude
  session per change is the norm.

## Where things live

The `cc_lint/` package map is in [CONTRIBUTING.md](../CONTRIBUTING.md) —
don't duplicate it here. Root build/config files not covered there:

```
Makefile                # All run targets; see `make help`
mrjob.conf.example      # Prod EMR config template (30 c5.xlarge cores)
mrjob-test.conf.example # Test EMR config template (5–15 c5.xlarge cores)
mrjob.conf              # Local copy of the above (gitignored; real bucket)
cc-lint.defaults.mk     # TOP_N, MAP_TASKS, REDUCES, etc.
cc-lint.mk              # Per-operator overrides (bucket names; gitignored)
```
