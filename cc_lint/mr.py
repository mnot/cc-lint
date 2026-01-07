import sys
import shlex
import types

# Monkeypatch pipes for Python 3.13 (mrjob compatibility)
if sys.version_info >= (3, 13):
    if 'pipes' not in sys.modules:
        pipes = types.ModuleType('pipes')
        pipes.quote = shlex.quote
        sys.modules['pipes'] = pipes

from mrjob.job import MRJob
from mrjob.protocol import TextProtocol, JSONProtocol
import logging
from cc_lint.crawling import get_warc_stream, iter_warc_records
from cc_lint.linting import lint_record
from httplint.note import levels
from cc_lint.stats import VARS_TO_TRACK, SAMPLES_TO_COLLECT, iter_tracked_vars, iter_collected_samples, create_sample

from httplint.field.finder import UnknownHttpField

# Configure logging to capture errors in MR logs
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

class CCLintJob(MRJob):
    # Input Protocol: We read paths (lines of text).
    # Output Protocol: We output JSON stats (NoteID, count).
    INPUT_PROTOCOL = TextProtocol
    OUTPUT_PROTOCOL = JSONProtocol

    def configure_args(self):
        super(CCLintJob, self).configure_args()
        self.add_passthru_arg('--use-s3', action='store_true', default=False, help='Use S3 for WARC access instead of HTTP')
        self.add_passthru_arg('--record-limit', type=int, default=0, help='Max records to process per WARC file')
        self.add_passthru_arg('--cache-dir', default=None, help='Directory to cache WARC files')

    def mapper(self, key, warc_path):
        """
        Mapper: Takes a WARC path, streams it, lints records, emits (NoteID, 1).
        """
        # warc_path comes from the input file (e.g. sample_paths.txt)
        if warc_path is None:
            # Maybe the key has the info?
            if key is not None and isinstance(key, str):
                warc_path = key
            else:
                return

        warc_path = str(warc_path).strip()
        if not warc_path:
            return

        use_s3 = self.options.use_s3
        record_limit = self.options.record_limit
        cache_dir = self.options.cache_dir
        
        try:
            stream = get_warc_stream(warc_path, use_s3=use_s3, cache_dir=cache_dir)
            count = 0
            for record in iter_warc_records(stream):
                if record_limit > 0 and count >= record_limit:
                    break
                count += 1
                
                try:
                    linter = lint_record(record)
                    if linter:
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
                            if sample:
                                samples = [sample]
                            else:
                                samples = []
                            yield (note_id, {'c': 1, 's': samples, 'v': var_stats, 'vs': var_samples})
                        yield ("_TOTAL_RESPONSES", {'c': 1, 's': [], 'v': {}})
                        
                        # Yield field counts
                        field_counts = {}
                        unprocessed_counts = {}
                        if hasattr(linter, 'headers') and hasattr(linter.headers, 'text'):
                            # field counts
                            for name, value in linter.headers.text:
                                if isinstance(name, bytes):
                                    name_str = name.decode('latin1', errors='replace')
                                else:
                                    name_str = str(name)
                                name_lower = name_str.lower()
                                if name_lower not in field_counts:
                                    field_counts[name_lower] = 0
                                field_counts[name_lower] += 1
                            
                            # processed / unprocessed
                            # linter.headers.handlers keys are names
                            if hasattr(linter.headers, 'handlers'):
                                for name, handler in linter.headers.handlers.items():
                                    if isinstance(handler, UnknownHttpField):
                                        # name is already lower case in handlers
                                        if name not in unprocessed_counts:
                                            unprocessed_counts[name] = 0
                                        unprocessed_counts[name] += 1
                                    
                        yield ("_FIELD_COUNTS", {'c': 0, 's': [], 'v': {}, 'fields': field_counts, 'unprocessed': unprocessed_counts})

                except Exception as e:
                    # Log but continue
                    logger.warning(f"Error linting record in {warc_path}: {e}")
                    yield ("_ERROR_LINTING", {'c': 1, 's': [], 'v': {}})
                    
        except Exception as e:
            logger.error(f"Error streaming {warc_path}: {e}")
            yield ("_ERROR_STREAMING", {'c': 1, 's': [], 'v': {}})

    def combiner(self, note_id, values):
        """
        Combiner: Sums counts locally and keeps up to 5 sample URLs.
        """
        total_count = 0
        samples = []
        var_stats = {} # {var_name: {val_str: count}}
        var_samples = {} # {var_name: {val_str: [samples]}}
        field_counts = {}
        unprocessed_counts = {}

        # optimization: verify if we are merging lists of dicts
        for v in values:
            total_count += v['c']
            for s in v.get('s', []):
                # s is now a dict {'url': ..., 'vars': ...}
                # Check uniqueness by URL
                existing_urls = [x['url'] for x in samples]
                if s['url'] not in existing_urls and len(samples) < 5:
                    samples.append(s)
            
            # Merge var stats
            v_stats = v.get('v', {})
            for var_name, counts in v_stats.items():
                if var_name not in var_stats:
                    var_stats[var_name] = {}
                for val_str, count in counts.items():
                    if val_str not in var_stats[var_name]:
                        var_stats[var_name][val_str] = 0
                    var_stats[var_name][val_str] += count

            # Merge var samples
            v_samples = v.get('vs', {})
            for var_name, val_dict in v_samples.items():
                if var_name not in var_samples:
                    var_samples[var_name] = {}
                for val_str, s_list in val_dict.items():
                     if val_str not in var_samples[var_name]:
                         var_samples[var_name][val_str] = []
                     # Merge and limit
                     current = var_samples[var_name][val_str]
                     existing_urls = [x['url'] for x in current]
                     for s in s_list:
                         if s['url'] not in existing_urls and len(current) < 15:
                             current.append(s)
            
            # Merge field counts
            f_counts = v.get('fields', {})
            for f_name, count in f_counts.items():
                if f_name not in field_counts:
                    field_counts[f_name] = 0
                field_counts[f_name] += count

            # Merge unprocessed counts
            u_counts = v.get('unprocessed', {})
            for f_name, count in u_counts.items():
                if f_name not in unprocessed_counts:
                    unprocessed_counts[f_name] = 0
                unprocessed_counts[f_name] += count

        out = {'c': total_count, 's': samples, 'v': var_stats, 'vs': var_samples}
        if field_counts:
            out['fields'] = field_counts
        if unprocessed_counts:
            out['unprocessed'] = unprocessed_counts
        yield (note_id, out)

    def reducer(self, note_id, values):
        """
        Reducer: Sums counts globally and keeps up to 5 sample URLs.
        """
        total_count = 0
        samples = []
        var_stats = {}
        var_samples = {}
        field_counts = {}
        unprocessed_counts = {}

        for v in values:
            total_count += v['c']
            for s in v.get('s', []):
                 existing_urls = [x['url'] for x in samples]
                 if s['url'] not in existing_urls and len(samples) < 5:
                    samples.append(s)
            
            # Merge var stats
            v_stats = v.get('v', {})
            for var_name, counts in v_stats.items():
                if var_name not in var_stats:
                    var_stats[var_name] = {}
                for val_str, count in counts.items():
                        var_stats[var_name][val_str] = 0
                    var_stats[var_name][val_str] += count

            # Merge var samples
            v_samples = v.get('vs', {})
            for var_name, val_dict in v_samples.items():
                if var_name not in var_samples:
                    var_samples[var_name] = {}
                for val_str, s_list in val_dict.items():
                     if val_str not in var_samples[var_name]:
                         var_samples[var_name][val_str] = []
                     # Merge and limit
                     current = var_samples[var_name][val_str]
                     existing_urls = [x['url'] for x in current]
                     for s in s_list:
                         if s['url'] not in existing_urls and len(current) < 15:
                             current.append(s)
            
            # Merge field counts
            f_counts = v.get('fields', {})
            for f_name, count in f_counts.items():
                if f_name not in field_counts:
                    field_counts[f_name] = 0
                field_counts[f_name] += count
                
            # Merge unprocessed counts
            u_counts = v.get('unprocessed', {})
            for f_name, count in u_counts.items():
                if f_name not in unprocessed_counts:
                    unprocessed_counts[f_name] = 0
                unprocessed_counts[f_name] += count
        
        if note_id.startswith("_"):
             out = {}
             if total_count > 0:
                 out = total_count # Legacy/simple
             if note_id == "_FIELD_COUNTS":
                 yield ("field_counts", field_counts)
                 yield ("unprocessed_counts", unprocessed_counts)
             elif note_id == "_TOTAL_RESPONSES":
                 yield ("total_responses", total_count)
             else:
                 yield (note_id, total_count)
        else:
             yield (note_id, {'count': total_count, 'samples': samples, 'vars': var_stats, 'var_samples': var_samples})

if __name__ == '__main__':
    CCLintJob.run()
