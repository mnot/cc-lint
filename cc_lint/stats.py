from collections import Counter
from typing import Any, Dict, Iterator, List, Optional, Set

from httplint.field.finder import UnknownHttpField
from httplint.message import HttpResponseLinter
from httplint.note import Note, levels

from cc_lint.fingerprint import UNMATCHED, Fingerprinter, default_fingerprinter
from cc_lint.histograms import byte_bucket, duration_bucket
from cc_lint.hll import (
    HLL_P_GLOBAL,
    HLL_P_PER_NOTE,
    HLL_P_RECIPE,
    hll_add,
    make_registers,
)
from cc_lint.ipasn import IpAsnTable
from cc_lint.recipes import RecipeStats
from cc_lint.top_sites import normalize_site
from cc_lint.types import NoteDataType, SampleType
from cc_lint.vary import ASTERISK, recipe_key, vary_tokens


def _level_to_severity(level: Any) -> Optional[str]:
    """Map an httplint ``levels`` value to the canonical severity string.

    Returns one of ``"bad"``, ``"warn"``, ``"info"``, ``"good"`` for
    recognised levels, or ``None`` for anything else (defensive against
    levels we haven't seen).
    """
    if level == levels.BAD:
        return "bad"
    if level == levels.WARN:
        return "warn"
    if level == levels.INFO:
        return "info"
    if level == levels.GOOD:
        return "good"
    return None


def iter_notes_depth_first(notes: Any) -> Iterator[Note]:
    """Yield each note and all of its descendant subnotes, depth-first.

    httplint attaches a class of findings — the strength/quality layer for
    CSP, HSTS, Permissions-Policy, etc. — as children via ``Note.add_child()``,
    which appends to ``note.subnotes`` rather than to ``linter.notes``. A child
    is itself a full ``Note`` (own level/vars and possibly its own subnotes), so
    we recurse rather than descend a single level.
    """
    for note in notes:
        yield note
        subnotes = getattr(note, "subnotes", None)
        if subnotes:
            yield from iter_notes_depth_first(subnotes)


def _header_value_byte_len(value: Any) -> int:
    """Best-effort byte length of a header field value as it appeared on the wire.

    httplint may hand us a str or bytes depending on how the linter was fed.
    For str, encode as UTF-8 since CSP values are ASCII in practice and
    UTF-8 is a superset; for anything else fall back to ``len(str(...))``.
    """
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8", errors="replace"))
    return len(str(value))


# Configuration for variable tracking
# Map Note ID to list of variable names to track statistics for
VARS_TO_TRACK = {
    "FIELD_DEPRECATED": ["field_name"],
    "SET_COOKIE_UNKNOWN_ATTRIBUTE": ["attribute"],
    "REQUEST_HDR_IN_RESPONSE": ["field_name"],
    "BAD_SYNTAX": ["field_name"],
    "CONTENT_TYPE_MISMATCH": ["sniffed_type", "declared_type"],
    "VARY_COMPLEX": ["vary_count"],
    "BAD_DATE_SYNTAX": ["field_name"],
    "SINGLE_HEADER_REPEAT": ["field_name"],
    "CC_DUP": ["directive"],
    "STRUCTURED_FIELD_PARSE_ERROR": ["field_error", "field_name"],
    "BAD_CC_SYNTAX": ["bad_directive"],
    "UNKNOWN_VALUE": ["field_name", "value"],
    "CROSS_ORIGIN_RESOURCE_POLICY_BAD_VALUE": ["value"],
    "BAD_SYNTAX_DETAILED": ["field_name"],
    "CC_WRONG_MESSAGE": ["other_message"],
    "CC_CONFLICTING": ["directive_conflicts"],
    "DUPLICATE_KEY": ["field_name", "field_name_key"],
    "FRESHNESS_FRESH": ["freshness_left_bucket"],
    "SET_COOKIE_LIFETIME_TOO_LONG": ["duration_bucket"],
    "SET_COOKIE_VALUE_TOO_LARGE": ["cookie_value_size_bucket"],
    "FIELD_TOO_LARGE": ["field_name", "field_size_bucket"],
}

# Every note whose breakdown is keyed on field_name gets per-field sample URLs
# with the offending header value(s) captured, wildcarded over field names.
# Derived from VARS_TO_TRACK so the two can't drift. A wildcard is safe for
# field_name because the key space (header names) is bounded and the retained
# set is further capped by VAR_SAMPLE_LIMIT (per value) and TOP_K_VAR_VALUES
# (per var); only field_name samples capture the on-the-wire value, since
# create_sample reads field_values from the matching header. High-cardinality
# value dimensions (field_error, attribute, directive_conflicts) are left
# unsampled to bound the cross-reducer shuffle. See issue #1.
SAMPLES_TO_COLLECT: Dict[str, Dict[str, List[str]]] = {
    note_id: {"field_name": ["*"]}
    for note_id, tracked_vars in VARS_TO_TRACK.items()
    if "field_name" in tracked_vars
}
# Bounded, hand-picked value sets stay explicit.
SAMPLES_TO_COLLECT["VARY_COMPLEX"] = {
    "vary_count": ["6", "7", "8", "9", "10", "11", "12", "27"]
}

# Cap each captured header value so a single pathological header can't bloat
# the report or the shuffle. Applied per value, before repr().
MAX_FIELD_VALUE_LEN = 300

# Distinct sites retained per (var, value) sample bucket. The collection side
# (StatsCollector._collect_samples) and the reducer-side merge
# (cc_lint.emr.job._merge_var_samples) must use the same cap, so it lives here
# -- the single source of truth -- and job.py imports it. It can't live in
# job.py because job.py imports stats, not the reverse.
VAR_SAMPLE_LIMIT = 15


def _bucket_var(note: Note, source_var: str, bucketer: Any) -> Optional[str]:
    raw = note.vars.get(source_var)
    if raw is None:
        return None
    try:
        return str(bucketer(float(str(raw))))
    except (TypeError, ValueError):
        return None


def get_note_value(note: Note, var_name: str) -> Optional[Any]:
    val: Optional[Any] = None
    if (
        var_name == "field_error"
        and "field_name" in note.vars
        and "problem" in note.vars
    ):
        # STRUCTURED_FIELD_PARSE_ERROR reports the parse failure in `problem`
        # (with `context` holding the multi-line caret excerpt, which we drop).
        # Keep the first line so values group cleanly.
        error_msg = str(note.vars["problem"]).split("\n", maxsplit=1)[0]
        val = f"{note.vars['field_name']}: {error_msg}"
    elif var_name == "directive_conflicts":
        directive = note.vars.get("directive")
        conflicts = note.vars.get("conflicts")
        if directive is not None and conflicts is not None:
            # httplint renders the conflicts list as a markdown bullet block;
            # collapse newlines so the value becomes a single sortable string.
            conflicts_str = " ".join(str(conflicts).split())
            val = f"{directive} → {conflicts_str}"
    elif var_name == "field_name_key":
        field = note.vars.get("field_name")
        key = note.vars.get("key")
        if field is not None and key is not None:
            val = f"{field}: {key}"
    elif var_name == "freshness_left_bucket":
        val = _bucket_var(note, "freshness_left", duration_bucket)
    elif var_name == "duration_bucket":
        val = _bucket_var(note, "duration", duration_bucket)
    elif var_name == "cookie_value_size_bucket":
        val = _bucket_var(note, "set_cookie_value_length", byte_bucket)
    elif var_name == "field_size_bucket":
        val = _bucket_var(note, "field_size", byte_bucket)
    elif var_name in note.vars:
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

    For the note-level samples pool (``var_name`` unset) the full note-vars
    dump is carried, since :func:`cc_lint.report.sections._sample_li` renders
    it. For per-(var, val) samples (``var_name``/``val_str`` set) only the
    payload the report actually reads is carried -- the on-the-wire header
    value(s) for ``field_name`` samples -- so the discarded note-attribute dump
    doesn't ride the mapper->reducer shuffle for every retained sample.
    """
    sample_url = getattr(linter, "base_uri", None)
    if not sample_url:
        return None
    site = normalize_site(sample_url)
    if site is None:
        return None

    note_vars: Dict[str, str] = {}
    if var_name and val_str:
        if var_name == "field_name":
            # Capture the offending header value(s) for context.
            target_field_name = val_str.lower()
            values = []
            for h_name, h_val in linter.headers.text:
                h_name_str = str(h_name)
                if h_name_str.lower() == target_field_name:
                    h_val_str = str(h_val)
                    if len(h_val_str) > MAX_FIELD_VALUE_LEN:
                        h_val_str = h_val_str[:MAX_FIELD_VALUE_LEN] + "…[truncated]"
                    values.append(h_val_str)
            if values:
                note_vars["field_values"] = repr(values)
    else:
        filtered_keys = ["vars", "subnotes", "subject", "field_type", "message_type"]
        for key, val in vars(note).items():
            if key not in filtered_keys:
                note_vars[key] = str(val)
        for key, val in note.vars.items():
            if key not in filtered_keys:
                note_vars[key] = str(val)

    return {"url": sample_url, "vars": note_vars, "site": site}


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
    def __init__(
        self,
        sample_sites: Optional[Set[str]] = None,
        fingerprinter: Optional[Fingerprinter] = None,
        ipasn: Optional[IpAsnTable] = None,
    ) -> None:
        self.note_data: Dict[str, NoteDataType] = {}
        self.total_responses = 0
        self.field_counts: Counter[str] = Counter()
        self.unprocessed_counts: Counter[str] = Counter()
        # Best-effort infrastructure fingerprinting (issue #4). The default
        # packaged table is shared (and cached) across collectors. ``ipasn``
        # is the optional offline IP->ASN table; when present, the crawl-time
        # WARC-IP-Address is resolved to an ASN that can match a layer by
        # network (and feeds the top-ASN histogram). None disables ASN logic.
        self.fingerprinter = fingerprinter or default_fingerprinter()
        self.ipasn = ipasn
        # Resolved-ASN -> response count, for surfacing big networks that
        # aren't (yet) in the fingerprint table. Keyed by str(asn) for JSON.
        self.asn_counts: Counter[str] = Counter()
        # Responses matching each layer, with an UNMATCHED bucket. Layers
        # overlap (a response can be cloudflare + nginx + nextjs), so this
        # sums to more than total_responses; that's expected and gives each
        # layer a real per-layer denominator.
        self.layer_counts: Counter[str] = Counter()
        # field name (lower) -> layer -> occurrences, sliced from the same
        # per-occurrence accounting as field_counts so it shares its
        # denominator. Answers "which infra emits header H".
        self.field_counts_by_layer: Dict[str, Counter[str]] = {}
        # When set, only responses whose site is in this set contribute samples
        # (URLs in samples/var_samples). Note counts, var counts, and field
        # histograms are unaffected. None disables the gate.
        self.sample_sites = sample_sites
        # HyperLogLog of distinct sites that contributed any response, plus
        # per-note HLLs for "seen on N sites" cardinality. Per-note HLLs use a
        # smaller precision to keep shuffle bounded across many notes.
        self.sites_hll: List[int] = make_registers(HLL_P_GLOBAL)
        # Per-site maximum CSP header byte size. A site appears in many WATs
        # with potentially different responses and CSP values; we keep the
        # maximum so the report's histogram counts each site once at the
        # largest CSP it ever served. 0 means "site seen, no CSP."
        self.csp_max_by_site: Dict[str, int] = {}
        # Per-response health rollup. For each response we bucket by the
        # most severe note that fired (bad > warn > info > good > clean),
        # so the report can show "X% of responses produced no findings".
        # This is intentionally per-response, not per-site: it's a
        # population-level summary of what httplint thought of each
        # crawled response, not a claim about specific sites.
        self.severity_counts: Counter[str] = Counter()
        # Vary composition (issue #3). Recipes are the full sorted token-set
        # per response (the primary artifact); marginals are the per-field
        # rollup. Both carry occurrence + per-site HLL via RecipeStats.
        # Recipes are high-cardinality (up to TOP_K_RECIPES per mapper), so
        # their per-site HLLs use the coarse HLL_P_RECIPE precision to bound
        # shuffle; marginals are bounded (a few hundred field-names) and
        # carry the headline axes, so they keep the default HLL_P_PER_NOTE.
        self.responses_with_vary = 0
        self.vary_recipes = RecipeStats(HLL_P_RECIPE)
        self.vary_marginals = RecipeStats(HLL_P_PER_NOTE)

    def process_linter(self, linter: HttpResponseLinter) -> None:
        """
        Extracts stats from a finished linter.
        """
        self.total_responses += 1
        site = normalize_site(getattr(linter, "base_uri", None))
        if site:
            hll_add(self.sites_hll, HLL_P_GLOBAL, site)
        # Fingerprint the response once, up front, so the layer set can be
        # attributed to every note and header below. The layer set unions
        # header signals with an optional ASN match from the crawl-time IP.
        # Materialize the header list once: it feeds fingerprint matching here
        # and field counting in _process_headers, so we avoid a second walk of
        # linter.headers.text on the per-response hot path.
        header_items = [
            (str(name).lower(), value) for name, value in linter.headers.text
        ]
        layers, asn = self._fingerprint(header_items, linter)
        if asn is not None:
            self.asn_counts[str(asn)] += 1
        if layers:
            for layer in layers:
                self.layer_counts[layer] += 1
        else:
            self.layer_counts[UNMATCHED] += 1
        # Track the maximum severity fired on this response so we can roll
        # it up into severity_counts after the note loop.
        max_severity: Optional[str] = None
        severity_order = {"bad": 4, "warn": 3, "info": 2, "good": 1}
        for note in iter_notes_depth_first(linter.notes):
            level = getattr(note, "level", None)
            severity = _level_to_severity(level)
            if severity is None:
                continue
            if (
                max_severity is None
                or severity_order[severity] > severity_order[max_severity]
            ):
                max_severity = severity
            self._process_note(note, linter, site, layers)

        self.severity_counts[max_severity or "clean"] += 1
        self._process_headers(header_items, linter, site, layers)
        self._process_vary(linter, site)

    def _fingerprint(
        self,
        header_items: List[tuple[str, Any]],
        linter: HttpResponseLinter,
    ) -> tuple[Set[str], Optional[int]]:
        """Return (matched layer ids, resolved ASN or None) for a response.

        ``header_items`` is the pre-lowercased (name, value) list shared with
        :meth:`_process_headers` so headers are walked once per response.
        """
        header_map: Dict[str, List[str]] = {}
        for name_lower, value in header_items:
            header_map.setdefault(name_lower, []).append(str(value))
        asn: Optional[int] = None
        if self.ipasn is not None:
            ip_address = getattr(linter, "ip_address", None)
            if ip_address:
                asn = self.ipasn.lookup(str(ip_address))
        return self.fingerprinter.match(header_map, asn), asn

    def _process_vary(self, linter: HttpResponseLinter, site: Optional[str]) -> None:
        """Tabulate Vary composition for responses carrying a Vary header.

        Records the full token-set as a recipe and each field-name as a
        marginal, both keyed for per-occurrence and per-site rollups. The
        wildcard token stays in the recipe (so ``*`` shows up as its own
        recipe) but is excluded from the per-field marginals.
        """
        tokens = vary_tokens(linter)
        if not tokens:
            return
        self.responses_with_vary += 1
        self.vary_recipes.add(recipe_key(tokens), site)
        for token in tokens:
            if token == ASTERISK:
                continue
            self.vary_marginals.add(token, site)

    def _process_note(
        self,
        note: Note,
        linter: HttpResponseLinter,
        site: Optional[str],
        layers: Set[str],
    ) -> None:
        note_id = note.__class__.__name__

        if note_id not in self.note_data:
            self.note_data[note_id] = {
                "count": 0,
                "samples": [],
                "vars": {},
                "sites_hll": make_registers(HLL_P_PER_NOTE),
            }

        self.note_data[note_id]["count"] += 1
        if site:
            sites_hll = self.note_data[note_id].get("sites_hll")
            if sites_hll is not None:
                hll_add(sites_hll, HLL_P_PER_NOTE, site)

        if layers:
            by_layer = self.note_data[note_id].setdefault("by_layer", {})
            for layer in layers:
                by_layer[layer] = by_layer.get(layer, 0) + 1

        self._track_vars(note, note_id)
        self._track_numeric_maxes(note, note_id)
        self._collect_samples(note, linter, note_id)
        self._collect_note_sample(note, linter, note_id)

    # Per-note maxima keyed by another var. Currently only FIELD_TOO_LARGE
    # uses this, to surface the largest field_size seen per field_name.
    # Mapper and reducer merge these dicts with max() instead of sum().
    _NUMERIC_MAXES = {
        "FIELD_TOO_LARGE": [("field_name", "field_size")],
    }

    def _track_numeric_maxes(self, note: Note, note_id: str) -> None:
        spec = self._NUMERIC_MAXES.get(note_id)
        if not spec:
            return
        for key_var, value_var in spec:
            key = note.vars.get(key_var)
            raw = note.vars.get(value_var)
            if key is None or raw is None:
                continue
            try:
                size = int(float(str(raw)))
            except (TypeError, ValueError):
                continue
            maxes = self.note_data[note_id].setdefault("numeric_maxes", {})
            per_var = maxes.setdefault(value_var, {})
            prev = per_var.get(str(key), 0)
            if size > prev:
                per_var[str(key)] = size

    def _track_vars(self, note: Note, note_id: str) -> None:
        # Track variable statistics
        for var_name, val_str in iter_tracked_vars(note):
            if var_name not in self.note_data[note_id]["vars"]:
                self.note_data[note_id]["vars"][var_name] = {}

            if val_str not in self.note_data[note_id]["vars"][var_name]:
                self.note_data[note_id]["vars"][var_name][val_str] = 0
            self.note_data[note_id]["vars"][var_name][val_str] += 1

    def _site_eligible_for_sample(self, site: Optional[str]) -> bool:
        if site is None:
            return False
        if self.sample_sites is None:
            return True
        return site in self.sample_sites

    def _collect_samples(
        self, note: Note, linter: HttpResponseLinter, note_id: str
    ) -> None:
        # Collect detailed samples, deduped by site so the cap maps to N
        # distinct sites rather than N URLs from possibly the same site.
        for var_name, val_str, sample in iter_collected_samples(note, linter):
            sample_site = sample.get("site")
            if not self._site_eligible_for_sample(sample_site):
                continue
            if "var_samples" not in self.note_data[note_id]:
                self.note_data[note_id]["var_samples"] = {}
            if var_name not in self.note_data[note_id]["var_samples"]:
                self.note_data[note_id]["var_samples"][var_name] = {}
            if val_str not in self.note_data[note_id]["var_samples"][var_name]:
                self.note_data[note_id]["var_samples"][var_name][val_str] = []

            current_samples = self.note_data[note_id]["var_samples"][var_name][val_str]
            if len(current_samples) >= VAR_SAMPLE_LIMIT:
                continue
            if sample_site not in {s.get("site") for s in current_samples}:
                current_samples.append(sample)

    def _collect_note_sample(
        self, note: Note, linter: HttpResponseLinter, note_id: str
    ) -> None:
        if len(self.note_data[note_id]["samples"]) >= 5:
            return
        sample = create_sample(note, linter)
        if not sample:
            return
        sample_site = sample.get("site")
        if not self._site_eligible_for_sample(sample_site):
            return
        existing_sites = {s.get("site") for s in self.note_data[note_id]["samples"]}
        if sample_site not in existing_sites:
            self.note_data[note_id]["samples"].append(sample)

    def _process_headers(
        self,
        header_items: List[tuple[str, Any]],
        linter: HttpResponseLinter,
        site: Optional[str],
        layers: Set[str],
    ) -> None:
        # Count fields (case-insensitive). ``header_items`` is the (lowercased
        # name, raw value) list already materialized in process_linter.
        # Capture CSP byte size for the per-site histogram while we iterate.
        csp_bytes = 0
        for name_lower, value in header_items:
            self.field_counts[name_lower] += 1
            if layers:
                per_layer = self.field_counts_by_layer.setdefault(name_lower, Counter())
                for layer in layers:
                    per_layer[layer] += 1
            if name_lower == "content-security-policy":
                csp_bytes += _header_value_byte_len(value)

        if site:
            prev = self.csp_max_by_site.get(site, 0)
            if csp_bytes > prev:
                self.csp_max_by_site[site] = csp_bytes
            elif site not in self.csp_max_by_site:
                # Record the site with size 0 so the histogram can show
                # "no CSP" as a distinct bucket rather than missing data.
                self.csp_max_by_site[site] = 0

        # Count unprocessed headers, ignoring crawler-injected ones.
        for name, handler in linter.headers.handlers.items():
            if isinstance(handler, UnknownHttpField) and not name.startswith(
                "x-crawler-"
            ):
                self.unprocessed_counts[name] += 1

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "total_responses": self.total_responses,
            "notes": self.note_data,
            "field_counts": dict(self.field_counts),
            "unprocessed_counts": dict(self.unprocessed_counts),
            "sites_hll": self.sites_hll,
            "csp_max_by_site": dict(self.csp_max_by_site),
            "severity_counts": dict(self.severity_counts),
            "layer_counts": dict(self.layer_counts),
            "field_counts_by_layer": {
                field: dict(layers)
                for field, layers in self.field_counts_by_layer.items()
            },
            "asn_counts": dict(self.asn_counts),
        }
        if self.responses_with_vary:
            result["vary"] = {
                "responses_with_vary": self.responses_with_vary,
                "recipes": self.vary_recipes.to_dict(),
                "marginals": self.vary_marginals.to_dict(),
            }
        return result
