# pylint: disable=abstract-method
import sys
import shlex
import types
import logging

# Monkeypatch pipes for Python 3.13 (mrjob compatibility)
if sys.version_info >= (3, 13):
    if "pipes" not in sys.modules:
        pipes = types.ModuleType("pipes")
        pipes.quote = shlex.quote
        sys.modules["pipes"] = pipes

# pylint: disable=wrong-import-position
from mrjob.job import MRJob
from mrjob.protocol import TextProtocol, JSONProtocol
from httplint.note import levels
from httplint.field.finder import UnknownHttpField

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


def _merge_counts(target, source):
    """
    Helper to merge counts dictionaries.
    """
    for key, count in source.items():
        if key not in target:
            target[key] = 0
        target[key] += count


def _merge_nested_stats(target, source):
    """
    Helper to merge nested stats {name: {val: count}}.
    """
    for name, counts in source.items():
        if name not in target:
            target[name] = {}
        _merge_counts(target[name], counts)


def _merge_samples(target, source, limit=15):
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


class CCLintJob(MRJob):

    # Input Protocol: We read paths (lines of text).
    # Output Protocol: We output JSON stats (NoteID, count).
    INPUT_PROTOCOL = TextProtocol
    OUTPUT_PROTOCOL = JSONProtocol

    def configure_args(self):
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

    def _process_record(self, record):
        """
        Lint a single record and yield intermediate results.
        """
        linter = lint_record(record)
        if not linter:
            return

        for note in linter.notes:
            if note.level not in [levels.WARN, levels.BAD]:
                continue
            # Using Note class name as ID
            note_id = note.__class__.__name__

            # Var stats
            var_stats = {}
            for var_name, val_str in iter_tracked_vars(note):
                if var_name not in var_stats:
                    var_stats[var_name] = {}
                var_stats[var_name][val_str] = 1

            # Variable samples
            var_samples = {}
            for var_name, val_str, sample in iter_collected_samples(note, linter):
                if var_name not in var_samples:
                    var_samples[var_name] = {}
                if val_str not in var_samples[var_name]:
                    var_samples[var_name][val_str] = []
                var_samples[var_name][val_str].append(sample)

            # yield dictionary with count and sample url
            sample = create_sample(note, linter)
            samples = [sample] if sample else []

            yield (
                note_id,
                {
                    "c": 1,
                    "s": samples,
                    "v": var_stats,
                    "vs": var_samples,
                },
            )

        yield ("_TOTAL_RESPONSES", {"c": 1, "s": [], "v": {}})

        # Yield field counts
        self._process_field_counts(linter)

    def _process_field_counts(self, linter):
        """
        Extract field counts and valid/invalid header counts from linter.
        """
        field_counts = {}
        unprocessed_counts = {}

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
                    "c": 0,
                    "s": [],
                    "v": {},
                    "fields": field_counts,
                    "unprocessed": unprocessed_counts,
                },
            )

    # pylint: disable=abstract-method
    def mapper(self, _, value):
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
                    yield ("_ERROR_LINTING", {"c": 1, "s": [], "v": {}})

        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Error streaming %s: %s", warc_path, exc)
            yield ("_ERROR_STREAMING", {"c": 1, "s": [], "v": {}})

    def combiner(self, key, values):
        """
        Combiner: Sums counts locally and keeps up to 5 sample URLs.
        """
        yield (key, self._aggregate_values(values, sample_limit=5))

    def reducer(self, key, values):
        """
        Reducer: Sums counts globally and keeps up to 5 sample URLs.
        """
        agg = self._aggregate_values(values, sample_limit=5)

        if key.startswith("_"):
            if key == "_FIELD_COUNTS":
                yield ("field_counts", agg.get("fields", {}))
                yield ("unprocessed_counts", agg.get("unprocessed", {}))
            elif key == "_TOTAL_RESPONSES":
                yield ("total_responses", agg["c"])
            else:
                yield (key, agg["c"])
        else:
            yield (
                key,
                {
                    "count": agg["c"],
                    "samples": agg["s"],
                    "vars": agg["v"],
                    "var_samples": agg["vs"],
                },
            )

    def _aggregate_values(self, values, sample_limit=5):
        """
        Aggregate a stream of value dicts.
        """
        total_count = 0
        samples = []
        var_stats = {}
        var_samples = {}
        field_counts = {}
        unprocessed_counts = {}

        for val in values:
            total_count += val["c"]

            # Merge Samples
            existing_urls = {x["url"] for x in samples}
            for sample_obj in val.get("s", []):
                if sample_obj["url"] not in existing_urls and len(samples) < sample_limit:
                    samples.append(sample_obj)
                    existing_urls.add(sample_obj["url"])

            # Merge Stats
            _merge_nested_stats(var_stats, val.get("v", {}))
            _merge_samples(var_samples, val.get("vs", {}), limit=15)
            _merge_counts(field_counts, val.get("fields", {}))
            _merge_counts(unprocessed_counts, val.get("unprocessed", {}))

        out = {"c": total_count, "s": samples, "v": var_stats, "vs": var_samples}
        if field_counts:
            out["fields"] = field_counts
        if unprocessed_counts:
            out["unprocessed"] = unprocessed_counts
        return out


if __name__ == "__main__":
    CCLintJob.run()
