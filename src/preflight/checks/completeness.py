"""Completeness checks: does the data actually cover the period you are reporting on?

``period_completeness``
    ``missing_days = (every calendar day in the period) - (days present in the source)``
    Status: ``fail`` when ``len(missing_days) > fail_above``, ``warn`` when
    ``len(missing_days) > warn_above``, otherwise ``pass``. Any day in the source that
    falls outside the period is reported separately and is always at least a warning,
    because it means the export used the wrong date range.

``source_freshness``
    ``lag_days = period_end - max(date in source)``
    Status: ``fail`` when ``lag_days > fail_after_days``, ``warn`` when
    ``lag_days > warn_after_days``, otherwise ``pass``. A negative lag (dates after the
    period end) is reported as a range error.
"""

from __future__ import annotations

from ..model import CheckResult, Severity, worst
from ..periods import period_bounds, period_days
from . import CheckContext, CheckDef


def _fmt_days(days: list) -> list[str]:
    return [day.isoformat() for day in days]


def run_period_completeness(ctx: CheckContext) -> CheckResult:
    source = ctx.source("source")
    start, end = period_bounds(ctx.period)
    expected = period_days(ctx.period)
    present = set(source.dates())
    missing = [day for day in expected if day not in present]
    outside = sorted(day for day in present if day < start or day > end)

    warn_above = int(ctx.number("warn_above", 0))
    fail_above = int(ctx.number("fail_above", 2))

    detail = {
        "source": source.key,
        "file": source.path.name,
        "expected_days": len(expected),
        "days_present": len(expected) - len(missing),
        "missing_days": _fmt_days(missing),
        "dates_outside_period": _fmt_days(outside),
        "warn_above": warn_above,
        "fail_above": fail_above,
    }

    findings: list[tuple[Severity, str]] = []
    if missing:
        status = (
            Severity.FAIL
            if len(missing) > fail_above
            else Severity.WARN
            if len(missing) > warn_above
            else Severity.PASS
        )
        shown = ", ".join(_fmt_days(missing[:5])) + ("…" if len(missing) > 5 else "")
        findings.append((status, f"{len(missing)} of {len(expected)} days have no data ({shown})"))
    if outside:
        findings.append(
            (
                Severity.WARN if len(outside) <= 2 else Severity.FAIL,
                f"{len(outside)} row date(s) fall outside {ctx.period} "
                f"({', '.join(_fmt_days(outside[:3]))})",
            )
        )

    if not findings:
        return CheckResult(
            ctx.spec.id,
            ctx.spec.type,
            Severity.PASS,
            f"All {len(expected)} days of {ctx.period} are present",
            detail,
        )
    status = worst([severity for severity, _ in findings])
    summary = "; ".join(message for _, message in findings)
    note = None
    if status is not Severity.PASS and missing:
        day_word = "day" if len(missing) == 1 else "days"
        verb = "is" if len(missing) == 1 else "are"
        note = (
            f"This report covers {len(expected) - len(missing)} of the {len(expected)} days in "
            f"{ctx.period}; {len(missing)} {day_word} of data {verb} not available, so period "
            "totals are lower than the true figure."
        )
    elif status is not Severity.PASS:
        note = (
            f"Some of the underlying data falls outside {ctx.period}; comparisons in this report "
            "may not be like-for-like."
        )
    return CheckResult(ctx.spec.id, ctx.spec.type, status, summary, detail, note=note)


def run_source_freshness(ctx: CheckContext) -> CheckResult:
    sources = ctx.source_list("sources")
    _, end = period_bounds(ctx.period)
    warn_after = int(ctx.number("warn_after_days", 1))
    fail_after = int(ctx.number("fail_after_days", 3))

    per_source: dict[str, dict] = {}
    findings: list[tuple[Severity, str]] = []
    for source in sources:
        dates = source.dates()
        if not dates:
            per_source[source.key] = {"latest_date": None, "lag_days": None}
            findings.append(
                (Severity.WARN, f"{source.key}: no dated rows, freshness cannot be checked")
            )
            continue
        latest = dates[-1]
        lag = (end - latest).days
        per_source[source.key] = {"latest_date": latest.isoformat(), "lag_days": lag}
        if lag < 0:
            findings.append(
                (Severity.FAIL, f"{source.key}: contains data dated after {end.isoformat()}")
            )
        elif lag > fail_after:
            findings.append(
                (Severity.FAIL, f"{source.key}: last data point is {lag} days before period end")
            )
        elif lag > warn_after:
            findings.append(
                (Severity.WARN, f"{source.key}: last data point is {lag} days before period end")
            )

    detail = {
        "period_end": end.isoformat(),
        "warn_after_days": warn_after,
        "fail_after_days": fail_after,
        "sources": per_source,
    }
    if not findings:
        return CheckResult(
            ctx.spec.id,
            ctx.spec.type,
            Severity.PASS,
            f"All {len(sources)} source(s) have data through {end.isoformat()}",
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
            "One or more data sources stopped updating before the end of the period, so the "
            "figures shown are partial."
        )
        if status is not Severity.PASS
        else None,
    )


DEFS = (
    CheckDef(
        type="period_completeness",
        run=run_period_completeness,
        description="Every calendar day of the reporting period is present, and no stray dates.",
        source_params=("source",),
    ),
    CheckDef(
        type="source_freshness",
        run=run_source_freshness,
        description="Each source has data through the period end (catches stalled connectors).",
        source_list_params=("sources",),
    ),
)
