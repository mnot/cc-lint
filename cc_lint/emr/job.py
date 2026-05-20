"""mrjob entry point for the cc-lint EMR pipeline.

Each mapper reads a chunk of Common Crawl WARC paths (one per input
line), spawns a fork-isolated child for each path that downloads and
processes the corresponding WAT file, then merges the child's
StatsCollector into the mapper's running totals. ``mapper_final`` emits
the serialized StatsCollector once per mapper; the reducer merges all
mapper outputs into a single summary record.
"""

# pylint: disable=abstract-method,attribute-defined-outside-init,broad-exception-caught

import sys
import tempfile
import time
import traceback
from multiprocessing import get_context
from typing import Any, Dict, Generator, Iterator, List, Optional, Set, Tuple

from cc_lint.emr.compat import install_mrjob_pipes_compat

install_mrjob_pipes_compat()

# pylint: disable=wrong-import-position,wrong-import-order,ungrouped-imports
from mrjob.job import MRJob
from mrjob.protocol import JSONProtocol

from cc_lint.emr.warc_worker import (
    WarcWorkerResult,
    load_warc_worker_result,
    process_warc_to_file,
)
from cc_lint.stats import StatsCollector
from cc_lint.top_sites import load_top_sites


SAMPLE_LIMIT = 5
VAR_SAMPLE_LIMIT = 15

# Long-tail caps applied before shuffle and again at reducer output. The web
# generates extremely long-tailed value distributions for things like
# UNKNOWN_VALUE.value and STRUCTURED_FIELD_PARSE_ERROR.field_error; keeping
# the top entries by occurrence preserves the report signal while bounding
# the shuffle and final report size.
TOP_K_VAR_VALUES = 2000  # entries kept per vars[var_name] counts dict
TOP_K_FIELD_COUNTS = 5000  # entries kept in field_counts / unprocessed_counts


sys.stderr.write("DEBUG: Python interpreter started successfully\n")
sys.stderr.flush()


def _merge_counts(target: Dict[str, int], source: Dict[str, int]) -> None:
    for key, count in source.items():
        target[key] = target.get(key, 0) + int(count)


def _merge_nested_counts(
    target: Dict[str, Dict[str, int]], source: Dict[str, Dict[str, int]]
) -> None:
    for var_name, counts in source.items():
        target.setdefault(var_name, {})
        _merge_counts(target[var_name], counts)


def _merge_samples(
    target: List[Dict[str, Any]],
    source: List[Dict[str, Any]],
    limit: int,
) -> None:
    existing_urls = {sample["url"] for sample in target}
    for sample in source:
        if sample["url"] in existing_urls:
            continue
        if len(target) >= limit:
            return
        target.append(sample)
        existing_urls.add(sample["url"])


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


def _merge_note(target: Dict[str, Any], source: Dict[str, Any]) -> None:
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


def merge_stats_dict(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """Merge a serialized StatsCollector dict into ``target`` in place."""
    target["total_responses"] = target.get("total_responses", 0) + int(
        source.get("total_responses", 0)
    )
    target.setdefault("field_counts", {})
    _merge_counts(target["field_counts"], source.get("field_counts", {}))
    target.setdefault("unprocessed_counts", {})
    _merge_counts(target["unprocessed_counts"], source.get("unprocessed_counts", {}))
    target.setdefault("notes", {})
    for note_id, note in source.get("notes", {}).items():
        target["notes"].setdefault(
            note_id, {"count": 0, "samples": [], "vars": {}}
        )
        _merge_note(target["notes"][note_id], note)


GLOBALS_KEY = "globals"
NOTE_KEY_PREFIX = "note:"


def _merge_globals(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    target["total_responses"] = target.get("total_responses", 0) + int(
        source.get("total_responses", 0)
    )
    target.setdefault("field_counts", {})
    _merge_counts(target["field_counts"], source.get("field_counts", {}))
    target.setdefault("unprocessed_counts", {})
    _merge_counts(target["unprocessed_counts"], source.get("unprocessed_counts", {}))


def _trim_note(note: Dict[str, Any]) -> Dict[str, Any]:
    """Trim a single per-note dict in place."""
    wrapper = {"notes": {"_": note}}
    trim_stats_dict(wrapper)
    return note


def _trim_globals(globals_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Trim a globals dict (scalars + header histograms) in place."""
    trim_stats_dict(globals_dict)  # ignores 'notes' if absent
    return globals_dict


def _trim_counts(counts: Dict[str, int], top_k: int) -> Dict[str, int]:
    if len(counts) <= top_k:
        return counts
    top_items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return dict(top_items)


def trim_stats_dict(stats: Dict[str, Any]) -> Dict[str, Any]:
    """Cap long-tail dicts before emit/output.

    Trims field_counts, unprocessed_counts, and every note's vars/var_samples
    to the configured top-K. Mutates ``stats`` in place and returns it for
    convenience.

    Per-note vars are trimmed by occurrence count, and var_samples are pruned
    so they only retain entries for vals that survived the vars trim. This
    keeps the shuffle bounded and the report focused on signal, not noise.
    """
    if "field_counts" in stats:
        stats["field_counts"] = _trim_counts(stats["field_counts"], TOP_K_FIELD_COUNTS)
    if "unprocessed_counts" in stats:
        stats["unprocessed_counts"] = _trim_counts(
            stats["unprocessed_counts"], TOP_K_FIELD_COUNTS
        )
    for note in stats.get("notes", {}).values():
        var_counts = note.get("vars", {})
        retained_vals_per_var: Dict[str, set[str]] = {}
        for var_name, counts in list(var_counts.items()):
            trimmed = _trim_counts(counts, TOP_K_VAR_VALUES)
            var_counts[var_name] = trimmed
            retained_vals_per_var[var_name] = set(trimmed.keys())
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
            "--tranco-path",
            default=None,
            help="Path to the Tranco top-sites CSV (sent to mappers via --files)",
        )

    def mapper_init(self) -> None:
        init_start = time.perf_counter()
        try:
            sys.stderr.write("*" * 50 + "\n")
            sys.stderr.write("DEBUG: mapper_init starting\n")
            self.stats = StatsCollector()
            self.top_sites: Optional[Set[str]] = None
            if self.options.top_sites and self.options.tranco_path:
                self.top_sites = load_top_sites(
                    self.options.tranco_path, self.options.top_sites
                )
            self.warcs_seen = 0
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
            sys.stderr.write(
                f"INFO: starting WARC {self.warcs_seen}: {raw_path}\n"
            )
            sys.stderr.flush()
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
                ),
            )
            process.start()
            while process.is_alive():
                process.join(timeout=30)
                if process.is_alive():
                    self.set_status(
                        f"Downloading WARC {self.warcs_seen}: {raw_path}"
                    )
                    sys.stderr.write(
                        f"INFO: still downloading WARC {self.warcs_seen}: "
                        f"{raw_path}\n"
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
        self.increment_counter(
            "timing", "iterator_download_ms", result.iterator_ms
        )
        self.increment_counter("timing", "warcs_completed", 1)
        self.increment_counter("timing", "records_seen", result.records_seen)

    def _stats_dict(self) -> Dict[str, Any]:
        # Maintain a single accumulating dict per mapper so we never have to
        # re-serialize the StatsCollector. We swap the live StatsCollector
        # for its dict form on first use.
        if not hasattr(self, "_running_dict"):
            self._running_dict: Dict[str, Any] = self.stats.to_dict()
        return self._running_dict

    def _record_warc_failure(
        self, raw_path: str, exit_code: Optional[int]
    ) -> None:
        exit_label = "unknown" if exit_code is None else str(exit_code)
        signal_label = ""
        if exit_code is not None and exit_code < 0:
            signal_label = f" signal={-exit_code}"
            self.increment_counter("status", f"warc_signal_{-exit_code}", 1)
        elif exit_code is not None:
            self.increment_counter("status", f"warc_exit_{exit_code}", 1)
        self.increment_counter("status", "warcs_failed", 1)
        sys.stderr.write(
            "ERROR: failed WARC "
            f"{self.warcs_seen}: {raw_path} | "
            f"exit_code={exit_label}{signal_label} "
            "child process did not produce results\n"
        )
        sys.stderr.flush()

    def mapper_final(self) -> Generator[Tuple[str, Any], None, None]:
        stats = trim_stats_dict(self._stats_dict())
        yield GLOBALS_KEY, {
            "total_responses": stats.get("total_responses", 0),
            "field_counts": stats.get("field_counts", {}),
            "unprocessed_counts": stats.get("unprocessed_counts", {}),
        }
        for note_id, note in stats.get("notes", {}).items():
            yield NOTE_KEY_PREFIX + note_id, note

    def combiner(
        self, key: str, values: Generator[Any, None, None]
    ) -> Generator[Tuple[str, Any], None, None]:
        if key == GLOBALS_KEY:
            merged: Dict[str, Any] = {}
            for value in values:
                _merge_globals(merged, value)
            yield key, _trim_globals(merged)
        elif key.startswith(NOTE_KEY_PREFIX):
            merged_note: Dict[str, Any] = {"count": 0, "samples": [], "vars": {}}
            for value in values:
                _merge_note(merged_note, value)
            yield key, _trim_note(merged_note)

    def reducer(
        self, key: str, values: Generator[Any, None, None]
    ) -> Generator[Tuple[str, Any], None, None]:
        if key == GLOBALS_KEY:
            merged: Dict[str, Any] = {}
            for value in values:
                _merge_globals(merged, value)
            yield GLOBALS_KEY, _trim_globals(merged)
        elif key.startswith(NOTE_KEY_PREFIX):
            merged_note: Dict[str, Any] = {"count": 0, "samples": [], "vars": {}}
            for value in values:
                _merge_note(merged_note, value)
            yield key, _trim_note(merged_note)


def main() -> None:
    CCLintJob.run()


if __name__ == "__main__":
    main()
