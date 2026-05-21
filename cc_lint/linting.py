import json
import logging
from typing import Any, Optional

from dateutil.parser import parse
from httplint import HttpResponseLinter

logger = logging.getLogger(__name__)


def _as_latin1(value: Any) -> bytes:
    """Encode value as latin1, the wire encoding httplint expects.

    Header field names and values come in as str (from JSON-decoded WAT
    payloads) or already-decoded warcio str. httplint's process_*
    methods take bytes; we replace any non-latin1 characters rather
    than raise so a single malformed header doesn't drop the response.
    """
    return str(value).encode("latin1", errors="replace")


def lint_record(record: Any) -> Optional[HttpResponseLinter]:
    """
    Lints a WARC record or WAT metadata using httplint.
    Returns the linter object populated with notes, or None if not a response.
    """
    if record.rec_type == "metadata":
        return _lint_wat_record(record)

    if record.rec_type == "response":
        return _lint_warc_record(record)

    return None


def _lint_wat_record(record: Any) -> Optional[HttpResponseLinter]:
    """
    Parse a WAT metadata record.
    """
    try:
        content = record.content_stream().read()
        data = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError, OSError) as exc:
        # Malformed WAT envelope, truncated read, or a record whose
        # content_stream isn't byte-like. Log so EMR stderr surfaces the
        # rate of parse failures; return None so the caller skips the record.
        logger.warning("WAT record parse failed (%s): %s", type(exc).__name__, exc)
        return None

    response_meta = (
        data.get("Envelope", {})
        .get("Payload-Metadata", {})
        .get("HTTP-Response-Metadata")
    )
    if not response_meta:
        return None

    linter = HttpResponseLinter(no_content=True)

    # Base URI
    warc_header_meta = data.get("Envelope", {}).get("WARC-Header-Metadata", {})
    target_uri = warc_header_meta.get("WARC-Target-URI")
    if target_uri:
        linter.base_uri = target_uri

    # Date
    warc_date = warc_header_meta.get("WARC-Date")
    if warc_date:
        try:
            dt = parse(warc_date)
            linter.start_time = dt.timestamp()
        except (ValueError, TypeError, OverflowError):
            # Malformed or out-of-range WARC-Date; leave start_time unset.
            pass

    # Status Line
    response_message = response_meta.get("Response-Message")
    if isinstance(response_message, dict):
        status_code = response_message.get("Status")
        status_phrase = response_message.get("Reason", "")
        protocol = response_message.get("Version", "HTTP/1.1")
    else:
        # Fallback or error
        status_code = None
        status_phrase = ""
        protocol = "HTTP/1.1"

    linter.process_response_topline(
        _as_latin1(protocol),
        str(status_code).encode("ascii", errors="replace"),
        _as_latin1(status_phrase),
    )

    # WAT "Headers" is a dict; values may be a single str or a list of strs
    # when a header repeats. Expand the list form into a flat
    # [(name, value), ...] sequence in the same shape httplint expects.
    json_headers = response_meta.get("Headers", {})
    headers = []
    if isinstance(json_headers, dict):
        for name, val in json_headers.items():
            values = val if isinstance(val, list) else [val]
            for item in values:
                headers.append((_as_latin1(name), _as_latin1(item)))

    linter.process_headers(headers)
    linter.finish_content(True)

    return linter


def _lint_warc_record(record: Any) -> Optional[HttpResponseLinter]:
    """
    Parse a standard WARC response record.
    """
    # Extract status line and headers
    linter = HttpResponseLinter()

    # Set Request URL (base_uri)
    target_uri = record.rec_headers.get_header("WARC-Target-URI")
    if target_uri:
        linter.base_uri = target_uri

    # Set Request Time (start_time)
    warc_date = record.rec_headers.get_header("WARC-Date")
    if warc_date:
        try:
            dt = parse(warc_date)
            linter.start_time = dt.timestamp()
        except (ValueError, TypeError, OverflowError):
            # Malformed or out-of-range WARC-Date; leave start_time unset.
            pass

    # Protocol, status code, phrase
    http_headers = record.http_headers
    if not http_headers:
        return None

    # Protocol
    protocol = http_headers.protocol
    if not protocol:
        protocol = "HTTP/1.1"

    # Status Code
    status_code = http_headers.get_statuscode()

    # Reason Phrase
    parts = http_headers.statusline.split(" ", 1)
    if len(parts) == 2:
        _, status_phrase = parts
    else:
        status_phrase = ""

    linter.process_response_topline(
        _as_latin1(protocol),
        str(status_code).encode("ascii", errors="replace"),
        _as_latin1(status_phrase),
    )

    headers = [
        (_as_latin1(name), _as_latin1(value))
        for name, value in http_headers.headers
    ]
    linter.process_headers(headers)

    # Body
    chunk_size = 8192
    stream = record.content_stream()
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        linter.feed_content(chunk)

    linter.finish_content(True)

    return linter
