
import logging

from typing import Iterator, Any, Dict, List, Set, Optional

import cc_lint.patches  # pylint: disable=wrong-import-order,unused-import
from mrjob.job import MRJob
from mrjob.protocol import TextProtocol, JSONProtocol
from httplint.note import levels
from httplint.field.finder import UnknownHttpField
from cc_lint.types import SampleType, MRJobAggregateType
from cc_lint.top_sites import load_top_sites, is_in_top_sites

from cc_lint.crawling import get_warc_stream, iter_warc_records
from cc_lint.linting import lint_record
from cc_lint.stats import (
    iter_tracked_vars,
    iter_collected_samples,
    create_sample,
)


# Configure logging to capture errors in MR logs
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


def _merge_counts(target: Dict[str, int], source: Dict[str, int]) -> None:
    """
    Helper to merge counts dictionaries.
    """
    for key, count in source.items():
        if key not in target:
            target[key] = 0
        target[key] += count


def _merge_nested_stats(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """
    Helper to merge nested stats {name: {val: count}}.
    """
    for name, counts in source.items():
        if name not in target:
            target[name] = {}
        _merge_counts(target[name], counts)


def _merge_samples(target: Dict[str, Any], source: Dict[str, Any], limit: int = 15) -> None:
    """
    Helper to merge sample lists {name: {val: [samples]}}.
    """
    for name, val_dict in source.items():
        if name not in target:
            target[name] = {}
        for val_str, s_list in val_dict.items():
            if val_str not in target[name]:
                target[name][val_str] = []

            # Merge unique URLs
            current = target[name][val_str]
            existing_urls = {x["url"] for x in current}
            for s_obj in s_list:
                if s_obj["url"] not in existing_urls and len(current) < limit:
                    current.append(s_obj)
                    existing_urls.add(s_obj["url"])


class CCLintJob(MRJob):  # pylint: disable=abstract-method

    # Input Protocol: We read paths (lines of text).
    # Output Protocol: We output JSON stats (NoteID, count).
    INPUT_PROTOCOL = TextProtocol
    OUTPUT_PROTOCOL = JSONProtocol

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.top_domains: Optional[Set[str]] = None

    def configure_args(self) -> None:
        super().configure_args()
        self.add_passthru_arg(
            "--use-s3",
            action="store_true",
            default=False,
            help="Use S3 for WARC access instead of HTTP",
        )
        self.add_passthru_arg(
            "--record-limit",
            type=int,
            default=0,
            help="Max records to process per WARC file",
        )
        self.add_passthru_arg(
            "--cache-dir", default=None, help="Directory to cache WARC files"
        )
        self.add_passthru_arg(
            "--top-sites", type=int, default=None, help="Filter to top N sites"
        )
        self.add_passthru_arg(
            "--tranco-path", default=None, help="Path to Tranco CSV"
        )

    def mapper_init(self) -> None:
        """
        Initialize mapper with top sites list if needed.
        """
        # self.top_domains initialized in __init__
        if self.options.top_sites and self.options.tranco_path:
            self.top_domains = load_top_sites(
                self.options.tranco_path, self.options.top_sites
            )

    def _process_record(self, record: Any) -> Iterator[tuple[str, Dict[str, Any]]]:
        """
        Lint a single record and yield intermediate results.
        """
        linter = lint_record(record)
        if not linter:
            return

        # Filter by top sites if enabled
        if self.top_domains is not None:
            if not is_in_top_sites(linter.base_uri, self.top_domains):
                return

        for note in linter.notes:
            if note.level not in [levels.WARN, levels.BAD]:
                continue
            # Using Note class name as ID
            note_id = note.__class__.__name__

            # Var stats
            var_stats: Dict[str, Dict[str, int]] = {}
            for var_name, val_str in iter_tracked_vars(note):
                if var_name not in var_stats:
                    var_stats[var_name] = {}
                var_stats[var_name][val_str] = 1

            # Variable samples
            var_samples: Dict[str, Dict[str, List[SampleType]]] = {}
            for var_name, val_str, sample in iter_collected_samples(note, linter):
                if var_name not in var_samples:
                    var_samples[var_name] = {}
                if val_str not in var_samples[var_name]:
                    var_samples[var_name][val_str] = []
                var_samples[var_name][val_str].append(sample)

            # yield dictionary with count and sample url
            note_sample = create_sample(note, linter)
            samples: List[SampleType] = []
            if note_sample:
                samples.append(note_sample)

            yield (
                note_id,
                {
                    "count": 1,
                    "samples": samples,
                    "vars": var_stats,
                    "var_samples": var_samples,
                },
            )

        yield ("_TOTAL_RESPONSES", {"count": 1, "samples": [], "vars": {}})

        # Yield field counts
        self._process_field_counts(linter)

    def _process_field_counts(self, linter: Any) -> Iterator[tuple[str, Dict[str, Any]]]:
        """
        Extract field counts and valid/invalid header counts from linter.
        """
        field_counts: Dict[str, int] = {}
        unprocessed_counts: Dict[str, int] = {}

        if hasattr(linter, "headers") and hasattr(linter.headers, "text"):
            # field counts
            for name, _value in linter.headers.text:
                if isinstance(name, bytes):
                    name_str = name.decode("latin1", errors="replace")
                else:
                    name_str = str(name)
                name_lower = name_str.lower()
                field_counts[name_lower] = field_counts.get(name_lower, 0) + 1

            # processed / unprocessed
            if hasattr(linter.headers, "handlers"):
                for name, handler in linter.headers.handlers.items():
                    if isinstance(handler, UnknownHttpField):
                        unprocessed_counts[name] = unprocessed_counts.get(name, 0) + 1

        if field_counts or unprocessed_counts:
            yield (
                "_FIELD_COUNTS",
                {
                    "count": 0,
                    "samples": [],
                    "vars": {},
                    "fields": field_counts,
                    "unprocessed": unprocessed_counts,
                },
            )


    def mapper(self, _: Any, value: Any) -> Iterator[tuple[str, Dict[str, Any]]]:
        """
        Mapper: Takes a WARC path, streams it, lints records, emits (NoteID, 1).
        """
        warc_path = str(value).strip() if value else None
        if not warc_path:
            return

        use_s3 = self.options.use_s3
        record_limit = self.options.record_limit
        cache_dir = self.options.cache_dir

        try:
            stream = get_warc_stream(warc_path, use_s3=use_s3, cache_dir=cache_dir)
            count = 0
            for record in iter_warc_records(stream):
                if 0 < record_limit <= count:
                    break
                count += 1

                try:
                    yield from self._process_record(record)
                except Exception as exc:  # pylint: disable=broad-except
                    # Log but continue
                    logger.warning("Error linting record in %s: %s", warc_path, exc)
                    yield ("_ERROR_LINTING", {"count": 1, "samples": [], "vars": {}})

        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Error streaming %s: %s", warc_path, exc)
            yield ("_ERROR_STREAMING", {"count": 1, "samples": [], "vars": {}})

    def combiner(self, key: str, values: Iterator[Any]) -> Iterator[tuple[str, Any]]:
        """
        Combiner: Sums counts locally and keeps up to 5 sample URLs.
        """
        yield (key, self._aggregate_values(values, sample_limit=5))

    def reducer(self, key: str, values: Iterator[Any]) -> Iterator[tuple[str, Any]]:
        """
        Reducer: Sums counts globally and keeps up to 5 sample URLs.
        """
        agg = self._aggregate_values(values, sample_limit=5)

        if key.startswith("_"):
            if key == "_FIELD_COUNTS":
                yield ("field_counts", agg.get("fields", {}))
                yield ("unprocessed_counts", agg.get("unprocessed", {}))
            elif key == "_TOTAL_RESPONSES":
                yield ("total_responses", agg["count"])
            else:
                yield (key, agg["count"])
        else:
            yield (
                key,
                {
                    "count": agg["count"],
                    "samples": agg["samples"],
                    "vars": agg["vars"],
                    "var_samples": agg["var_samples"],
                },
            )

    def _aggregate_values(
        self, values: Iterator[Dict[str, Any]], sample_limit: int = 5
    ) -> MRJobAggregateType:
        """
        Aggregate a stream of value dicts.
        """
        total_count = 0
        samples: List[SampleType] = []
        var_stats: Dict[str, Any] = {}
        var_samples: Dict[str, Any] = {}
        field_counts: Dict[str, int] = {}
        unprocessed_counts: Dict[str, int] = {}

        for val in values:
            total_count += val["count"]

            # Merge Samples
            existing_urls = {x["url"] for x in samples}
            for sample_obj in val.get("samples", []):
                if sample_obj["url"] not in existing_urls and len(samples) < sample_limit:
                    samples.append(sample_obj)
                    existing_urls.add(sample_obj["url"])

            # Merge Stats
            _merge_nested_stats(var_stats, val.get("vars", {}))
            _merge_samples(var_samples, val.get("var_samples", {}), limit=15)
            _merge_counts(field_counts, val.get("fields", {}))
            _merge_counts(unprocessed_counts, val.get("unprocessed", {}))

        out: MRJobAggregateType = {
            "count": total_count,
            "samples": samples,
            "vars": var_stats,
            "var_samples": var_samples,
        }
        if field_counts:
            out["fields"] = field_counts
        if unprocessed_counts:
            out["unprocessed"] = unprocessed_counts
        return out


if __name__ == "__main__":
    CCLintJob.run()
