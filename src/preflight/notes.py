"""Compose the client-facing "data notes" block.

This is the artefact the rest of the tool exists to produce. Every check that is not a
clean pass contributes one plain-language sentence written for the client, not for the
agency: no check ids, no file names, no tool names, no metric API names. Paste the block
into the report (or attach it) and the awkward conversation happens on your terms, in
writing, before the client finds the discrepancy themselves.

The block is deterministic: same run, same bytes. No timestamps.
"""

from __future__ import annotations

from .model import RunResult, Severity

_INTRO = {
    Severity.PASS: (
        "The data behind this report passed every quality check we run before sending: "
        "the reporting period is complete, tracking recorded what it should, and nothing "
        "material changed in how the numbers are collected."
    ),
    Severity.WARN: (
        "Before sending this report we checked the data behind it. The figures are usable, "
        "with the following caveats:"
    ),
    Severity.FAIL: (
        "Before sending this report we checked the data behind it and found problems that "
        "affect the figures shown. Please read these notes alongside the numbers:"
    ),
}


def note_lines(run: RunResult) -> list[str]:
    """Client-safe sentences, worst first, de-duplicated, stable order."""
    ordered = sorted(
        (result for result in run.results if result.status is not Severity.PASS and result.note),
        key=lambda result: (0 if result.status is Severity.FAIL else 1, run.results.index(result)),
    )
    lines: list[str] = []
    for result in ordered:
        text = (result.note or "").strip()
        if text and text not in lines:
            lines.append(text)
    return lines


def compose(run: RunResult) -> str:
    """Return the data-notes block as Markdown."""
    counts = run.counts()
    header = f"## Data notes — {run.client_name} · {run.period}"
    body = [header, "", _INTRO[run.status]]
    lines = note_lines(run)
    if lines:
        body.append("")
        body.extend(f"- {line}" for line in lines)
    waived = [result for result in run.results if result.waived]
    if waived:
        body.append("")
        body.append(
            "One or more of the issues above were reviewed and accepted before sending: "
            + "; ".join(
                sorted((result.waiver.reason or "").strip() for result in waived if result.waiver)
            )
            + "."
        )
    body.append("")
    body.append(
        f"_{counts['total']} data checks run · {len(run.blocking())} blocking · "
        f"{counts['warned']} caveats · {counts['waived']} accepted. "
        f"Report Preflight v{run.tool_version}._"
    )
    return "\n".join(body) + "\n"
