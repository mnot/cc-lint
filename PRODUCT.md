# Product

## Register

product

## Users

HTTP and web-standards experts: httplint maintainers, IETF/HTTP Workshop
participants, and people fluent in caching, CSP, `Vary`, and response-header
hygiene. They read the report to understand how the real-world web (as seen
through Common Crawl) behaves at the HTTP level — which notes fire, how
broadly, and on how many distinct sites. They arrive knowing the vocabulary;
they want the finding, the magnitude, and the evidence, not an explainer.

Secondary reach: the findings feed a public talk and blog post, so the report
doubles as the canonical artifact those reference. It is not authored *for* a
lay audience, but it should survive being linked to one.

## Product Purpose

A single self-contained HTML report (with a Markdown twin) that summarizes
HTTP-level issues found by linting ~123M Common Crawl responses across ~50k
sites. It exists to turn ~1.2B note occurrences into a ranked, scannable
account of what's wrong (and right) with HTTP responses in the wild.

Success: an expert opens the report and, within seconds, can see the headline
counts, find the most broadly-fired issues, and drill from a one-line note
summary down to sample values and per-variable breakdowns — without the long
tail or the cluster-shuffle mechanics getting in the way. Severity ranking and
site-cardinality estimates do the prioritizing so the reader doesn't have to.

## Brand Personality

Precise, measured, observational. Three words: **rigorous, neutral,
legible.** The voice reports what the data shows and is honest about its
limits — every percentage is scoped to "this Common Crawl result set, not the
entire web," and elided long tails are labeled as such. It does not grade,
scold, or editorialize; severity is a property of the HTTP note, not a verdict
on a site. The aesthetic is closer to a well-set technical paper or a standards
document than to a product UI.

## Anti-references

- **Not a SaaS dashboard.** No hero-metric KPI cards, gauges, sparkline
  theater, or vanity numbers dressed up as a product. The existing stat grid
  is deliberately plain; keep it that way. (The skill's own "hero-metric
  template" ban applies here directly.)
- **Not a security-scanner scare sheet.** No red-everywhere alarmism, no
  letter grades, no per-site naming-and-shaming. The five-level severity
  palette (bad / warn / info / good / clean) is for ranking and legibility,
  not for manufacturing urgency. Measured over loud.
- **Not a raw data dump.** Density is fine; undifferentiated walls of table
  are not. Findings stay ranked by severity, then site reach, then count.

## Design Principles

- **Finding first, mechanics last.** The reader wants the HTTP result. Shuffle
  caps, top-K trims, and truncation are real and must be disclosed — but as
  quiet footnotes (`muted`, `truncated`), never as the headline.
- **Severity is information, not alarm.** Color encodes a note's class so the
  eye can rank; it never shouts. Bad is legible, not blaring.
- **Honest about scope.** Every aggregate carries its denominator and its
  exclusions. Truncated tails, HLL estimates, and result-set scoping are
  surfaced, not hidden — credibility with an expert audience depends on it.
- **Parity across renderers.** HTML and Markdown present the same data; a
  finding that exists in one exists in the other. Neither is the "real" one.
- **Earn density.** This audience tolerates — wants — a lot on the page.
  Spend that budget on real signal (ranked notes, per-var tables, sample
  values), not on decoration.

## Accessibility & Inclusion

Target WCAG AA for the rendered HTML. The current system already does the
right things and they are load-bearing:

- **Semantic markup** — native `<details>`/`<summary>` for collapsible notes,
  real `<table>` for tabular data, heading hierarchy for structure. Keep new
  sections semantic rather than div-soup.
- **Light and dark themes** via `prefers-color-scheme`; both palettes must
  hold contrast. Verify body text ≥4.5:1 and the semantic badge/segment colors
  against their own backgrounds in **both** schemes when touching the palette.
- **Color is never the only channel.** Severity is carried by badge text and
  ordering as well as hue, so the five-level scale survives color-blindness and
  grayscale printing.
- **Tabular numerics** (`font-variant-numeric: tabular-nums`) for all counts so
  columns align and scan cleanly.
