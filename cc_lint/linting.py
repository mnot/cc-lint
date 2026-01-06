from httplint import HttpResponseLinter

def lint_record(record):
    """
    Lints a WARC record using httplint.
    Returns the linter object populate with notes, or None if not a response.
    """
    if record.rec_type != 'response':
        return None
    
    # Extract status line and headers
    # warcio records present headers as list of tuples or similar
    # httplint expects raw bytes or structured data. 
    # Let's see how ResponseLinter is initialized.
    # It usually takes `process_response_top(protocol, status_code, status_phrase)`
    # and `process_headers(headers)`.
    
    linter = HttpResponseLinter()
    
    # Set Request URL (base_uri)
    target_uri = record.rec_headers.get_header('WARC-Target-URI')
    if target_uri:
        linter.base_uri = target_uri

    # Set Request Time (start_time)
    # WARC-Date is ISO8601, e.g., 2023-12-05T16:10:51Z
    warc_date = record.rec_headers.get_header('WARC-Date')
    if warc_date:
        try:
            from dateutil.parser import parse
            # dateutil handles iso8601 nicely
            dt = parse(warc_date)
            linter.start_time = dt.timestamp()
        except Exception:
            # Fallback or ignore if date parse fails
            pass
    
    # Protocol, status code, phrase from HTTP header
    # warcio http_headers object has statusline
    http_headers = record.http_headers
    if not http_headers:
        return None

    # Protocol
    protocol = http_headers.protocol
    if not protocol:
        protocol = "HTTP/1.1" # Default or fallback

    # Status Code
    status_code = http_headers.get_statuscode()
    
    # Reason Phrase
    # statusline usually is "200 OK" or "HTTP/1.1 200 OK" depending on how it's stored?
    # warcio statusline property usually returns the full line or the part after protocol?
    # In my debug output: "Status line: 200 OK"
    # So it seems to exclude protocol?
    
    # Let's simple split the statusline.
    # If statusline is '200 OK', then:
    parts = http_headers.statusline.split(' ', 1)
    if len(parts) == 2:
        found_code, status_phrase = parts
        # sanity check if found_code matches status_code
    else:
        status_phrase = ""
        
    linter.process_response_topline(protocol.encode('latin1', errors='replace'), str(status_code).encode('ascii', errors='replace'), status_phrase.encode('latin1', errors='replace'))

    # Headers
    # httplint expects headers as a list of (name, value) tuples, both bytes.
    headers = []
    for name, value in http_headers.headers:
        headers.append((name.encode('latin1', errors='replace'), value.encode('latin1', errors='replace')))
    
    linter.process_headers(headers)

    # Body
    # We can feed the body in chunks
    # record.content_stream() gives us a stream
    chunk_size = 8192
    f = record.content_stream()
    while True:
        chunk = f.read(chunk_size)
        if not chunk:
            break
        linter.feed_content(chunk)
    
    linter.finish_content(True)
    
    return linter
