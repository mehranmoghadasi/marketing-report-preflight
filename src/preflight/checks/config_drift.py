"""Configuration-drift check — a ``diff`` for the settings behind the numbers.

Attribution windows, conversion counting, data filters and channel groupings all change
the meaning of a metric without changing its name. When one of them moves between two
periods, a month-over-month comparison is no longer like-for-like, and nothing in a
reporting tool tells you.

This check diffs two JSON settings snapshots (however you produce them — an export, a
screenshot transcribed into JSON, a few lines of API output) at a list of watched dot
paths, and reports every watched value that changed, appeared or disappeared.
"""

from __future__ import annotations

import json

from ..model import CheckError, CheckResult, Severity, worst
from ..sources import dotted
from . import CheckContext, CheckDef
from .ga4_response import severity_param


def _render(value: object) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def run_config_drift(ctx: CheckContext) -> CheckResult:
    current = ctx.source("source")
    previous = ctx.source("previous_source")
    watched = ctx.required_param("watched_keys")
    if isinstance(watched, str):
        watched = [watched]
    if not isinstance(watched, list) or not watched:
        raise CheckError(
            f"check {ctx.spec.id!r}: watched_keys must be a non-empty list of dot paths"
        )

    on_change = severity_param(ctx, "on_change", "fail")
    on_missing = severity_param(ctx, "on_missing", "warn")

    changes: list[dict] = []
    findings: list[tuple[Severity, str]] = []
    for raw_key in watched:
        key = str(raw_key)
        found_now, value_now = dotted(current.data, key)
        found_before, value_before = dotted(previous.data, key)
        if not found_now or not found_before:
            where = current.key if not found_now else previous.key
            changes.append({"key": key, "status": "absent", "absent_from": where})
            findings.append((on_missing, f"{key}: not present in {where}"))
            continue
        if value_now != value_before:
            changes.append(
                {
                    "key": key,
                    "status": "changed",
                    "previous": value_before,
                    "current": value_now,
                }
            )
            findings.append((on_change, f"{key}: {_render(value_before)} -> {_render(value_now)}"))
        else:
            changes.append({"key": key, "status": "unchanged", "current": value_now})

    detail = {
        "source": current.key,
        "previous_source": previous.key,
        "watched_keys": [str(key) for key in watched],
        "changes": changes,
    }
    if not findings:
        return CheckResult(
            ctx.spec.id,
            ctx.spec.type,
            Severity.PASS,
            f"All {len(watched)} watched setting(s) unchanged since last period",
            detail,
        )
    status = worst([severity for severity, _ in findings])
    return CheckResult(
        ctx.spec.id,
        ctx.spec.type,
        status,
        "; ".join(message for _, message in findings),
        detail,
        note=(
            "A reporting setting changed between the two periods shown, so the comparison is not "
            "strictly like-for-like. The change is described in the commentary."
        )
        if status is not Severity.PASS
        else None,
    )


DEFS = (
    CheckDef(
        type="config_drift",
        run=run_config_drift,
        description="Watched settings (attribution window, filters) unchanged mid-comparison.",
        source_params=("source", "previous_source"),
    ),
)
