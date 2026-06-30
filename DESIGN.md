---
name: Common Crawl Response Lint
description: A self-contained HTML report of HTTP-hygiene findings across the crawled web — a lab notebook, not a dashboard.
colors:
  ink: "#1c1c1c"
  canvas: "#fafafa"
  surface: "#ffffff"
  muted: "#5f6168"
  border: "#e7e7ea"
  row-alt: "#f5f5f7"
  link: "#1f4ed8"
  bad-fg: "#a31515"
  bad-bg: "#fdecec"
  bad-border: "#e9a0a0"
  warn-fg: "#8a5400"
  warn-bg: "#fff8e1"
  warn-border: "#f0c356"
  info-fg: "#1b4079"
  info-bg: "#e7f0fb"
  info-border: "#b6c9e3"
  good-fg: "#1e7c3a"
  good-bg: "#e6f4ea"
  good-border: "#aed4ba"
  clean-fg: "#555555"
  clean-bg: "#ececec"
  clean-border: "#c8c8c8"
typography:
  display:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "normal"
  headline:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "normal"
  title:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1rem"
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "normal"
  body:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.9rem"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "0.04em"
  mono:
    fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace"
    fontSize: "0.9em"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "normal"
rounded:
  sm: "4px"
  md: "6px"
  lg: "8px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "32px"
components:
  badge-bad:
    backgroundColor: "{colors.bad-bg}"
    textColor: "{colors.bad-fg}"
    rounded: "{rounded.sm}"
    padding: "0.1rem 0.4rem"
    typography: "{typography.label}"
  badge-warn:
    backgroundColor: "{colors.warn-bg}"
    textColor: "{colors.warn-fg}"
    rounded: "{rounded.sm}"
    padding: "0.1rem 0.4rem"
    typography: "{typography.label}"
  badge-info:
    backgroundColor: "{colors.info-bg}"
    textColor: "{colors.info-fg}"
    rounded: "{rounded.sm}"
    padding: "0.1rem 0.4rem"
    typography: "{typography.label}"
  badge-good:
    backgroundColor: "{colors.good-bg}"
    textColor: "{colors.good-fg}"
    rounded: "{rounded.sm}"
    padding: "0.1rem 0.4rem"
    typography: "{typography.label}"
  badge-clean:
    backgroundColor: "{colors.clean-bg}"
    textColor: "{colors.clean-fg}"
    rounded: "{rounded.sm}"
    padding: "0.1rem 0.4rem"
    typography: "{typography.label}"
  note-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0"
  run-pill:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: "0.15rem 0.65rem"
  stat-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "0.75rem 1rem"
---

# Design System: Common Crawl Response Lint

## 1. Overview

**Creative North Star: "The Lab Notebook"**

This is the rendered output of an instrument, not a product. ~123M HTTP
responses go in; a ranked, scannable account of what they got right and wrong
comes out. The design serves that reading the way a well-kept lab notebook
serves an experiment: every figure carries its denominator, every elided long
tail is annotated where it was cut, and the apparatus (shuffle caps, HLL
estimates, top-K trims) is recorded in the margin rather than hidden. The
reader is an HTTP expert — caching, CSP, `Vary`, header hygiene are their
native vocabulary — so the notebook earns density and spends it on signal:
ranked notes, per-variable tables, sample values. Nothing is dressed up.

The page is **flat by construction.** There is not a single `box-shadow` in
the system. Depth is carried entirely by 1px borders, tonal row striping, and
a restrained type scale — the look of a typeset technical paper, not a layered
app UI. Light and dark are equal citizens via `prefers-color-scheme`; neither
is the "real" theme. The one identity move is the **five-level severity
palette** (bad / warn / info / good / clean), and it reads as *clinical
signal*: precise instrument-panel readouts that rank and group, never alarm.

What this system explicitly rejects: the **SaaS dashboard** (no hero-metric
KPI theater, no gauges, no vanity numbers — the stat grid is deliberately
plain), and the **security-scanner scare sheet** (no red-everywhere
alarmism, no letter grades, no naming-and-shaming sites). Severity is a
property of the HTTP note, not a verdict on anyone.

**Key Characteristics:**
- Flat by construction — borders and tonal layering, zero shadows.
- Density earned and spent on signal, never on decoration.
- Severity as a sorting channel, carried by text + order as well as hue.
- Honest apparatus: denominators, exclusions, and truncation always visible.
- Dual-theme parity; system-ui type; tabular numerics throughout.

## 2. Colors

A near-neutral grayscale chassis (true sRGB neutrals, no warm tint) carrying a
single functional blue and one five-level semantic severity scale. There is no
decorative brand accent; every saturated color means something.

### Primary
- **Reference Blue** (`#1f4ed8`, dark `#8ab4ff`): The sole functional accent —
  links and the CSP usage bars only. It is interaction and measurement, never
  decoration. Carries ~5% of any screen.

### Secondary — The Severity Scale
Five classification levels. Each is a triad: a saturated **foreground** (badge
text, health-bar segment fill), a pale **background** tint (badge fill, callout
ground), and a mid **border**. Listed worst-to-best, which is also their sort
order on the page.
- **Bad / Red** (fg `#a31515`, bg `#fdecec`, border `#e9a0a0`): Notes the
  linter classes as errors.
- **Warn / Amber** (fg `#8a5400`, bg `#fff8e1`, border `#f0c356`): Cautions;
  also the ground for the truncation callout and run-config warning pills.
- **Info / Blue** (fg `#1b4079`, bg `#e7f0fb`, border `#b6c9e3`):
  Informational notes. Distinct in hue from Reference Blue's role.
- **Good / Green** (fg `#1e7c3a`, bg `#e6f4ea`, border `#aed4ba`): Positive
  findings — correct behavior worth surfacing.
- **Clean / Gray** (fg `#555555`, bg `#ececec`, border `#c8c8c8`): The "no
  issue / neutral" classification. Deliberately desaturated so it recedes.

### Neutral
- **Ink** (`#1c1c1c`, dark `#e7e8ea`): Body text.
- **Muted** (`#5f6168`, dark `#9aa0a6`): Secondary text, table headers, methodology and truncation footnotes, eyebrow labels.
- **Canvas** (`#fafafa`, dark `#0f1115`): Page background. A true neutral near-white — chroma ~0, not a warm cream.
- **Surface** (`#ffffff`, dark `#16181d`): Cards, note bodies, pills, stat tiles.
- **Border** (`#e7e7ea`, dark `#2a2d33`): All 1px dividers and card strokes.
- **Row-Alt** (`#f5f5f7`, dark `#1a1c22`): Zebra striping on var tables and the site-count chip ground.

### Named Rules
**The Meaning-Only Saturation Rule.** Saturated color is reserved for the
functional blue and the five severity levels. Nothing decorative is allowed to
be colorful. If a color appears, it classifies something.

**The Recede-on-Clean Rule.** The further a finding is from "error", the
quieter its color. Clean is gray on purpose; it must never compete with Bad
for the eye.

## 3. Typography

**Display / Body Font:** the native system-ui stack (`system-ui`,
`-apple-system`, `'Segoe UI'`, `Roboto`, sans-serif). One family, multiple
weights and sizes — no second face.
**Label / Mono Font:** `ui-monospace`, `'SF Mono'`, `Menlo`, monospace — for
note IDs, raw header values, sample strings, and the missing-notes list.

**Character:** Deliberately font-less. Using the reader's own OS UI font keeps
the report feeling like a system artifact — a readout, not a designed
publication. The mono face does the opposite job: it flags machine literals
(header names, values, IDs) as quotable evidence. The two-axis pairing
(proportional sans vs. mono) is the only type contrast the system needs.

### Hierarchy
- **Display** (h1, 700, 1.75rem, lh 1.25): The single page title. One per report.
- **Headline** (h2, 700, 1.25rem, lh 1.25): Section headings, with a 1px bottom rule for separation.
- **Title** (h3, 700, 1rem): Sub-sections and category group headings.
- **Body** (400, 15px, lh 1.5): All prose and table cells. Page width capped at `64rem` so lines stay readable.
- **Label** (h4 / eyebrows, 600, 0.9rem, uppercase, tracking 0.04em, muted): Sub-headers and table column heads. Functional, not decorative.
- **Mono** (400, 0.9em): Note IDs, header literals, sample values.

### Named Rules
**The System-Font Rule.** No web fonts, ever. The report renders in the
reader's own UI font so it reads as an instrument's output. A loaded display
face would make it a brochure.

**The Mono-Means-Literal Rule.** Monospace is reserved for machine literals
(IDs, header names, raw values). Never use it for emphasis or headings.

## 4. Elevation

**There are no shadows in this system.** Depth is conveyed entirely through
1px borders, tonal layering (Canvas → Surface → Row-Alt), and a single
header underline. This is the typeset-paper model: a notebook page has no
drop shadows, and neither does this. Surfaces sit flat at every state.

### Named Rules
**The No-Shadow Rule.** `box-shadow` is forbidden. If a surface needs to read
as distinct, give it a border or a tonal step, not a shadow. If something
looks like it's floating, the design has failed.

## 5. Components

### Severity Badges
The signature primitive. A small uppercase chip naming a classification.
- **Shape:** 4px radius (`rounded.sm`), 1px border in the matching severity border color.
- **Color:** background = severity `-bg`, text = severity `-fg`, border = severity `-border`. Five variants: bad / warn / info / good / clean.
- **Type:** 0.7em, weight 600, tracking 0.04em, uppercase.
- **Behavior:** Static. Badges classify; they don't interact.

### Note Cards (`details.note`)
The core content unit — a native `<details>`/`<summary>` disclosure per HTTP note, sorted by severity then site reach then count.
- **Corner Style:** 6px radius (`rounded.md`).
- **Background:** Surface; **Border:** 1px Border all around, with a heavier 4px left edge tinted to the note's severity border color (this is the one sanctioned colored left edge — it encodes severity classification on a native disclosure widget, not a decorative stripe).
- **Summary row:** severity badge, monospace note ID, a Row-Alt site-count chip (`note-sites`, with HLL tooltip), and a right-aligned tabular occurrence count. The marker is a custom `▸` that rotates 90° on open (0.15s).
- **Shadow Strategy:** none (see Elevation).

### Run-Context Pills (`run-pill`)
Rounded-full chips in a `flex-wrap` row carrying run metadata (label + tabular value).
- **Shape:** 999px (`rounded.pill`), 1px Border, Surface background.
- **Warning variant** (`pill-warning`): Warn palette — flags when a run was capped (record/WARC limits) so figures aren't mistaken for a full run.

### Stat Grid (`stat-grid`)
The header summary — a `repeat(auto-fit, minmax(12rem, 1fr))` grid of plain tiles. **Not** hero metrics: muted uppercase label (`dt`) over a 1.5rem value (`dd`) with a smaller muted qualifier. No accent color, no gradient, no icon. Deliberately quiet.

### Tables (`var-table` / `data-table`)
- **Style:** Full-width, 0.9em, collapsed borders. Header row is muted uppercase label type with a 2px bottom rule; body rows divided by 1px Border.
- **Var tables** zebra-stripe odd rows with Row-Alt. Counts use tabular numerics.

### Health Bar (`health-bar`)
A single horizontal stacked bar, 1.5rem tall, 0.25rem radius, segmented by severity (`health-seg-*` using the saturated `-fg` fills) to show the severity mix at a glance. A proportion strip, not a gauge.

### CSP Usage Bars (`csp-bar`)
Inline horizontal bars in Reference Blue, fixed 8px height, min-width 1px, in a 220px-wide table column — lightweight magnitude marks beside values, not a charting library.

## 6. Do's and Don'ts

### Do:
- **Do** keep the system flat — convey depth with 1px Border, the Canvas→Surface→Row-Alt tonal steps, and header underlines only.
- **Do** reserve saturated color for the functional blue and the five severity levels; if a color appears, it must classify something (**The Meaning-Only Saturation Rule**).
- **Do** carry severity in text and sort order, not hue alone, so the scale survives color-blindness and grayscale printing.
- **Do** render figures with `font-variant-numeric: tabular-nums` and keep every aggregate's denominator and exclusions visible (methodology + truncation footnotes in Muted).
- **Do** verify contrast in **both** light and dark schemes whenever you touch the palette: body ≥4.5:1, badges/segments ≥3:1 against their own ground.
- **Do** use native `<details>`, `<table>`, and heading hierarchy; keep new sections semantic.
- **Do** keep the report's HTML and Markdown twins in parity — a finding in one exists in the other.

### Don't:
- **Don't** build a **SaaS dashboard**: no hero-metric KPI cards, gauges, sparkline theater, or vanity numbers. The stat grid stays plain.
- **Don't** build a **security-scanner scare sheet**: no red-everywhere alarmism, no letter grades, no naming-and-shaming sites. Measured over loud.
- **Don't** add `box-shadow` anywhere (**The No-Shadow Rule**). If a surface looks like it's floating, it's wrong.
- **Don't** introduce a web font or a second proportional face (**The System-Font Rule**); use monospace only for machine literals (**The Mono-Means-Literal Rule**).
- **Don't** use a colored `border-left` as decoration. The only sanctioned one is the 4px severity edge on note cards, and it encodes classification — not an accent stripe on arbitrary callouts.
- **Don't** let Clean compete with Bad for attention; the gray-on-purpose Clean level must recede (**The Recede-on-Clean Rule**).
- **Don't** let the apparatus (shuffle caps, top-K trims, HLL math) become a headline; record it in the margin as a Muted footnote.
