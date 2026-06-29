# Common Crawl Response Linter

Run [httplint](https://github.com/mnot/httplint) against the
[Common Crawl](https://commoncrawl.org/) WAT archives and collect
statistics on the HTTP-level issues found in real-world responses.

The tool runs locally for development against a handful of WARC files,
and on Amazon EMR for full-crawl analyses. Both paths share the same
linting and reporting code; only the input distribution and result
aggregation differ.

---

## Local setup

Requires Python 3.9+.

```bash
make venv         # create .venv and install runtime + dev dependencies
make test         # run unit tests
make typecheck    # mypy
make lint         # pylint + validate-pyproject
```

`make venv` installs the `cc-lint[dev]` extras, which pulls in the
optional `[emr]` group (`mrjob`, `tqdm`) needed to drive EMR jobs.

### Local lint run

The `cc-lint` CLI fetches one or more WAT files over HTTP from
`data.commoncrawl.org`, runs httplint over their response metadata,
and renders an HTML + Markdown report:

```bash
# Put a few WARC paths in paths.txt, one per line:
echo crawl-data/CC-MAIN-2024-18/segments/.../warc/CC-MAIN-...warc.gz > paths.txt

make report.html  # lints and writes report.html plus report.md
```

The Make target wraps `cc-lint lint`; see `cc-lint lint --help` for the
full option set.

---

## Running on Amazon EMR

The EMR path scales the same linting pipeline across an entire
Common Crawl release. It uses mrjob to launch a transient EMR
cluster, distributes WARC paths across mappers, runs the linter in
fork-isolated child processes (so a single bad WARC cannot take a
mapper down), and aggregates the per-mapper `StatsCollector` dicts
into one summary record.

**EMR jobs cost money on your AWS account.** Always confirm the
cluster terminated in the EMR console after a run.

### One-time configuration

1. Install and configure the AWS CLI (region `us-east-1` is closest
   to Common Crawl's S3 bucket).

2. Copy the example config and fill in your bucket paths:

   ```bash
   cp cc-lint.example.mk cc-lint.mk
   # Edit cc-lint.mk: OUTPUT_DIR, PATHS_PREFIX, WHEEL_S3_PATH
   ```

   Every Make target reads `cc-lint.defaults.mk` first, then your
   `cc-lint.mk` overrides (or any `CONFIG=/path/to/another.mk`).

   Edit `mrjob.conf` and `mrjob-test.conf` to replace
   `s3://YOUR-BUCKET/cc-lint/emr-logs/` (the `cloud_log_dir` value)
   with a writable S3 path. mrjob writes the EMR cluster logs there
   so `make emr-timing EMR_LOG_CLUSTER_ID=j-...` has something to
   download for postmortems.

3. Build and upload the dependency wheel bundle. The bootstrap
   installs packages from `/tmp/wheels` on each EMR node with
   `--no-index`, so no PyPI traffic happens during a job:

   ```bash
   make wheels         # builds wheels in ./wheels via amazonlinux:2023 docker
   make upload-wheels  # sync ./wheels to $(WHEEL_S3_PATH)
   ```

4. Cache the Tranco top-sites CSV locally — it is uploaded with each
   job via `--files`:

   ```bash
   make tranco-cache
   ```

5. *(Optional)* Build the IP-to-ASN table for infrastructure
   fingerprinting. Every report fingerprints the CDN / server /
   framework behind each response from signal headers; supplying a
   CAIDA pfx2as snapshot additionally resolves the crawl-time
   `WARC-IP-Address` to an ASN, which catches CDNs that strip
   identifying headers. Set `IPASN_V4_URL` (and optionally
   `IPASN_V6_URL`) in your config to a snapshot near the crawl month,
   then:

   ```bash
   make ipasn-cache
   ```

   Once built, the table is shipped to mappers (and used by the local
   lint) automatically. Without it, fingerprinting is header-only.

### Smoke test

`make test-emr` runs the full pipeline against
`tests/fixtures/warc.paths.txt` with a small instance fleet
(`mrjob-test.conf`) and `LIMIT=1` so only a single WARC is processed.
Use it to validate AWS plumbing, wheel availability, and bootstrap
correctness before a full run:

```bash
make test-emr
```

Successful runs land in `results/test-<RUN_ID>/` with `part-*`
records from EMR plus the rendered `report.html` and `report.md`.

### Full run

```bash
make emr
```

This pipeline does, in order:

1. `cc_lint.emr.split_paths` reads
   `s3://commoncrawl/crawl-data/$(CRAWL_ID)/warc.paths.gz` (requester
   pays handled automatically) and uploads `MAP_TASKS` chunk files to
   `$(PATHS_PREFIX)$(CRAWL_ID)-$(RUN_ID)/`.
2. `cc_lint.emr.job` runs on EMR with `mrjob.conf` and
   `REDUCES` reducers. Each mapper forks a child per WARC path,
   pickles a `StatsCollector` snapshot back, and merges into a
   single per-mapper dict.
3. `aws s3 sync` pulls the reducer output into
   `results/$(CRAWL_ID)-$(RUN_ID)/`.
4. `cc_lint.emr.finalize` merges the sharded `globals` / `note:*` /
   `csp_sizes` records from the `part-*` files and renders
   `report.html` + `report.md`.

### Re-rendering an existing run

If you already have a `results/<run-name>/` directory with `part-*`
files synced from S3, regenerate the report without rerunning EMR:

```bash
make report RESULTS_DIR=results/CC-MAIN-2026-12-20260520-101500
# or:
make results/CC-MAIN-2026-12-20260520-101500/report.html
```

### Diagnosing slow or failed mappers

If you preserved EMR logs (`MRJOB_CLEANUP=NONE` or via the EMR
console), pass the cluster id to surface per-WARC timings and
failures:

```bash
make emr-timing EMR_LOG_CLUSTER_ID=j-XXXXXXXX
```

This downloads `stderr.gz` from S3, parses the structured
`INFO: finished WARC ...` lines emitted by the mapper, and prints a
Markdown summary of total/process/iterator times, top-N slow WARCs,
and any child-process failures.

### Configuration reference

`make show-config` prints the effective values. Important knobs:

| Variable | Purpose |
| --- | --- |
| `CRAWL_ID` | Common Crawl release to process (e.g. `CC-MAIN-2026-12`) |
| `TOP_N`, `LOCAL_TOP_N` | Tranco top-N filter for full / local runs |
| `RECORD_LIMIT` | Max records per WARC (0 = all) |
| `MAP_TASKS`, `REDUCES` | Full-run cluster sizing |
| `TEST_MAP_TASKS`, `TEST_REDUCES`, `LIMIT` | Smoke-test sizing |
| `OUTPUT_DIR`, `PATHS_PREFIX`, `WHEEL_S3_PATH` | S3 locations |
| `MRJOB_CLEANUP` | Set to `NONE` to keep cluster + logs for postmortem |

---

## Project layout

```
cc_lint/
├── cli.py              local cc-lint CLI (lint, report)
├── crawling.py         warcio-based WARC/WAT streaming for the CLI
├── linting.py          httplint wiring for warc/wat records
├── stats.py            StatsCollector and per-note tracking config
├── report.py           HTML report generator
├── top_sites.py        Tranco top-N loader
└── emr/
    ├── job.py          mrjob entry point (CCLintJob)
    ├── warc_source.py  requester-pays S3 + heartbeat WAT iterator
    ├── warc_worker.py  fork-isolated per-WARC worker + pickle result
    ├── split_paths.py  paths.gz -> N S3 chunk files
    ├── finalize.py     part-* -> rendered report.html + report.md
    ├── timing.py       EMR stderr.gz timing/failure summary
    └── compat.py       Python 3.13+ `pipes` shim for mrjob
```
