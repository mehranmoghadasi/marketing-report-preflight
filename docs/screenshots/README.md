# Interface, described

No image files are committed. Run `report-preflight serve` and you will see exactly this.

## Overview (default view)

A two-column layout: a 256-px white sidebar on the left, content on the right. The sidebar
holds the product mark (a small indigo-to-violet rounded square), the version, three nav
buttons (Overview, Run history, Checks & waivers) with the active one filled pale indigo,
and a client `<select>` under a small-caps "Client" label with `10 checks · 11 sources`
beneath it. On screens narrower than 768 px the sidebar becomes a top bar with a "Menu"
button that toggles it.

A sticky, semi-transparent top bar carries the client name on the left and, on the right, a
`Period` field pre-filled with last month and an indigo **Run preflight** button. Pressing
it disables the button, relabels it "Running…", and shows four shimmering grey skeleton
blocks where the results will appear.

The verdict card comes back with a red **FAIL** pill, the sentence *Do not send yet*, the
period and check count, and a four-column figure row: Checks run 10 · Blocking 3 · Warnings
5 · Accepted 0.

Below it, one card per check, in config order. Each row is a status pill (green PASS, amber
WARN, red FAIL), the check id in medium weight, and the one-line summary in grey, truncated
to a single line. Clicking anywhere on the row rotates the small chevron 90° and eases open
the detail body: the client-facing sentence for that check, then the full detail JSON in a
monospace block on a pale background. A waived check carries an extra amber "accepted" pill.

The last card is the data-notes panel — an indigo left border, a small-caps heading, a
**Copy** button that briefly reads "Copied", and the composed Markdown in pre-wrapped
14-px text.

## Run history

A card containing a hand-drawn SVG bar chart of the last twelve runs: one bar per period,
height proportional to the number of checks that were not clean, coloured by the run
verdict (green / amber / red), the count above each bar and the abbreviated period below.
Underneath, a table — Run, Period, Status, Checks, Blocking, Warnings, Recorded — where each
row highlights on hover and loads that run into the Overview when clicked. With no runs
yet, the table shows a single centred grey line instead.

## Checks & waivers

A table of every configured check: title, the check type as inline code, the plain-language
description of what it verifies, and a waiver column. Unwaived checks show an "Accept…"
button that asks for a client-safe reason and a name; once recorded the cell shows an amber
"accepted" pill with the reason underneath. A footnote explains that the reason is quoted
in the client data notes.

## States and details

- **Error**: a rose-tinted banner with `role="alert"` above the content, used for a bad
  period, an unknown client, a missing config directory, or any API error.
- **Empty**: a dashed-border card reading "No preflight run for this period yet".
- **Loading**: the shimmering skeletons described above, in a `aria-live="polite"` region.
- Every interactive element has a 2-px indigo focus ring; the SVG chart carries
  `role="img"` and a label; animations are disabled under `prefers-reduced-motion`.
- Spacing is a 4/8-pt scale throughout; cards use a 10-px radius and a 1-px slate border.

## Standalone HTML page

`preflight.html` (see `examples/output/preflight.html`) is the same content without the
dashboard chrome: a header with the verdict pill, the figure row, `<details>` blocks per
check, the data notes in a bordered panel, and a footer stating the verdict rule. It has no
external assets and a print stylesheet that keeps check cards from breaking across pages.
