# cc-lint EMR efficiency & robustness review

Read-only audit of the cc-lint EMR pipeline focused on cost / efficiency
and recovery / robustness behaviour at full-crawl scale. No code
changes in this pass; findings are prioritised below for follow-up.

Severity: **H**igh = address before next full-crawl run, **M**edium =
follow-up commit, **L**ow = nice to have. Effort: **S**mall = single
file edit, **M**edium = multi-file refactor, **L**arge = cross-cutting
change.

## Configuration baseline

| Knob | Value | Notes |
| --- | --- | --- |
| Image | EMR 7.12.0, Python 3.12 | latest as of audit date |
| Master | 1 × m5.xlarge on-demand | |
| Core | 30 × on-demand, mixed m5/m5d/r5/r5d/c5.xlarge | identical fleet to cc-feeds |
| Map memory | 2 GB / 1.5 GB Xmx | comfortable for our small Python state |
| Reduce memory | 4 GB / 3 GB Xmx | bounded by per-key trim caps |
| Reduces | 20 | shards across ~100-200 active note keys after sharding |
| Reduce slowstart | 0.9 | reducers begin when 90% of maps done |
| Map tasks | 1600 | ~50 WARCs per mapper for the full crawl |
| Map / Reduce speculation | enabled (full), disabled (test) | see E5 |
| Requester pays | enabled for s3a | required for the commoncrawl bucket |
| Wheel bootstrap | dnf python3.12 + S3 wheel sync + offline pip | one-time per cluster |
| Per-WARC wall clock | 15 min (`--warc-timeout`) | added in Phase 2b |
| Child process | forked, pickled result | crash-isolated per WARC |
| Trim caps | 2000 / 5000 per dict | added in Phase 2b |

## E. Efficiency findings

**E1 (M / S). No S3 jitter on initial WARC download.** cc-feeds spaces
mapper S3 reads with a 0-30s random jitter before the first WARC
download to avoid 30 nodes × ~4 concurrent S3 GETs slamming the same
partition. cc-lint inherits the same fleet but skipped this. With 30
mappers each starting 50-WARC loops the simultaneous burst can hit
S3's per-prefix request budget (3500 GET/sec on a flat key, much less
within a single partition). cc-feeds reference:
`feed_survey/emr/job.py::mapper_init` (`self._s3_jitter = random.uniform(0, 30)`).
*Fix*: add the same 0-30s jitter to cc-lint's `_process_warc_in_child`
first-WARC path.

**E2 (M / M). No spot pricing.** Both projects run fully on-demand
core fleets. m5.xlarge on-demand is ~$0.192/hr; spot is typically
$0.05-0.07/hr (70% savings). A full-crawl run is ~30 cores × ~10h ≈
~$60 on-demand vs ~$20 spot. The blocker is robustness: a spot
interruption today restarts the mapper from scratch. Worth doing
together with E6 / R1.

**E3 (L / M). Mappers are serial within a chunk.** Each mapper
processes its ~50 WARCs sequentially: download → ArchiveIterator → lint
→ next. Download is I/O-bound (10-30 MB/s × ~100MB WAT = 5-10 s);
linting is CPU-bound. Pipelining the next download while the current
one lints would save ~30% mapper wall time on warm caches. Implementation
needs the child process to overlap with the parent's setup; tricky to
add without breaking the crash-isolation model. Defer until E2 (spot)
makes shorter mapper wall time more valuable.

**E4 (L / S). Wheel bootstrap pulls every node from one S3 prefix.**
30 nodes × ~30MB wheel bundle = 900 MB total at cluster start, all
from one S3 key prefix. Within budget. Could mirror to local NVMe
once a node has it and have peers fetch from the master, but the
saving (~30s of cluster startup) isn't worth the complexity.

**E5 (L / S). Speculative execution is enabled in the full-run
mrjob.conf.** With cc-lint's per-WARC accumulation in StatsCollector
and pickle round-trip back to the mapper, a speculated mapper attempt
duplicates work but produces the same output (the mrjob framework
discards the loser's output). No correctness risk. Cost is doubled
work on slow nodes only — but if "slow node" was the symptom of a
problem (memory pressure, network throttle), speculation may chase
the symptom forever. *Recommendation*: leave on; revisit only if
emr-timing logs show recurring speculation on the same paths.

**E6 (M / S). Bootstrap installs from a single S3 bucket with no
integrity check.** `aws s3 sync $(WHEEL_S3_PATH) /tmp/wheels/` then
`sudo pip install --no-index`. If the wheel bucket is poisoned (write
access compromised), every EMR node runs poisoned wheels as root.
This is documented in the code review (E1) but the EMR-specific fix
would be: pin wheel hashes via `--require-hashes` and check them into
git, or maintain a signed wheel manifest. Higher value than the code
review made it sound, since the wheels are bundled then deployed
without a content-addressed handle.

**E7 (L / S). REDUCES=20 may now be over-provisioned.** After the
sharding refactor, ~100-200 active note keys distribute across 20
reducers at 5-10 keys each. The big "globals" key is still a
single-reducer bottleneck (one site_hll + field_counts merge). If
that key's reducer becomes the long pole, dropping REDUCES to 10 or
15 saves shuffle overhead. Worth profiling once the next full run
produces stderr timings.

## R. Robustness findings

**R1 (H / M). No resilience to spot interruption.** Tied to E2. A
killed mapper restarts from scratch via mrjob's default retry (3
attempts). Per-WARC state isn't checkpointed; the mapper has
accumulated ~50 WARCs of stats in `self._stats_dict` and loses them
all on restart. With 10% hourly spot interruption × 10h = expected
1-3 full mapper restarts per run if we switched to spot today. Two
fixes:
- Mid-run checkpointing: emit `mapper_final`-shaped records every N
  WARCs into a per-attempt scratch path so a retry can resume. Adds
  complexity but unlocks spot.
- Smaller mapper chunks: `MAP_TASKS=3200` (25 WARCs/mapper) halves the
  retry blast radius without checkpointing. Cheaper.

**R2 (M / S). Hadoop default 3-attempt retry is fine, but the failure
modes aren't distinguished.** A WARC that fails consistently (e.g. a
malformed WAT) burns 3 mapper attempts × ~3 min each before being
skipped on the 4th. The fork-isolated child correctly returns a
non-zero exit, but the parent doesn't track per-WARC retry counts
across mapper attempts. *Fix*: maintain a per-(crawl_id, warc_path)
poison-list in S3 / DynamoDB so genuinely bad WARCs are skipped after
N retries cluster-wide.

**R3 (M / S). Truncated part-* files silently degrade data.**
`cc_lint.emr.finalize._iter_records` already handles malformed lines
(logs WARN, skips). But a reducer that died mid-write leaves a part
file with a truncated final record; the WARN-and-skip means we
silently lose that record's contribution. With REDUCES=20 each part
carries 5-10 note records → losing one is 5-10% of one note id's
data. *Fix*: have the reducer wrap each emit in a `(key, payload)`
size-prefixed line or emit a trailing sentinel `\n# END\n` and have
finalize verify it.

**R4 (M / S). split_paths and finalize are idempotent; the job itself
isn't.** A re-run of `make emr` generates a new `$(FULL_RUN_NAME)` (a
timestamp suffix), so re-attempting after a failure builds a new
dir tree. Recoverable but not resumable; you re-pay the full cost.
*Fix*: stable run-id derivation (`<CRAWL_ID>-<git-sha>`?) so re-runs
land in the same paths and split_paths is idempotent against an
existing dir, which it already is.

**R5 (L / S). Reducer state is purely summing.** Restart-safe by
construction; trim_stats_dict is deterministic and idempotent. Mrjob
handles attempt-loser discard correctly. No action.

**R6 (M / S). No per-WARC dedup across mapper attempts.** When a
mapper attempt 1 dies after processing 30 of 50 WARCs, attempt 2
re-processes all 50. Stats from the 30 WARCs in attempt 1 were never
emitted (mapper_final didn't run) so this is correctness-safe — but
costs 30 WARCs of work. *Fix*: emit partial state via mapper-side
counters at every WARC boundary so the framework's
`mapreduce.task.recovery` could resume. Complex; cheap workaround is
R1's smaller chunks.

**R7 (L / S). Network-blip retries are conservative.** boto3 Config
in `cc_lint.emr.warc_source` says `max_attempts=3, mode=standard`.
Standard mode caps retries at 3 with exponential backoff. CC's S3
prefix occasionally throttles; raising to `mode=adaptive,
max_attempts=5` would reduce mapper failures without adding much
latency. *Fix*: one-line config bump.

**R8 (L / S). Hadoop counter cardinality OK.** After the bucketed
failure counters in Phase 2b, we emit a fixed set of ~15 counters per
mapper. Well under the default `mapreduce.job.counters.max=120`.

**R9 (M / S). Wall-clock timeout protects mappers but not the cluster
overall.** A single straggler mapper that doesn't fail but stays slow
(e.g. 5h instead of 2h) drives the cluster cost up. Hadoop's
`mapreduce.task.timeout` (default 600s, but our setup doesn't override)
would kill a mapper that doesn't increment counters / report status
for 10 min. cc-lint's child processes log every 30s, so they'll keep
the parent reporting. *Fix*: shorten `mapreduce.task.timeout` to e.g.
3600s so a wedged mapper gets killed and re-attempted instead of
holding the cluster open.

**R10 (L / S). EMR step retries vs mapper retries.** mrjob runs the
job as a single EMR step; step failure means the whole job fails.
With auto-termination on, the cluster terminates and the run is lost.
Tradeoff: keeping the cluster alive on failure for debugging means
paying for it until you notice. Current `MRJOB_CLEANUP=TMP` cleans
working data but keeps the cluster. *Fix*: leave as-is; the explicit
`make emr-timing EMR_LOG_CLUSTER_ID=j-...` workflow handles
postmortems.

## D. Diagnostics findings

**D1 (L / S). emr-timing log parser doesn't surface the new
warc_timed_out counter.** `cc_lint/emr/timing.py` parses
`INFO: finished WARC` / `ERROR: failed WARC` lines but doesn't have
a regex for `ERROR: timeout WARC` added in Phase 2b. *Fix*: extend
the regex set to bucket timeouts separately from other failures.

**D2 (L / S). Per-WARC timings are emitted but not aggregated at
finalize.** The `record_process_ms`, `iterator_download_ms`,
`warc_total_ms` Hadoop counters give cluster-wide totals but not
per-WARC or per-mapper distributions. emr-timing reconstructs this
from stderr.gz. *Fix*: have finalize also pull the EMR step counters
via the AWS SDK and persist them next to stats.json for an at-a-glance
"30k WARCs, p50 download 8.4s, p99 23s".

## Suggested follow-up order

If we tackle these, suggested ordering by ROI:

1. **E1** (S3 jitter) — single S patch, addresses a real production
   pressure point.
2. **R7** (boto3 retry config) — single line of config, improves
   transient-network resilience.
3. **D1** (timing log parser) — small extension to surface the
   new timeout counter.
4. **R3** (truncated part-* sentinel) — defensive against rare partial
   writes.
5. **R9** (`mapreduce.task.timeout` cap) — single jobconf line, kills
   wedged mappers faster.
6. **R4** (stable run-id) — quality-of-life for resumable runs.
7. **R1 + E2** (spot + checkpointing) — biggest cost win but the most
   work. Worth it if cc-lint runs become regular (weekly+).
8. **E6** (wheel hash pinning) — security hardening; do after the
   pipeline is stable.

E3 (pipelined downloads), E4 (wheel mirror), E5 (speculation review),
E7 (REDUCES tuning), R5 (no-action), R6 (per-WARC dedup), R8 (counter
limit), R10 (cluster auto-terminate), D2 (counter aggregation) are
either deferrable or no-action.
