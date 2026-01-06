from collections import Counter

from httplint.note import levels
from httplint.field.finder import UnknownHttpField

# Configuration for variable tracking
# Map Note ID to list of variable names to track statistics for
VARS_TO_TRACK = {
    'FIELD_DEPRECATED': ['field_name'],
    'SET_COOKIE_UNKNOWN_ATTRIBUTE': ['attribute'],
    'SERVER_TIMING_MISSING_DUR': ['metric'],
    'REQUEST_HDR_IN_RESPONSE': ['field_name'],
    'BAD_SYNTAX': ['field_name'],
    'CONTENT_TYPE_MISMATCH': ['sniffed_type', 'declared_type'],
    'VARY_COMPLEX': ['vary_count'],
    'BAD_DATE_SYNTAX': ['field_name'],
    'SINGLE_HEADER_REPEAT': ['field_name'],
    'CC_DUP': ['directive'],
    'STRUCTURED_FIELD_PARSE_ERROR': ['field_name', 'error'],
    'BAD_CC_SYNTAX': ['bad_directive']
}

class StatsCollector:
    def __init__(self):
        self.note_data = {}
        self.total_responses = 0
        self.field_counts = Counter()
        self.unprocessed_counts = Counter()

    def process_linter(self, linter):
        """
        Extracts stats from a finished linter.
        """
        self.total_responses += 1
        for note in linter.notes:
            if note.level not in [levels.WARN, levels.BAD]:
                continue
                
            # Using the note's class name as identifier
            note_id = note.__class__.__name__
            
            if note_id not in self.note_data:
                self.note_data[note_id] = {'count': 0, 'samples': [], 'vars': {}}
            
            self.note_data[note_id]['count'] += 1
            
            # Track variable statistics
            if note_id in VARS_TO_TRACK:
                for var_name in VARS_TO_TRACK[note_id]:
                    val = None
                    if hasattr(note, 'vars') and var_name in note.vars:
                        val = note.vars[var_name]
                    elif hasattr(note, var_name):
                        val = getattr(note, var_name)
                    
                    if val is not None:
                        val_str = str(val)
                        if var_name not in self.note_data[note_id]['vars']:
                            self.note_data[note_id]['vars'][var_name] = {}
                        
                        if val_str not in self.note_data[note_id]['vars'][var_name]:
                            self.note_data[note_id]['vars'][var_name][val_str] = 0
                        self.note_data[note_id]['vars'][var_name][val_str] += 1
            
            sample_url = getattr(linter, 'base_uri', None)
            if sample_url and len(self.note_data[note_id]['samples']) < 5:
                 # Capture note instance variables
                 note_vars = {}
                 filtered_keys = ['vars', 'subnotes', 'subject', 'field_type', 'message_type']
                 for k, v in vars(note).items():
                    if k not in filtered_keys:
                        note_vars[k] = str(v)
                 if hasattr(note, 'vars'):
                     for k, v in note.vars.items():
                         if k not in filtered_keys:
                             note_vars[k] = str(v)
                 
                 # Check if we already have this URL. If we do, we don't add it again.
                 # (Complexity: checking uniqueness of URL in list of dicts)
                 current_urls = [s['url'] for s in self.note_data[note_id]['samples']]
                 if sample_url not in current_urls:
                     self.note_data[note_id]['samples'].append({'url': sample_url, 'vars': note_vars})
                     
        # Count fields
        if hasattr(linter, 'headers') and hasattr(linter.headers, 'text'):
            for name, value in linter.headers.text:
                # linter headers are often bytes, decode if needed
                if isinstance(name, bytes):
                    name_str = name.decode('latin1', errors='replace')
                else:
                    name_str = str(name)
                # Normalize case to lower for case-insensitive stats as requested
                self.field_counts[name_str.lower()] += 1

        # Count unprocessed headers
        if hasattr(linter, 'headers') and hasattr(linter.headers, 'handlers'):
            for name, handler in linter.headers.handlers.items():
                if isinstance(handler, UnknownHttpField):
                    if not name.startswith('x-crawler-'):
                        self.unprocessed_counts[name] += 1

    def to_dict(self):
        return {
            'total_responses': self.total_responses,
            'notes': self.note_data,
            'field_counts': dict(self.field_counts),
            'unprocessed_counts': dict(self.unprocessed_counts)
        }
