"""Static text constants used by the report renderer.

Kept separate from the section builders so the CSS block and the
methodology / truncation explanatory text can be reviewed and
edited in isolation.
"""

METHODOLOGY_NOTE = (
    "Percentages describe this Common Crawl result set, not the entire web. "
    "Counts reflect what Common Crawl fetched (after robots.txt, WAF, paywall, "
    "and geofence exclusions), scoped to the Tranco top-sites filter "
    "configured for this run."
)


TRUNCATED_NOTE = (
    '<p class="muted truncated">The long tail of rare values was elided during '
    "shuffle to keep cluster memory bounded; counts and percentages below "
    "describe the retained head only.</p>"
)


STYLE = """
  :root {
    --bg: #fafafa;
    --fg: #1c1c1c;
    --muted: #5f6168;
    --card: #ffffff;
    --card-border: #e7e7ea;
    --link: #1f4ed8;
    --warn-bg: #fff8e1;
    --warn-fg: #8a5400;
    --warn-border: #f0c356;
    --bad-bg: #fdecec;
    --bad-fg: #a31515;
    --bad-border: #e9a0a0;
    --info-bg: #e7f0fb;
    --info-fg: #1b4079;
    --info-border: #b6c9e3;
    --good-bg: #e6f4ea;
    --good-fg: #1e7c3a;
    --good-border: #aed4ba;
    --clean-bg: #ececec;
    --clean-fg: #555;
    --clean-border: #c8c8c8;
    --row-alt: #f5f5f7;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0f1115;
      --fg: #e7e8ea;
      --muted: #9aa0a6;
      --card: #16181d;
      --card-border: #2a2d33;
      --link: #8ab4ff;
      --warn-bg: #2a2218;
      --warn-fg: #ffcb74;
      --warn-border: #5b4626;
      --bad-bg: #2b1818;
      --bad-fg: #ff9c9c;
      --bad-border: #5a2828;
      --info-bg: #182333;
      --info-fg: #8ab4ff;
      --info-border: #2c3f5f;
      --good-bg: #16291e;
      --good-fg: #8edf9f;
      --good-border: #2a5333;
      --clean-bg: #1c1e23;
      --clean-fg: #aaa;
      --clean-border: #383b42;
      --row-alt: #1a1c22;
    }
  }
  * { box-sizing: border-box; }
  html { background: var(--bg); color: var(--fg); }
  body {
    font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    margin: 0 auto;
    max-width: 64rem;
    padding: 2rem 1.25rem 4rem;
  }
  a { color: var(--link); text-decoration: none; }
  a:hover { text-decoration: underline; }
  h1, h2, h3, h4 { line-height: 1.25; margin: 0 0 .5rem; }
  h1 { font-size: 1.75rem; }
  h2 { font-size: 1.25rem; margin-top: 2rem; border-bottom: 1px solid var(--card-border); padding-bottom: .25rem; }
  h3 { font-size: 1rem; margin-top: 1rem; }
  h4 { font-size: .9rem; font-weight: 600; margin-top: 1rem; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
  .muted { color: var(--muted); font-size: .85em; }
  .vars { display: block; color: var(--muted); font-size: .85em; margin-left: 1.5em; }
  .cooccur-headline { font-size: 1.05em; margin: .5rem 0 1rem; }

  .hero { padding: 1rem 0 1.5rem; border-bottom: 1px solid var(--card-border); margin-bottom: 1.5rem; }
  .stat-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
    gap: 1rem;
    margin: 1rem 0 0;
  }
  .stat-grid div {
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: .5rem;
    padding: .75rem 1rem;
  }
  .stat-grid dt { font-size: .8em; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
  .stat-grid dd { margin: .25rem 0 0; font-size: 1.5rem; font-weight: 600; }
  .stat-grid dd small { font-size: .65em; font-weight: 400; color: var(--muted); }

  .note-list { display: flex; flex-direction: column; gap: .5rem; }
  details.note {
    background: var(--card);
    border: 1px solid var(--card-border);
    border-left: 4px solid var(--card-border);
    border-radius: .375rem;
    padding: 0;
  }
  details.note.severity-bad { border-left-color: var(--bad-border); }
  details.note.severity-warn { border-left-color: var(--warn-border); }
  details.note.severity-info { border-left-color: var(--info-border); }
  details.note.severity-good { border-left-color: var(--good-border); }
  details.note > summary {
    cursor: pointer;
    padding: .5rem .75rem;
    display: flex;
    align-items: center;
    gap: .5rem;
    list-style: none;
  }
  details.note > summary::-webkit-details-marker { display: none; }
  details.note > summary::before { content: "▸"; color: var(--muted); transition: transform .15s; }
  details.note[open] > summary::before { transform: rotate(90deg); display: inline-block; }
  .badge {
    display: inline-block;
    padding: .1rem .4rem;
    border-radius: .25rem;
    font-size: .7em;
    font-weight: 600;
    letter-spacing: .04em;
    border: 1px solid;
  }
  .badge-bad { background: var(--bad-bg); color: var(--bad-fg); border-color: var(--bad-border); }
  .badge-warn { background: var(--warn-bg); color: var(--warn-fg); border-color: var(--warn-border); }
  .badge-info { background: var(--info-bg); color: var(--info-fg); border-color: var(--info-border); }
  .badge-good { background: var(--good-bg); color: var(--good-fg); border-color: var(--good-border); }
  .badge-clean { background: var(--clean-bg); color: var(--clean-fg); border-color: var(--clean-border); }
  .note-id { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: .9em; }
  .note-sites {
    background: var(--row-alt);
    border: 1px solid var(--card-border);
    border-radius: .25rem;
    color: var(--muted);
    font-size: .75em;
    padding: .05rem .35rem;
    font-variant-numeric: tabular-nums;
  }
  .note-count { margin-left: auto; font-variant-numeric: tabular-nums; color: var(--muted); }
  .note-body { padding: .25rem .75rem .75rem; }
  .note-body > ul.samples { margin: 0 0 .75rem; }

  ul.samples { margin: .5rem 0; padding-left: 1.25rem; }
  ul.samples li { word-break: break-all; line-height: 1.4; }
  ul.errors { margin: 0; padding-left: 1.25rem; }
  ul.errors > li { margin: .25rem 0; }
  ul.errors .err { font-family: ui-monospace, "SF Mono", Menlo, monospace; }

  details.field-samples { margin: .35rem 0 0; }
  details.field-samples > summary { cursor: pointer; color: var(--muted); font-size: .85em; padding: .35rem 0; }
  .field-val {
    display: block;
    margin-left: 1.5em;
    font-size: .8em;
    color: var(--muted);
    word-break: break-all;
  }

  table.var-table, table.data-table {
    border-collapse: collapse;
    width: 100%;
    margin: .5rem 0 1rem;
    font-size: .9em;
  }
  table.var-table th, table.var-table td,
  table.data-table th, table.data-table td {
    text-align: left;
    padding: .35rem .5rem;
    border-bottom: 1px solid var(--card-border);
    vertical-align: top;
  }
  table.var-table thead th, table.data-table thead th {
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
    font-size: .75em;
    letter-spacing: .04em;
    border-bottom: 2px solid var(--card-border);
  }
  table.var-table tbody tr:nth-child(odd) { background: var(--row-alt); }

  .note-summary { margin: .25rem 0 .75rem; max-width: 72ch; color: var(--fg); font-style: italic; }
  details.note-samples { margin: .25rem 0 .75rem; }
  details.note-samples > summary { cursor: pointer; color: var(--muted); font-size: .9em; padding: .35rem 0; }

  .missing-list { columns: 2 14rem; column-gap: 1.25rem; font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: .85em; }
  .missing-list li { break-inside: avoid; }

  .truncated {
    background: var(--warn-bg);
    border: 1px solid var(--warn-border);
    color: var(--warn-fg);
    margin: .5rem 0;
    padding: .35rem .6rem;
    border-radius: .25rem;
  }

  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: -1px;
    padding: 0;
    overflow: hidden;
    clip: rect(0 0 0 0);
    clip-path: inset(50%);
    white-space: nowrap;
    border: 0;
  }

  section.run-context { margin-top: 1rem; }
  .run-pills { display: flex; flex-wrap: wrap; gap: .4rem; margin: 0 0 .75rem; }
  .run-pill {
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 999px;
    padding: .15rem .65rem;
    display: inline-flex;
    align-items: baseline;
    gap: .4rem;
    font-size: .82em;
  }
  .run-pill.pill-warning {
    background: var(--warn-bg);
    border-color: var(--warn-border);
    color: var(--warn-fg);
  }
  .pill-label { color: var(--muted); font-size: .9em; }
  .pill-value { font-weight: 600; font-variant-numeric: tabular-nums; }
  .methodology {
    color: var(--muted);
    font-size: .85em;
    margin: 0 0 .5rem;
    max-width: 72ch;
  }

  table.csp-table td:last-child { width: 220px; }
  span.csp-bar {
    background: var(--link);
    border-radius: 2px;
    display: inline-block;
    height: 8px;
    vertical-align: middle;
    min-width: 1px;
  }

  span.vary-synthetic,
  span.cc-synthetic {
    color: var(--bad-fg);
    font-weight: 600;
  }

  .health-bar {
    display: flex;
    width: 100%;
    height: 1.5rem;
    border-radius: .25rem;
    overflow: hidden;
    margin: .75rem 0;
    border: 1px solid var(--card-border);
  }
  .health-seg { display: block; height: 100%; }
  .health-seg-bad { background: var(--bad-fg); }
  .health-seg-warn { background: var(--warn-fg); }
  .health-seg-info { background: var(--info-fg); }
  .health-seg-good { background: var(--good-fg); }
  .health-seg-clean { background: var(--clean-fg); }

  section.note-category h3 { font-size: 1.05rem; margin-top: 1.5rem; }
  section.note-category .cat-totals {
    color: var(--muted);
    font-size: .8em;
    font-weight: 400;
    margin-left: .5em;
  }

  section { margin-top: 2.5rem; }
  section:first-of-type { margin-top: 0; }

  /* Table of contents. Inline collapsible block by default; docked into the
     left gutter on wide viewports where there's room beside the centred
     column. */
  .toc { margin: 0 0 2rem; }
  .toc details {
    border: 1px solid var(--card-border);
    border-radius: .5rem;
    background: var(--card);
    padding: .1rem .85rem;
    font-size: .85em;
  }
  .toc summary {
    cursor: pointer;
    padding: .5rem 0;
    color: var(--muted);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .04em;
    font-size: .85em;
  }
  .toc ul { list-style: none; margin: .25rem 0 .6rem; padding-left: 0; }
  .toc li { margin: .12rem 0; line-height: 1.35; }
  .toc a { color: var(--fg); }
  .toc a:hover { color: var(--link); text-decoration: underline; }
  .toc a.active { color: var(--link); font-weight: 600; }
  .toc .toc-sub {
    margin: .15rem 0 .4rem;
    padding-left: .85rem;
    border-left: 1px solid var(--card-border);
  }
  .toc .toc-sub a { color: var(--muted); }
  .toc .toc-link { margin: .35rem 0 .2rem; font-size: .9em; }
  .toc .toc-link a { color: var(--muted); }
  .toc .toc-link a:hover { color: var(--link); }

  @media (min-width: 96rem) {
    .toc {
      position: fixed;
      top: 2rem;
      left: calc((100vw - 64rem) / 2 - 15rem);
      width: 13.5rem;
      margin: 0;
      max-height: calc(100vh - 4rem);
      overflow-y: auto;
    }
    .toc details { padding: .35rem .85rem .6rem; }
    /* Docked: stay open, drop the disclosure affordance. */
    .toc summary { pointer-events: none; list-style: none; }
    .toc summary::-webkit-details-marker { display: none; }
    .toc summary::marker { content: ""; }
  }

  /* Narrow viewports: let wide tables scroll inside themselves rather than
     forcing the whole page to scroll horizontally. */
  @media (max-width: 40rem) {
    table.var-table, table.data-table, table.csp-table {
      display: block;
      overflow-x: auto;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    details.note > summary::before { transition: none; }
  }
"""
