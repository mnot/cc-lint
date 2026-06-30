# Common Crawl Response Linter

Run [httplint](https://github.com/mnot/httplint) against the
[Common Crawl](https://commoncrawl.org/) WAT archives and report aggregate
statistics on the HTTP-level issues found in real-world responses.

There are two ways to run it, sharing the same linting and reporting code
— only the input distribution and result aggregation differ:

- **Local** — lint a handful of WAT files fetched over HTTP. No AWS
  required; good for spot checks and development. Start here.
- **Amazon EMR** — distribute an entire Common Crawl release across a
  transient EMR cluster for a full-crawl analysis (~123M responses).
  Needs an AWS account and costs money per run.

---

## Local run

Lint a few WAT files with no AWS setup. Requires Python 3.10+; the
virtualenv is created automatically on first run.

```bash
# One or more WAT paths, one per line:
echo crawl-data/CC-MAIN-2024-18/segments/.../warc/CC-MAIN-...warc.gz > paths.txt

make report.html   # fetches the WATs, lints, writes report.html + report.md
```

`make report.html` wraps the `cc-lint lint` CLI: it fetches each WAT over
HTTP from `data.commoncrawl.org`, runs httplint over the response
metadata, and renders the report. See `cc-lint lint --help` for the full
option set.

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

2. Create your config files. `make setup` prompts for your S3 bucket
   name and generates all three from the tracked `*.example` templates:

   ```bash
   make setup
   ```

   It writes `cc-lint.mk` (your `OUTPUT_DIR` / `PATHS_PREFIX` /
   `WHEEL_S3_PATH`) plus `mrjob.conf` and `mrjob-test.conf` (the
   `cloud_log_dir` where mrjob preserves EMR cluster logs, so
   `make emr-timing EMR_LOG_CLUSTER_ID=j-...` has something to
   download for postmortems). All three are gitignored, so your bucket
   never lands in a commit. `make setup` refuses to overwrite an
   existing file — delete it first to regenerate.

   To do it by hand instead, `cp` each `*.example` to its real name and
   replace `YOUR-BUCKET`. Every Make target reads `cc-lint.defaults.mk`
   first, then your `cc-lint.mk` overrides (or any
   `CONFIG=/path/to/another.mk`).

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
4. `cc_lint.emr.finalize` merges the sharded reducer records from the
   `part-*` files — `globals`, per-note `note:*`, and the per-dimension
   shards (`csp_sizes`, `vary`, `cache_control`, `value_histograms`,
   `cooccur`, `note_cooccur`, `transition`) — and renders `report.html`
   + `report.md`.

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
| `SAMPLE_TOP_N` | Tranco ceiling for collecting per-note sample URLs |
| `RECORD_LIMIT` | Max records per WARC (0 = all) |
| `MAP_TASKS`, `REDUCES` | Full-run cluster sizing |
| `TEST_MAP_TASKS`, `TEST_REDUCES`, `LIMIT` | Smoke-test sizing |
| `OUTPUT_DIR`, `PATHS_PREFIX`, `WHEEL_S3_PATH` | S3 locations |
| `MRJOB_CLEANUP` | Set to `NONE` to keep cluster + logs for postmortem |

---

## Contributing

Development setup, the test / typecheck / lint workflow, commit
conventions, and a tour of the project layout live in
[CONTRIBUTING.md](CONTRIBUTING.md).
