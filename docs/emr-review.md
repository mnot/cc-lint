# cc-lint EMR efficiency & robustness review

Read-only audit of the cc-lint EMR pipeline focused on cost / efficiency
and recovery / robustness behaviour at full-crawl scale. No code
changes in this pass; findings are prioritised below for follow-up.

Severity: **H**igh = address before next full-crawl run, **M**edium =
follow-up commit, **L**ow = nice to have. Effort: **S**mall = single
file edit, **M**edium = multi-file refactor, **L**arge = cross-cutting
change.

## Workload character

**cc-lint reads WAT, not WARC.** Both `cc_lint.crawling._warc_to_wat`
and `cc_lint.emr.warc_source.warc_path_to_wat` rewrite the input
path: `/warc/` → `/wat/` and `.warc.gz` → `.warc.wat.gz`. WAT files
carry response metadata only (HTTP headers, no body), so they are
roughly an order of magnitude smaller than the WARC equivalents
(~100-300 MB compressed per WAT vs ~1 GB per WARC).

This inverts the cc-feeds assumption that the workload is I/O-bound:

- **Download dominates cc-feeds wall time** because each WARC is
  ~1 GB and the mapper streams it before httplint can act on the
  response body.
- **httplint CPU dominates cc-lint wall time** because each WAT is
  small (fast download) but each metadata record decodes JSON,
  reconstructs HTTP headers, and runs the full httplint analysis. A
  typical WAT carries ~50-80k metadata records; processing all of
  them at modest per-record cost is the long pole.

Several findings below were written with cc-feeds' I/O profile in
mind and have been re-scored or dropped after this re-examination.

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
| Map tasks | 1600 | ~50 WATs per mapper for the full crawl |
| Map / Reduce speculation | enabled (full), disabled (test) | see E5 |
| Requester pays | enabled for s3a | required for the commoncrawl bucket |
| Wheel bootstrap | dnf python3.12 + S3 wheel sync + offline pip | one-time per cluster |
| Per-WAT wall clock | 15 min (`--warc-timeout`) | added in Phase 2b |
| Child process | forked, pickled result | crash-isolated per WAT |
| Trim caps | 2000 / 5000 per dict | added in Phase 2b |

## E. Efficiency findings

**E0 (H / S). Instance fleet is wrong for a CPU-bound workload.** The
fleet inherits cc-feeds' choices: m5, m5d, r5, r5d, c5. cc-feeds has
real reasons for the d/r mix — local NVMe (m5d/r5d) helps WARC
streaming, and r5 memory matters for full-WARC parsing. cc-lint
needs neither: WAT records are small, all state is in-process Python
dicts, and per-mapper memory caps at 2 GB. The compute-optimised c5
family (4 vCPU, 8 GB RAM, ~$0.085/hr on-demand) is roughly half the
price of m5.xlarge (~$0.192) and matches cc-lint's actual resource
shape. EMR's instance-fleet allocator picks the cheapest available
instance by default, so c5.xlarge will usually win — but the
heterogeneous fleet still allocates m5/r5 instances when c5 capacity
is short, pushing the average cost up. *Fix*: tighten the cc-lint
full-run fleet to c5/c5n only, or set explicit weighted capacity to
prefer c5 strongly. Estimated savings 30-50% of compute cost.

**E1 (L / S). S3 jitter is less urgent for WAT.** cc-feeds spaces
mapper S3 reads with a 0-30s random jitter before the first WARC
download to avoid 30 nodes simultaneously hitting one S3 prefix.
cc-lint inherits the same fleet but skipped this. For WAT the burst
pressure is real but the per-download wall time is short (a few
seconds), so the contention window is narrower than for cc-feeds.
*Recommendation*: add jitter for defensive parity with cc-feeds, but
it is a low-impact finding for cc-lint specifically. Trivial
one-liner if we want it.

**E2 (M / M). No spot pricing.** Both projects run fully on-demand
core fleets. c5.xlarge on-demand is ~$0.085/hr; spot is typically
$0.02-0.03/hr (~70% savings). With E0's c5 switch a full-crawl run
is ~30 cores × ~10h × $0.085 ≈ ~$25 on-demand vs ~$8 spot.
Absolute savings are modest in dollars but the % is large, and spot
also reduces the cost of long-running ad-hoc runs. The blocker is
robustness: a spot interruption today restarts the mapper from
scratch. Worth doing together with R1.

**E3 (DROP). Pipelined downloads.** The original review flagged
"download next WAT while linting current" as a ~30% speedup. That
was written assuming I/O-bound mappers. For cc-lint the download
portion is a small fraction of mapper wall time (lint dominates), so
pipelining yields maybe ~5-10% — not worth the complexity of
breaking the crash-isolation model. **Dropped.**

**E4 (L / S). Wheel bootstrap pulls every node from one S3 prefix.**
Unchanged from original review. 30 nodes × ~30MB at cluster start =
~900MB total from one S3 key prefix. Within budget. Could mirror to
local NVMe and have peers fetch from the master, but the saving
(~30s of cluster startup) isn't worth the complexity.

**E5 (M / S). Speculative execution is more expensive on CPU-bound
workloads.** With cc-feeds (I/O-bound), a speculated mapper doesn't
double CPU usage because the slow mapper is waiting on S3, not
burning CPU. cc-lint's slow mappers are usually slow because they're
*using* CPU — speculation duplicates the CPU work. Combined with
mrjob's discard-loser semantics, speculation effectively doubles
billable core-hours for any slow-but-correct mapper. *Fix*: disable
speculation in the full-run mrjob.conf (it's already off in the test
config). Re-enable selectively if a future run shows consistent
slow-mapper symptoms that point to network or memory rather than CPU.
This is a one-jobconf-line change worth ~10-20% on the slow tail.

**E6 (M / Doc). Bootstrap installs from a single S3 bucket with no
integrity check.** Unchanged from original. Document the wheel
bucket access-policy requirements in README.md (cc-lint.example.mk
already has a note as of the C5 commit).

**E7 (L / S). REDUCES=20 may be over-provisioned for a small
reducer payload.** Unchanged from original; the merged dict is
small enough that 10 reducers would be fine. Worth profiling once a
full run produces stderr timings.

## R. Robustness findings

**R1 (H / M). No resilience to spot interruption.** Tied to E2. A
killed mapper restarts from scratch via mrjob's default retry (3
attempts). Per-WAT state isn't checkpointed; the mapper has
accumulated ~50 WATs of stats in `self._stats_dict` and loses them
all on restart. With 10% hourly spot interruption × 10h = expected
1-3 full mapper restarts per run if we switched to spot today. Two
fixes:
- Mid-run checkpointing: emit `mapper_final`-shaped records every N
  WATs into a per-attempt scratch path so a retry can resume.
- Smaller mapper chunks: `MAP_TASKS=3200` (25 WATs/mapper) halves
  the retry blast radius without checkpointing. Cheaper. **This is
  particularly attractive for cc-lint** because a mapper's wall
  time is CPU-dominated and roughly halves with half the chunk, so
  smaller chunks also reduce the wall-clock target a retry has to
  finish in.

**R2 (M / S). 3-attempt retry doesn't deduplicate cluster-wide.** A
WAT that fails consistently (e.g. malformed JSON) burns 3 mapper
attempts before being skipped on the 4th. *Fix*: maintain a
per-(crawl_id, warc_path) poison-list in S3 / DynamoDB so genuinely
bad WATs are skipped after N retries cluster-wide.

**R3 (M / S). Truncated part-* files silently degrade data.**
Unchanged from original. A reducer that died mid-write leaves a part
file with a truncated final record; `cc_lint.emr.finalize._iter_records`
logs WARN and skips. *Fix*: have the reducer wrap each emit in a
size-prefixed line or emit a trailing sentinel and have finalize
verify it.

**R4 (M / S). split_paths and finalize are idempotent; the job isn't.**
A re-run of `make emr` generates a new `$(FULL_RUN_NAME)`. Recoverable
but not resumable; you re-pay the full cost. *Fix*: stable run-id
derivation (`<CRAWL_ID>-<git-sha>`) so re-runs land in the same paths.

**R5 (L / S). Reducer state is purely summing.** Unchanged. No action.

**R6 (M / S). No per-WAT dedup across mapper attempts.** Unchanged.
Cheap workaround: R1's smaller chunks.

**R7 (L / S). Network-blip retries are conservative.** Unchanged.
`boto3.Config(retries={"max_attempts": 3, "mode": "standard"})` →
`mode=adaptive, max_attempts=5` would reduce mapper failures on
transient S3 throttles. *Fix*: one-line config bump.

**R8 (L / S). Hadoop counter cardinality OK.** Unchanged.

**R9 (M / S). Wall-clock timeout protects mappers but not the
cluster.** Cap `mapreduce.task.timeout` to 3600s so a wedged mapper
gets killed and re-attempted instead of holding the cluster open.

**R10 (L / S). EMR step retries vs mapper retries.** Unchanged.
Leave as-is.

## D. Diagnostics findings

**D1 (L / S). emr-timing log parser doesn't surface
warc_timed_out counter.** Unchanged. One-regex extension.

**D2 (L / S). Per-WAT timings are emitted but not aggregated at
finalize.** Unchanged. *Fix*: have finalize pull EMR step counters
via the AWS SDK and persist next to the report.

## Suggested follow-up order

With WAT-CPU profile in mind:

1. **E0** (c5-only fleet) — single config change, ~30-50% compute
   cost reduction. Biggest single win.
2. **E5** (disable speculation in full-run) — single jobconf line,
   ~10-20% saving on slow tails.
3. **R7** (boto3 retry config) — one line, transient-network resilience.
4. **D1** (timing log parser) — small extension for the new timeout
   counter.
5. **R3** (truncated part-* sentinel) — defensive against rare partial
   writes.
6. **R9** (mapreduce.task.timeout cap) — kills wedged mappers faster.
7. **R4** (stable run-id) — quality-of-life for resumable runs.
8. **R1 + E2** (spot + checkpointing or smaller chunks) — biggest
   cost win at the cost of the most work. With E0's already-cheaper
   fleet the absolute dollar savings are smaller than they looked in
   the first draft, so this is less urgent than it appeared.
9. **E1** (S3 jitter) — defensive parity with cc-feeds; low impact
   for cc-lint specifically.
10. **E6** (wheel hash pinning) — security hardening once stable.

E3 (pipelined downloads) is dropped as not worth it for a CPU-bound
workload. E4, E7, R5, R6, R8, R10, D2 deferrable or no-action.
