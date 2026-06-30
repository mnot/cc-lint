# Contributing to cc-lint

## Development environment

Requires Python 3.10+.

```bash
make venv         # create .venv and install runtime + dev dependencies
make test         # run unit tests
make typecheck    # mypy (strict)
make lint         # pylint + validate-pyproject
make tidy         # isort + black
```

`make venv` installs the `cc-lint[dev]` extras, which pull in the optional
`[emr]` group (`mrjob`, `tqdm`) needed to drive EMR jobs.

Tests, mypy, and pylint must all pass before a change lands; pylint is held
at 10.00/10 and mypy runs in strict mode. Avoid `# type: ignore` without a
comment justifying it.

## Commit conventions

Use keep-a-changelog prefixes — `Added:`, `Changed:`, `Fixed:`, `Removed:`,
past tense — for any user-facing change; the release tooling greps these to
assemble the changelog, so other prefixes are invisible to it. Keep the
subject ≤72 columns and let the body explain *why*. Trivial commits
(formatting, doc tweaks, making lint/typecheck happy) don't need a prefix.

## Project layout

```
cc_lint/
├── cli.py / __main__.py  local `cc-lint lint` CLI
├── crawling.py           HTTP-only WAT streaming for the CLI
├── cc_paths.py           Common Crawl path helpers (CLI + EMR)
├── linting.py            httplint wiring for WAT records
├── stats.py              StatsCollector and per-note tracking config
├── top_sites.py          Tranco top-N loader + site normalisation
├── hll.py                HyperLogLog distinct-site estimator
├── histograms.py         bucket scales for numeric note vars
├── redact.py             scrub on-the-wire secrets from samples (#28)
├── types.py              shared TypedDicts
│   # analysis dimensions — each its own shuffle aggregation
├── cooccur.py            header co-occurrence (#6)
├── note_cooccur.py       finding co-occurrence (#7)
├── transition.py         legacy/modern transition tax (#11)
├── vary.py               Vary composition
├── cache_control.py      Cache-Control recipes
├── recipes.py            shared top-N recipe machinery
├── fingerprint.py        infrastructure fingerprinting (#4)
├── ipasn.py              offline IP->ASN from a CAIDA pfx2as snapshot (#4)
├── header_categories.py  header byte-economics categories (#10)
├── header_census.py      non-standard header census (#12)
├── *.toml                data tables: fingerprints, header_families,
│                         cooccur_alphabet
├── report/
│   ├── render.py         top-level orchestration (HTML + Markdown)
│   ├── sections.py       HTML sections (incl. the TOC nav)
│   ├── markdown.py       Markdown renderer
│   ├── severity.py       Note-class severity / category / summary lookup
│   └── styles.py         CSS
└── emr/
    ├── job.py            mrjob entry point; mapper/reducer; merge_* fns
    ├── warc_source.py    requester-pays S3 + heartbeat WAT iterator
    ├── warc_worker.py    fork-isolated per-WARC worker + pickle result
    ├── split_paths.py    paths.gz -> N S3 chunk files
    ├── finalize.py       part-* -> rendered report.html + report.md
    ├── timing.py         EMR stderr.gz timing/failure summary
    └── compat.py         Python 3.13+ `pipes` shim for mrjob
```

The HTML (`report/sections.py`) and Markdown (`report/markdown.py`) renderers
surface the same data and must stay in sync — a new field shows up in both.
On the EMR side, every field `StatsCollector.to_dict()` produces must be
merged in `cc_lint/emr/job.py`; a missed field is silently dropped at
aggregation time. See the regression test in
`tests/test_emr_job.py::test_merges_every_to_dict_field`.
