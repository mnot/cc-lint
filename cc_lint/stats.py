from typing import Dict, Optional, Iterator, Any
from collections import Counter

from httplint.note import levels, Note
from httplint import HttpResponseLinter
from httplint.field.finder import UnknownHttpField

from cc_lint.types import SampleType, NoteDataType

# Configuration for variable tracking
# Map Note ID to list of variable names to track statistics for
VARS_TO_TRACK = {
    "FIELD_DEPRECATED": ["field_name"],
    "SET_COOKIE_UNKNOWN_ATTRIBUTE": ["attribute"],
    "SERVER_TIMING_MISSING_DUR": ["metric"],
    "REQUEST_HDR_IN_RESPONSE": ["field_name"],
    "BAD_SYNTAX": ["field_name"],
    "CONTENT_TYPE_MISMATCH": ["sniffed_type", "declared_type"],
    "VARY_COMPLEX": ["vary_count"],
    "BAD_DATE_SYNTAX": ["field_name"],
    "SINGLE_HEADER_REPEAT": ["field_name"],
    "CC_DUP": ["directive"],
    "STRUCTURED_FIELD_PARSE_ERROR": ["field_error"],
    "BAD_CC_SYNTAX": ["bad_directive"],
    "UNKNOWN_VALUE": ["field_name", "value"],
    "CROSS_ORIGIN_RESOURCE_POLICY_BAD_VALUE": ["value"],
    "BAD_SYNTAX_DETAILED": ["field_name"],
    "CC_WRONG_MESSAGE": ["other_message"],
}

SAMPLES_TO_COLLECT = {
    "BAD_SYNTAX": {
        "field_name": [
            "link",
            "via",
            "strict-transport-security",
            "clear-site-data",
            "content-md5",
            "location",
            "warning",
            "content-range",
        ]
    },
    "BAD_SYNTAX_DETAILED": {
        "field_name": [
            "link",
            "via",
            "strict-transport-security",
            "clear-site-data",
            "content-md5",
            "location",
            "warning",
            "content-range",
        ]
    },
    "VARY_COMPLEX": {"vary_count": ["6", "7", "8", "9", "10", "11", "12", "27"]},
    "STRUCTURED_FIELD_PARSE_ERROR": {"field_error": ["*"]},
}


def get_note_value(note: Note, var_name: str) -> Optional[Any]:
    val: Optional[Any] = None
    if (
        var_name == "field_error"
        and "field_name" in note.vars
        and "error" in note.vars
    ):
        # Strip context from the error message to group effectively
        error_msg = str(note.vars["error"]).split("\n", maxsplit=1)[0]
        val = f"{note.vars['field_name']}: {error_msg}"
    elif hasattr(note, "vars") and var_name in note.vars:
        val = note.vars[var_name]
    elif hasattr(note, var_name):
        val = getattr(note, var_name)
    return val


def create_sample(
    note: Note,
    linter: HttpResponseLinter,
    var_name: Optional[str] = None,
    val_str: Optional[str] = None,
) -> Optional[SampleType]:
    """
    Helper to create a sample dictionary from a note.
    Captures header values if var_name/val_str are provided and match logic.
    """
    sample_url = getattr(linter, "base_uri", None)
    if not sample_url:
        return None

    note_vars = {}
    filtered_keys = ["vars", "subnotes", "subject", "field_type", "message_type"]
    for key, val in vars(note).items():
        if key not in filtered_keys:
            note_vars[key] = str(val)
    if hasattr(note, "vars"):
        for key, val in note.vars.items():
            if key not in filtered_keys:
                note_vars[key] = str(val)

    if var_name and val_str:
        # Capture header values for context
        if (
            hasattr(linter, "headers")
            and hasattr(linter.headers, "text")
            and var_name == "field_name"
        ):
            target_field_name = val_str.lower()
            values = []
            for h_name, h_val in linter.headers.text:
                h_name_str = str(h_name)
                if h_name_str.lower() == target_field_name:
                    h_val_str = str(h_val)
                    values.append(h_val_str)
            if values:
                note_vars["field_values"] = repr(values)

    return {"url": sample_url, "vars": note_vars}


def iter_tracked_vars(note: Note) -> Iterator[tuple[str, str]]:
    note_id = note.__class__.__name__
    if note_id in VARS_TO_TRACK:
        for var_name in VARS_TO_TRACK[note_id]:
            val = get_note_value(note, var_name)
            if val is not None:
                yield var_name, str(val)


def iter_collected_samples(
    note: Note, linter: HttpResponseLinter
) -> Iterator[tuple[str, str, SampleType]]:
    note_id = note.__class__.__name__
    if note_id in SAMPLES_TO_COLLECT:
        for var_name, target_values in SAMPLES_TO_COLLECT[note_id].items():
            val = get_note_value(note, var_name)
            if val is not None:
                val_str = str(val)
                # Check for wildcard or explicit match
                if "*" in target_values or val_str.lower() in target_values:
                    sample = create_sample(note, linter, var_name, val_str)
                    if sample:
                        yield var_name, val_str, sample


class StatsCollector:
    def __init__(self) -> None:
        self.note_data: Dict[str, NoteDataType] = {}
        self.total_responses = 0
        self.field_counts: Counter[str] = Counter()
        self.unprocessed_counts: Counter[str] = Counter()

    def process_linter(self, linter: HttpResponseLinter) -> None:
        """
        Extracts stats from a finished linter.
        """
        self.total_responses += 1
        for note in linter.notes:
            if note.level not in [levels.WARN, levels.BAD]:
                continue
            self._process_note(note, linter)

        self._process_headers(linter)

    def _process_note(self, note: Note, linter: HttpResponseLinter) -> None:
        # Using the note's class name as identifier
        note_id = note.__class__.__name__

        if note_id not in self.note_data:
            self.note_data[note_id] = {"count": 0, "samples": [], "vars": {}}

        self.note_data[note_id]["count"] += 1

        self._track_vars(note, note_id)
        self._collect_samples(note, linter, note_id)
        self._collect_note_sample(note, linter, note_id)

    def _track_vars(self, note: Note, note_id: str) -> None:
        # Track variable statistics
        for var_name, val_str in iter_tracked_vars(note):
            if var_name not in self.note_data[note_id]["vars"]:
                self.note_data[note_id]["vars"][var_name] = {}

            if val_str not in self.note_data[note_id]["vars"][var_name]:
                self.note_data[note_id]["vars"][var_name][val_str] = 0
            self.note_data[note_id]["vars"][var_name][val_str] += 1

    def _collect_samples(self, note: Note, linter: HttpResponseLinter, note_id: str) -> None:
        # Collect detailed samples
        for var_name, val_str, sample in iter_collected_samples(note, linter):
            if "var_samples" not in self.note_data[note_id]:
                self.note_data[note_id]["var_samples"] = {}
            if var_name not in self.note_data[note_id]["var_samples"]:
                self.note_data[note_id]["var_samples"][var_name] = {}
            if val_str not in self.note_data[note_id]["var_samples"][var_name]:
                self.note_data[note_id]["var_samples"][var_name][val_str] = []

            # Check limit (15)
            current_samples = self.note_data[note_id]["var_samples"][var_name][val_str]
            if len(current_samples) < 15:
                # Check uniqueness
                if sample["url"] not in [s["url"] for s in current_samples]:
                    current_samples.append(sample)

    def _collect_note_sample(self, note: Note, linter: HttpResponseLinter, note_id: str) -> None:
        sample_url = getattr(linter, "base_uri", None)
        if sample_url and len(self.note_data[note_id]["samples"]) < 5:
            sample = create_sample(note, linter)
            if sample:
                # Check if we already have this URL. If we do, we don't add it again.
                current_urls = [s["url"] for s in self.note_data[note_id]["samples"]]
                if sample_url not in current_urls:
                    self.note_data[note_id]["samples"].append(sample)

    def _process_headers(self, linter: HttpResponseLinter) -> None:
        # Count fields
        if hasattr(linter, "headers") and hasattr(linter.headers, "text"):
            for name, _value in linter.headers.text:
                # linter headers are often bytes, decode if needed
                name_str = str(name)
                # Normalize case to lower for case-insensitive stats as requested
                self.field_counts[name_str.lower()] += 1

        # Count unprocessed headers
        if hasattr(linter, "headers") and hasattr(linter.headers, "handlers"):
            for name, handler in linter.headers.handlers.items():
                if isinstance(handler, UnknownHttpField):
                    if not name.startswith("x-crawler-"):
                        self.unprocessed_counts[name] += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_responses": self.total_responses,
            "notes": self.note_data,
            "field_counts": dict(self.field_counts),
            "unprocessed_counts": dict(self.unprocessed_counts),
        }
