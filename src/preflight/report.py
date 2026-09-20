"""Render a run as a standalone HTML readiness page and as console text.

The HTML file has no external assets and no timestamps, so it can be emailed, committed,
or attached to a client folder, and two runs over the same data produce identical bytes.
"""

from __future__ import annotations

import html
import json

from .model import RunResult, Severity
from .notes import compose

_PILL = {
    Severity.PASS: ("#065f46", "#d1fae5", "PASS"),
    Severity.WARN: ("#92400e", "#fef3c7", "WARN"),
    Severity.FAIL: ("#991b1b", "#fee2e2", "FAIL"),
}

_CSS = """
:root { --ink:#0f172a; --muted:#64748b; --line:#e2e8f0; --bg:#f8fafc; }
* { box-sizing: border-box; }
body { margin:0; padding:32px; background:var(--bg); color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
.wrap { max-width: 880px; margin:0 auto; }
header { display:flex; align-items:flex-start; justify-content:space-between; gap:16px;
  padding-bottom:16px; border-bottom:1px solid var(--line); flex-wrap:wrap; }
h1 { font-size:20px; margin:0 0 4px; }
h2 { font-size:15px; margin:32px 0 8px; text-transform:uppercase; letter-spacing:.06em;
  color:var(--muted); }
.sub { color:var(--muted); font-size:13px; }
.pill { display:inline-block; padding:6px 14px; border-radius:999px; font-weight:700;
  font-size:13px; letter-spacing:.04em; }
.verdict { font-size:17px; font-weight:600; margin:16px 0 0; }
.counts { display:flex; gap:24px; margin:16px 0 0; flex-wrap:wrap; }
.counts div { font-size:13px; color:var(--muted); }
.counts strong { display:block; font-size:22px; color:var(--ink); }
.check { background:#fff; border:1px solid var(--line); border-radius:10px; padding:14px 16px;
  margin:8px 0; }
.check summary { cursor:pointer; display:flex; gap:12px; align-items:baseline;
  list-style:none; }
.check summary::-webkit-details-marker { display:none; }
.check .name { font-weight:600; }
.check .msg { color:var(--muted); font-size:13px; }
.check pre { background:var(--bg); border:1px solid var(--line); border-radius:8px;
  padding:12px; overflow:auto; font-size:12px; margin:12px 0 0; }
.waived { color:#92400e; font-size:12px; font-weight:600; }
.notes { background:#fff; border:1px solid var(--line); border-left:4px solid #1d4fd7;
  border-radius:10px; padding:16px 18px; white-space:pre-wrap; font-size:14px; }
footer { margin-top:32px; color:var(--muted); font-size:12px;
  border-top:1px solid var(--line); padding-top:12px; }
@media print { body { background:#fff; padding:0; } .check { break-inside: avoid; } }
"""


def render_html(run: RunResult) -> str:
    counts = run.counts()
    colour, background, label = _PILL[run.status]
    rows = []
    for result in run.results:
        r_colour, r_background, r_label = _PILL[result.status]
        waived = (
            f'<span class="waived">accepted: {html.escape(result.waiver.reason)}</span>'
            if result.waiver and result.waived
            else ""
        )
        rows.append(
            "<details class='check'>"
            "<summary>"
            f"<span class='pill' style='color:{r_colour};"
            f"background:{r_background}'>{r_label}</span>"
            f"<span class='name'>{html.escape(result.check_id)}</span>"
            f"<span class='msg'>{html.escape(result.summary)}</span>"
            f"{waived}"
            "</summary>"
            f"<pre>{html.escape(json.dumps(result.detail, indent=2, sort_keys=True))}</pre>"
            "</details>"
        )
    notes_md = compose(run)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Report Preflight — {html.escape(run.client_name)} {html.escape(run.period)}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<header>
  <div>
    <h1>Report Preflight — {html.escape(run.client_name)}</h1>
    <div class="sub">Reporting period {html.escape(run.period)}</div>
  </div>
  <span class="pill" style="color:{colour};background:{background}">{label}</span>
</header>
<p class="verdict">{html.escape(run.verdict)}</p>
<div class="counts">
  <div><strong>{counts["total"]}</strong>checks run</div>
  <div><strong>{counts["blocking"]}</strong>blocking</div>
  <div><strong>{counts["warned"]}</strong>warnings</div>
  <div><strong>{counts["waived"]}</strong>accepted</div>
</div>
<h2>Checks</h2>
{"".join(rows)}
<h2>Client-facing data notes</h2>
<div class="notes">{html.escape(notes_md)}</div>
<footer>Report Preflight v{html.escape(run.tool_version)} — verdict is the worst
unwaived check status (fail &gt; warn &gt; pass).</footer>
</div></body></html>
"""


def render_console(run: RunResult, colour: bool = True) -> str:
    codes = {
        Severity.PASS: "\033[92m",
        Severity.WARN: "\033[93m",
        Severity.FAIL: "\033[91m",
    }
    reset = "\033[0m"

    def paint(status: Severity, text: str) -> str:
        return f"{codes[status]}{text}{reset}" if colour else text

    counts = run.counts()
    lines = [
        f"Report Preflight — {run.client_name} · {run.period}",
        "-" * 60,
    ]
    for result in run.results:
        mark = {Severity.PASS: "PASS", Severity.WARN: "WARN", Severity.FAIL: "FAIL"}[result.status]
        suffix = " (accepted)" if result.waived else ""
        lines.append(f"{paint(result.status, mark)}  {result.check_id}{suffix}")
        lines.append(f"      {result.summary}")
    lines += [
        "-" * 60,
        f"{paint(run.status, run.verdict.upper())}  "
        f"({counts['blocking']} blocking, {counts['warned']} warnings, "
        f"{counts['waived']} accepted, {counts['total']} checks)",
    ]
    return "\n".join(lines)
