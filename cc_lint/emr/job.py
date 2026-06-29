"""mrjob entry point for the cc-lint EMR pipeline.

Each mapper reads a chunk of Common Crawl WARC paths (one per input
line), spawns a fork-isolated child for each path that downloads and
processes the corresponding WAT file, then merges the child's
StatsCollector into the mapper's running totals. ``mapper_final`` emits
the serialized StatsCollector once per mapper; the reducer merges all
mapper outputs into a single summary record.
"""

# pylint: disable=abstract-method,attribute-defined-outside-init,broad-exception-caught

import random
import sys
import tempfile
import time
import traceback
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from multiprocessing import get_context
from typing import Any, Dict, Generator, Iterator, List, Optional, Set, Tuple

from cc_lint.emr.compat import install_mrjob_pipes_compat

install_mrjob_pipes_compat()

# pylint: disable=wrong-import-position,wrong-import-order,ungrouped-imports
from mrjob.job import MRJob
from mrjob.protocol import JSONProtocol

import cc_lint
from cc_lint.emr.warc_worker import (
    WarcWorkerResult,
    load_warc_worker_result,
    process_warc_to_file,
)
from cc_lint.hll import hll_merge
from cc_lint.stats import VAR_SAMPLE_LIMIT, StatsCollector
from cc_lint.top_sites import load_top_sites
from cc_lint.vary import merge_vary, trim_vary


def _httplint_version() -> str:
    try:
        return _pkg_version("httplint")
    except PackageNotFoundError:
        return ""


def _build_run_context(options: Any) -> Dict[str, Any]:
    """Snapshot the run-shaping flags so the report can show provenance."""
    return {
        "crawl_id": getattr(options, "crawl_id", "") or "",
        "top_sites": int(getattr(options, "top_sites", 0)),
        "sample_top_sites": int(getattr(options, "sample_top_sites", 0)),
        "record_limit": int(getattr(options, "record_limit", 0)),
        "warc_limit": int(getattr(options, "limit", 0)),
        "warc_timeout_s": int(getattr(options, "warc_timeout", 0)),
        "cc_lint_version": cc_lint.__version__,
        "httplint_version": _httplint_version(),
    }


SAMPLE_LIMIT = 5
# VAR_SAMPLE_LIMIT is imported from cc_lint.stats so the collection-side cap and
# this reducer-side merge cap stay equal (single source of truth).

# Long-tail caps applied before shuffle and again at reducer output. The web
# generates extremely long-tailed value distributions for things like
# UNKNOWN_VALUE.value and STRUCTURED_FIELD_PARSE_ERROR.field_error; keeping
# the top entries by occurrence preserves the report signal while bounding
# the shuffle and final report size.
TOP_K_VAR_VALUES = 2000  # entries kept per vars[var_name] counts dict
TOP_K_FIELD_COUNTS = 5000  # entries kept in field_counts / unprocessed_counts
TOP_K_RECIPES = 2000  # entries kept per Vary recipe / marginal dict


def _merge_counts(target: Dict[str, int], source: Dict[str, int]) -> None:
    for key, count in source.items():
        target[key] = target.get(key, 0) + int(count)


def _merge_nested_counts(
    target: Dict[str, Dict[str, int]], source: Dict[str, Dict[str, int]]
) -> None:
    for var_name, counts in source.items():
        target.setdefault(var_name, {})
        _merge_counts(target[var_name], counts)


def sample_key(sample: Dict[str, Any]) -> str:
    """Return the dedup key for a sample. Prefer site; fall back to URL."""
    key = sample.get("site")
    if key:
        return str(key)
    return str(sample.get("url", ""))


def _merge_samples(
    target: List[Dict[str, Any]],
    source: List[Dict[str, Any]],
    limit: int,
) -> None:
    existing = {sample_key(sample) for sample in target}
    for sample in source:
        key = sample_key(sample)
        if not key or key in existing:
            continue
        if len(target) >= limit:
            return
        target.append(sample)
        existing.add(key)


def _merge_var_samples(
    target: Dict[str, Dict[str, List[Dict[str, Any]]]],
    source: Dict[str, Dict[str, List[Dict[str, Any]]]],
    limit: int,
) -> None:
    for var_name, by_value in source.items():
        target.setdefault(var_name, {})
        for val_str, samples in by_value.items():
            target[var_name].setdefault(val_str, [])
            _merge_samples(target[var_name][val_str], samples, limit)


def merge_note(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    target["count"] = target.get("count", 0) + int(source.get("count", 0))
    target.setdefault("samples", [])
    _merge_samples(target["samples"], source.get("samples", []), SAMPLE_LIMIT)
    target.setdefault("vars", {})
    _merge_nested_counts(target["vars"], source.get("vars", {}))
    if "var_samples" in source:
        target.setdefault("var_samples", {})
        _merge_var_samples(
            target["var_samples"], source["var_samples"], VAR_SAMPLE_LIMIT
        )
    source_truncated = source.get("truncated_vars") or {}
    if source_truncated:
        merged_truncated = target.setdefault("truncated_vars", {})
        for var_name, was_trunc in source_truncated.items():
            if was_trunc:
                merged_truncated[var_name] = True
    src_hll = source.get("sites_hll")
    if src_hll:
        if "sites_hll" not in target:
            target["sites_hll"] = list(src_hll)
        else:
            hll_merge(target["sites_hll"], src_hll)
    src_maxes = source.get("numeric_maxes")
    if src_maxes:
        _merge_numeric_maxes(target.setdefault("numeric_maxes", {}), src_maxes)


def _merge_numeric_maxes(
    target: Dict[str, Dict[str, int]], source: Dict[str, Dict[str, int]]
) -> None:
    """Per-(var_name, key) take the max. Used for FIELD_TOO_LARGE field_size."""
    for var_name, per_key in source.items():
        target_per_key = target.setdefault(var_name, {})
        for key, value in per_key.items():
            prev = target_per_key.get(key, 0)
            if int(value) > prev:
                target_per_key[key] = int(value)


def merge_stats_dict(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """Merge a serialized StatsCollector dict into ``target`` in place.

    Used by the EMR mapper to fold each WARC's worker-side StatsCollector
    snapshot into the mapper's running running dict. Must cover every
    field that ``StatsCollector.to_dict`` produces -- anything missed here
    is silently dropped at mapper-aggregation time.
    """
    target["total_responses"] = target.get("total_responses", 0) + int(
        source.get("total_responses", 0)
    )
    target.setdefault("field_counts", {})
    _merge_counts(target["field_counts"], source.get("field_counts", {}))
    target.setdefault("unprocessed_counts", {})
    _merge_counts(target["unprocessed_counts"], source.get("unprocessed_counts", {}))
    target.setdefault("severity_counts", {})
    _merge_counts(target["severity_counts"], source.get("severity_counts", {}))
    target.setdefault("notes", {})
    for note_id, note in source.get("notes", {}).items():
        target["notes"].setdefault(note_id, {"count": 0, "samples": [], "vars": {}})
        merge_note(target["notes"][note_id], note)
    src_hll = source.get("sites_hll")
    if src_hll:
        if "sites_hll" not in target:
            target["sites_hll"] = list(src_hll)
        else:
            hll_merge(target["sites_hll"], src_hll)
    src_csp = source.get("csp_max_by_site")
    if src_csp:
        target.setdefault("csp_max_by_site", {})
        merge_csp_sizes(target["csp_max_by_site"], src_csp)
    src_vary = source.get("vary")
    if src_vary:
        merge_vary(target.setdefault("vary", {}), src_vary)


GLOBALS_KEY = "globals"
NOTE_KEY_PREFIX = "note:"
CSP_SIZES_KEY = "csp_sizes"
VARY_KEY = "vary"

# Defensive cap on the per-site CSP-size dict. The dict naturally bounds at
# the cardinality of distinct sites the mapper saw (~ TOP_N in practice),
# but with no top-sites filter it could grow unbounded; trim at this cap so
# a runaway response set can't blow up the shuffle.
TOP_K_CSP_SITES = 100000

# Hadoop limits the number of distinct counter names (default 120). Bucket
# child-process failures into a fixed cardinality set instead of emitting one
# counter per observed exit code / signal number.
_SIGKILL = 9
_SIGSEGV = 11
_SIGTERM = 15
_SIGBUS = 10


_SIGNAL_BUCKETS = {
    _SIGKILL: "warc_signal_sigkill",
    _SIGSEGV: "warc_signal_sigsegv",
    _SIGBUS: "warc_signal_sigbus",
    _SIGTERM: "warc_signal_sigterm",
}


def _failure_bucket(exit_code: Optional[int]) -> str:
    if exit_code is None:
        return "warc_exit_unknown"
    if exit_code == 0:
        return "warc_exit_zero"
    if exit_code > 0:
        return "warc_exit_nonzero"
    return _SIGNAL_BUCKETS.get(-exit_code, "warc_signal_other")


def merge_globals(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    target["total_responses"] = target.get("total_responses", 0) + int(
        source.get("total_responses", 0)
    )
    target.setdefault("field_counts", {})
    _merge_counts(target["field_counts"], source.get("field_counts", {}))
    target.setdefault("unprocessed_counts", {})
    _merge_counts(target["unprocessed_counts"], source.get("unprocessed_counts", {}))
    target.setdefault("severity_counts", {})
    _merge_counts(target["severity_counts"], source.get("severity_counts", {}))
    for flag in ("truncated_field_counts", "truncated_unprocessed_counts"):
        if source.get(flag):
            target[flag] = True
    src_hll = source.get("sites_hll")
    if src_hll:
        if "sites_hll" not in target:
            target["sites_hll"] = list(src_hll)
        else:
            hll_merge(target["sites_hll"], src_hll)
    # run_context is identical across every mapper in a single job. Take the
    # first one we see and stick with it.
    if "run_context" not in target and source.get("run_context"):
        target["run_context"] = source["run_context"]


def merge_csp_sizes(target: Dict[str, int], source: Dict[str, int]) -> None:
    """Merge a per-site CSP-size dict into ``target`` keeping the max per site.

    A site appears in many WATs (and may serve different CSP values from
    different URLs). The histogram counts each site once at the largest CSP
    byte size it ever served, so reducer merges take the max rather than
    sum.
    """
    for site, size in source.items():
        prev = target.get(site)
        size_int = int(size)
        if prev is None or size_int > prev:
            target[site] = size_int


def _trim_csp_sizes(csp_sizes: Dict[str, int]) -> Dict[str, int]:
    """Cap the CSP-size dict to TOP_K_CSP_SITES sites by size (desc).

    Bounded already by Tranco --top-sites in practice; this is a defensive
    safety net for runs without that gate. Sites with size 0 (seen, no CSP)
    sort to the bottom and are the first dropped if we hit the cap.
    """
    if len(csp_sizes) <= TOP_K_CSP_SITES:
        return csp_sizes
    top_items = sorted(csp_sizes.items(), key=lambda kv: kv[1], reverse=True)[
        :TOP_K_CSP_SITES
    ]
    return dict(top_items)


def _trim_note(note: Dict[str, Any]) -> Dict[str, Any]:
    """Trim a single per-note dict in place."""
    wrapper = {"notes": {"_": note}}
    trim_stats_dict(wrapper)
    return note


def _trim_globals(globals_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Trim a globals dict (scalars + header histograms) in place."""
    trim_stats_dict(globals_dict)  # ignores 'notes' if absent
    return globals_dict


def _trim_counts(counts: Dict[str, int], top_k: int) -> Tuple[Dict[str, int], bool]:
    """Return (trimmed_dict, was_truncated)."""
    if len(counts) <= top_k:
        return counts, False
    top_items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return dict(top_items), True


def trim_stats_dict(stats: Dict[str, Any]) -> Dict[str, Any]:
    """Cap long-tail dicts before emit/output.

    Trims field_counts, unprocessed_counts, and every note's vars/var_samples
    to the configured top-K. Mutates ``stats`` in place and returns it for
    convenience.

    Per-note vars are trimmed by occurrence count, and var_samples are pruned
    so they only retain entries for vals that survived the vars trim. Where
    trimming actually dropped entries, sets a sticky truncation flag so the
    finalizer / report can footnote affected notes:

    - stats["truncated_field_counts"] / ["truncated_unprocessed_counts"]
      become True if those header histograms had to be trimmed.
    - note["truncated_vars"][var_name] becomes True for each var dict that
      had to be trimmed.

    Flags are sticky: once set, subsequent merges that don't trigger
    truncation still carry the True forward via OR semantics.
    """
    if "field_counts" in stats:
        stats["field_counts"], was_trunc = _trim_counts(
            stats["field_counts"], TOP_K_FIELD_COUNTS
        )
        if was_trunc:
            stats["truncated_field_counts"] = True
    if "unprocessed_counts" in stats:
        stats["unprocessed_counts"], was_trunc = _trim_counts(
            stats["unprocessed_counts"], TOP_K_FIELD_COUNTS
        )
        if was_trunc:
            stats["truncated_unprocessed_counts"] = True
    if stats.get("vary"):
        trim_vary(stats["vary"], TOP_K_RECIPES)
    for note in stats.get("notes", {}).values():
        var_counts = note.get("vars", {})
        retained_vals_per_var: Dict[str, set[str]] = {}
        for var_name, counts in list(var_counts.items()):
            trimmed, was_trunc = _trim_counts(counts, TOP_K_VAR_VALUES)
            var_counts[var_name] = trimmed
            retained_vals_per_var[var_name] = set(trimmed.keys())
            if was_trunc:
                note.setdefault("truncated_vars", {})[var_name] = True
        var_samples = note.get("var_samples")
        if var_samples:
            for var_name, by_val in list(var_samples.items()):
                keep = retained_vals_per_var.get(var_name, set())
                if not keep:
                    var_samples.pop(var_name, None)
                    continue
                for val_str in list(by_val.keys()):
                    if val_str not in keep:
                        by_val.pop(val_str, None)
    return stats


class CCLintJob(MRJob):  # type: ignore[misc]

    OUTPUT_PROTOCOL = JSONProtocol
    INTERNAL_PROTOCOL = JSONProtocol

    def configure_args(self) -> None:
        super().configure_args()
        self.add_passthru_arg(
            "--record-limit",
            type=int,
            default=0,
            help="Max records to process per WARC file (0 for all)",
        )
        self.add_passthru_arg(
            "--limit",
            type=int,
            default=0,
            help="Max WARC files to process per mapper (0 for all)",
        )
        self.add_passthru_arg(
            "--top-sites",
            type=int,
            default=0,
            help="Filter records to the top N sites (0 disables filtering)",
        )
        self.add_passthru_arg(
            "--sample-top-sites",
            type=int,
            default=10000,
            help=(
                "Only collect samples (URL lists) from sites within the top N "
                "Tranco ranks (default 10000, 0 disables the sample ceiling). "
                "Aggregate counts are not affected; this only restricts which "
                "responses appear in the published sample lists."
            ),
        )
        self.add_passthru_arg(
            "--tranco-path",
            default=None,
            help="Path to the Tranco top-sites CSV (sent to mappers via --files)",
        )
        self.add_passthru_arg(
            "--crawl-id",
            default="",
            help="Common Crawl release id (e.g. CC-MAIN-2026-12) recorded in the report",
        )
        self.add_passthru_arg(
            "--warc-timeout",
            type=int,
            default=900,
            help=(
                "Wall-clock seconds per WARC before the child worker is "
                "terminated and the WARC is skipped (default 900 = 15 min)"
            ),
        )

    def mapper_init(self) -> None:
        init_start = time.perf_counter()
        try:
            sys.stderr.write("*" * 50 + "\n")
            sys.stderr.write("DEBUG: mapper_init starting (Python OK)\n")
            self.stats = StatsCollector()
            self.top_sites: Optional[Set[str]] = None
            self.sample_sites: Optional[Set[str]] = None
            if self.options.tranco_path:
                if self.options.top_sites:
                    self.top_sites = load_top_sites(
                        self.options.tranco_path, self.options.top_sites
                    )
                if self.options.sample_top_sites:
                    sample_limit = self.options.sample_top_sites
                    # The sample ceiling never exceeds the response ceiling:
                    # sampling from sites we don't even process is meaningless.
                    if self.options.top_sites:
                        sample_limit = min(sample_limit, self.options.top_sites)
                    self.sample_sites = load_top_sites(
                        self.options.tranco_path, sample_limit
                    )
            self.run_context = _build_run_context(self.options)
            self.warcs_seen = 0
            # Random per-mapper sleep applied before the first S3 download
            # to break up the simultaneous mapper-startup burst. cc-feeds
            # parity (cc_lint is smaller-per-download but the burst pattern
            # is the same).
            self._s3_jitter = random.uniform(0, 30)
            init_ms = int((time.perf_counter() - init_start) * 1000)
            self.increment_counter("timing", "mapper_init_ms", init_ms)
            sys.stderr.write(
                f"DEBUG: mapper_init finished successfully in {init_ms}ms\n"
            )
            sys.stderr.flush()
        except Exception as exc:
            sys.stderr.write(f"FATAL: mapper_init failed: {exc}\n")
            sys.stderr.write(traceback.format_exc())
            sys.stderr.flush()
            raise

    def mapper(self, key: Any, value: Any) -> Iterator[Tuple[str, Any]]:
        raw_path = str(key or value or "").strip()
        if not raw_path or raw_path.startswith("#"):
            return

        if self.options.limit and self.warcs_seen >= self.options.limit:
            return
        self.warcs_seen += 1

        try:
            sys.stderr.write(f"INFO: starting WARC {self.warcs_seen}: {raw_path}\n")
            sys.stderr.flush()
            if self.warcs_seen == 1 and self._s3_jitter > 0:
                sys.stderr.write(f"INFO: jitter sleep {self._s3_jitter:.1f}s\n")
                sys.stderr.flush()
                time.sleep(self._s3_jitter)
                self.increment_counter(
                    "timing", "jitter_ms", int(self._s3_jitter * 1000)
                )
            self.set_status(f"Downloading WARC {self.warcs_seen}: {raw_path}")
            result = self._process_warc_in_child(raw_path)
            if result is None:
                return

            self._merge_worker_result(result)
            sys.stderr.write(
                "INFO: finished WARC "
                f"{self.warcs_seen}: {raw_path} | "
                f"records={result.records_seen} "
                f"total_ms={result.total_ms} "
                f"process_ms={result.process_ms} "
                f"iterator_download_ms={result.iterator_ms}\n"
            )
            sys.stderr.flush()
        except Exception as exc:
            sys.stderr.write(f"ERROR processing {raw_path}: {exc}\n")
            sys.stderr.write(traceback.format_exc())
            sys.stderr.flush()
        # Mapper emits via mapper_final; this stub keeps mrjob happy that the
        # method is a generator without producing intermediate output.
        yield from ()

    def _process_warc_in_child(self, raw_path: str) -> Optional[WarcWorkerResult]:
        with tempfile.NamedTemporaryFile(delete=True) as result_file:
            context = get_context("fork")
            process = context.Process(
                target=process_warc_to_file,
                args=(
                    raw_path,
                    result_file.name,
                    self.options.record_limit,
                    self.top_sites,
                    self.sample_sites,
                ),
            )
            process.start()
            deadline = time.monotonic() + self.options.warc_timeout
            while process.is_alive():
                process.join(timeout=30)
                if not process.is_alive():
                    break
                if time.monotonic() >= deadline:
                    sys.stderr.write(
                        "ERROR: timeout WARC "
                        f"{self.warcs_seen}: {raw_path} after "
                        f"{self.options.warc_timeout}s; terminating child\n"
                    )
                    sys.stderr.flush()
                    process.terminate()
                    process.join(timeout=10)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=5)
                    self.increment_counter("status", "warcs_failed", 1)
                    self.increment_counter("status", "warc_timed_out", 1)
                    return None
                self.set_status(f"Downloading WARC {self.warcs_seen}: {raw_path}")
                sys.stderr.write(
                    f"INFO: still downloading WARC {self.warcs_seen}: " f"{raw_path}\n"
                )
                sys.stderr.flush()

            if process.exitcode != 0:
                self._record_warc_failure(raw_path, process.exitcode)
                return None

            try:
                return load_warc_worker_result(result_file.name)
            except Exception as exc:
                sys.stderr.write(
                    "ERROR: failed WARC "
                    f"{self.warcs_seen}: {raw_path} | result_read_error={exc}\n"
                )
                sys.stderr.write(traceback.format_exc())
                sys.stderr.flush()
                self.increment_counter("status", "warcs_failed", 1)
                self.increment_counter("status", "warc_result_read_error", 1)
                return None

    def _merge_worker_result(self, result: WarcWorkerResult) -> None:
        merge_stats_dict(self._stats_dict(), result.stats.to_dict())
        self.increment_counter("status", "records_processed", result.records_seen)
        self.increment_counter("timing", "warc_total_ms", result.total_ms)
        self.increment_counter("timing", "record_process_ms", result.process_ms)
        self.increment_counter("timing", "iterator_download_ms", result.iterator_ms)
        self.increment_counter("timing", "warcs_completed", 1)
        self.increment_counter("timing", "records_seen", result.records_seen)

    def _stats_dict(self) -> Dict[str, Any]:
        # Maintain a single accumulating dict per mapper so we never have to
        # re-serialize the StatsCollector. We swap the live StatsCollector
        # for its dict form on first use.
        if not hasattr(self, "_running_dict"):
            self._running_dict: Dict[str, Any] = self.stats.to_dict()
        return self._running_dict

    def _record_warc_failure(self, raw_path: str, exit_code: Optional[int]) -> None:
        exit_label = "unknown" if exit_code is None else str(exit_code)
        signal_label = ""
        bucket = _failure_bucket(exit_code)
        if exit_code is not None and exit_code < 0:
            signal_label = f" signal={-exit_code}"
        self.increment_counter("status", bucket, 1)
        self.increment_counter("status", "warcs_failed", 1)
        sys.stderr.write(
            "ERROR: failed WARC "
            f"{self.warcs_seen}: {raw_path} | "
            f"exit_code={exit_label}{signal_label} bucket={bucket} "
            "child process did not produce results\n"
        )
        sys.stderr.flush()

    def mapper_final(self) -> Generator[Tuple[str, Any], None, None]:
        stats = trim_stats_dict(self._stats_dict())
        globals_payload: Dict[str, Any] = {
            "total_responses": stats.get("total_responses", 0),
            "field_counts": stats.get("field_counts", {}),
            "unprocessed_counts": stats.get("unprocessed_counts", {}),
            "severity_counts": stats.get("severity_counts", {}),
            "run_context": self.run_context,
        }
        if stats.get("sites_hll"):
            globals_payload["sites_hll"] = stats["sites_hll"]
        for flag in ("truncated_field_counts", "truncated_unprocessed_counts"):
            if stats.get(flag):
                globals_payload[flag] = True
        yield GLOBALS_KEY, globals_payload
        for note_id, note in stats.get("notes", {}).items():
            yield NOTE_KEY_PREFIX + note_id, note
        csp_sizes = stats.get("csp_max_by_site") or {}
        if csp_sizes:
            yield CSP_SIZES_KEY, _trim_csp_sizes(csp_sizes)
        vary = stats.get("vary") or {}
        if vary:
            yield VARY_KEY, vary

    # No combiner: mapper_final emits each (key, value) exactly once per
    # mapper, so there is nothing for a combiner to fold. The mrjob default
    # round-trips JSON for no benefit, so we override nothing here.

    def reducer(
        self, key: str, values: Generator[Any, None, None]
    ) -> Generator[Tuple[str, Any], None, None]:
        if key == GLOBALS_KEY:
            merged: Dict[str, Any] = {}
            for value in values:
                merge_globals(merged, value)
            yield GLOBALS_KEY, _trim_globals(merged)
        elif key.startswith(NOTE_KEY_PREFIX):
            merged_note: Dict[str, Any] = {"count": 0, "samples": [], "vars": {}}
            for value in values:
                merge_note(merged_note, value)
            yield key, _trim_note(merged_note)
        elif key == CSP_SIZES_KEY:
            merged_csp: Dict[str, int] = {}
            for value in values:
                merge_csp_sizes(merged_csp, value)
            yield CSP_SIZES_KEY, _trim_csp_sizes(merged_csp)
        elif key == VARY_KEY:
            merged_vary: Dict[str, Any] = {}
            for value in values:
                merge_vary(merged_vary, value)
            trim_vary(merged_vary, TOP_K_RECIPES)
            yield VARY_KEY, merged_vary


def main() -> None:
    CCLintJob.run()


if __name__ == "__main__":
    main()
