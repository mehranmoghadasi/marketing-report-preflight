"""Period-over-period checks. These answer the question a client always asks first:
"is this drop real?" — before the report is sent, not during the call.

``metric_delta``
    ``pct_change = (current_total - previous_total) / previous_total * 100``
    Status: ``fail`` when ``abs(pct_change) >= fail_pct``, ``warn`` when
    ``abs(pct_change) >= warn_pct``, otherwise ``pass``. When ``previous_total == 0`` no
    percentage is defined: a non-zero current total is reported as ``warn`` ("no
    baseline"), and zero in both periods is handed to ``zero_stream``.

``zero_stream``
    ``fail`` when a metric totals exactly 0 this period but was above 0 last period —
    the classic signature of tracking that stopped firing. ``warn`` when it is 0 in both
    periods (nothing is being measured at all).

``consent_shift``
    ``denial_rate = denied / (granted + denied)``
    ``shift_points = (denial_rate - previous_denial_rate) * 100``
    Status: ``fail`` when ``abs(shift_points) >= fail_points``, ``warn`` when
    ``abs(shift_points) >= warn_points``. A consent-banner or Consent Mode change moves
    this number and silently reshapes every metric in the report.
"""

from __future__ import annotations

from ..model import CheckError, CheckResult, Severity, worst
from ..sources import LoadedSource, metric_total
from . import CheckContext, CheckDef


def _metric_settings(ctx: CheckContext) -> dict[str, dict]:
    raw = ctx.required_param("metrics")
    if isinstance(raw, list):
        return {str(name): {} for name in raw}
    if isinstance(raw, dict):
        return {str(name): (settings or {}) for name, settings in raw.items()}
    raise CheckError(f"check {ctx.spec.id!r}: metrics must be a list or a mapping")


def _threshold(settings: dict, key: str, fallback: float, ctx: CheckContext) -> float:
    if key in settings:
        try:
            return float(settings[key])
        except (TypeError, ValueError) as exc:
            raise CheckError(f"check {ctx.spec.id!r}: {key} must be a number") from exc
    return fallback


def run_metric_delta(ctx: CheckContext) -> CheckResult:
    current = ctx.source("source")
    previous = ctx.source("previous_source")
    default_warn = ctx.number("warn_pct", 20.0)
    default_fail = ctx.number("fail_pct", 40.0)

    per_metric: dict[str, dict] = {}
    findings: list[tuple[Severity, str]] = []
    for metric, settings in _metric_settings(ctx).items():
        warn_pct = _threshold(settings, "warn_pct", default_warn, ctx)
        fail_pct = _threshold(settings, "fail_pct", default_fail, ctx)
        now = metric_total(current, metric)
        before = metric_total(previous, metric)
        entry: dict = {
            "current": now,
            "previous": before,
            "warn_pct": warn_pct,
            "fail_pct": fail_pct,
        }
        if before == 0:
            entry["pct_change"] = None
            if now != 0:
                findings.append(
                    (
                        Severity.WARN,
                        f"{metric}: {now:,.0f} this period with no baseline last period",
                    )
                )
        else:
            pct = (now - before) / before * 100
            entry["pct_change"] = round(pct, 2)
            magnitude = abs(pct)
            if magnitude >= fail_pct:
                findings.append(
                    (
                        Severity.FAIL,
                        f"{metric}: {pct:+.1f}% vs last period "
                        f"({before:,.0f} to {now:,.0f}), over the {fail_pct:g}% limit",
                    )
                )
            elif magnitude >= warn_pct:
                findings.append(
                    (
                        Severity.WARN,
                        f"{metric}: {pct:+.1f}% vs last period ({before:,.0f} to {now:,.0f})",
                    )
                )
        per_metric[metric] = entry

    detail = {
        "source": current.key,
        "previous_source": previous.key,
        "metrics": per_metric,
    }
    if not findings:
        return CheckResult(
            ctx.spec.id,
            ctx.spec.type,
            Severity.PASS,
            f"All {len(per_metric)} tracked metric(s) moved within their thresholds",
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
            "One or more headline numbers moved sharply against the previous period. "
            "The movement was reviewed before this report was sent; the commentary explains "
            "whether it reflects performance or a change in how the data is collected."
        )
        if status is not Severity.PASS
        else None,
    )


def run_zero_stream(ctx: CheckContext) -> CheckResult:
    current = ctx.source("source")
    previous = ctx.source("previous_source")
    metrics = _metric_settings(ctx)

    per_metric: dict[str, dict] = {}
    findings: list[tuple[Severity, str]] = []
    for metric in metrics:
        now = metric_total(current, metric)
        before = metric_total(previous, metric)
        per_metric[metric] = {"current": now, "previous": before}
        if now == 0 and before > 0:
            findings.append(
                (
                    Severity.FAIL,
                    f"{metric}: zero this period after {before:,.0f} last period — "
                    "tracking has most likely stopped",
                )
            )
        elif now == 0 and before == 0:
            findings.append(
                (Severity.WARN, f"{metric}: zero in both periods — nothing is recorded")
            )

    detail = {"source": current.key, "previous_source": previous.key, "metrics": per_metric}
    if not findings:
        return CheckResult(
            ctx.spec.id,
            ctx.spec.type,
            Severity.PASS,
            f"All {len(per_metric)} metric(s) recorded activity this period",
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
            "At least one metric recorded nothing at all this period. This is being treated as a "
            "measurement problem rather than a performance result until it is confirmed."
        )
        if status is not Severity.PASS
        else None,
    )


def _denial_rate(source: LoadedSource) -> tuple[float, float, float]:
    granted = sum(source.numbers("granted"))
    denied = sum(source.numbers("denied"))
    total = granted + denied
    if total <= 0:
        raise CheckError(
            f"{source.path.name}: granted + denied is zero, no consent rate to compute"
        )
    return denied / total, granted, denied


def run_consent_shift(ctx: CheckContext) -> CheckResult:
    current = ctx.source("source")
    previous = ctx.source("previous_source")
    warn_points = ctx.number("warn_points", 5.0)
    fail_points = ctx.number("fail_points", 15.0)

    rate, granted, denied = _denial_rate(current)
    prev_rate, prev_granted, prev_denied = _denial_rate(previous)
    shift = (rate - prev_rate) * 100

    detail = {
        "source": current.key,
        "previous_source": previous.key,
        "granted": granted,
        "denied": denied,
        "denial_rate": round(rate, 6),
        "previous_granted": prev_granted,
        "previous_denied": prev_denied,
        "previous_denial_rate": round(prev_rate, 6),
        "shift_points": round(shift, 2),
        "warn_points": warn_points,
        "fail_points": fail_points,
    }
    magnitude = abs(shift)
    if magnitude >= fail_points:
        status = Severity.FAIL
    elif magnitude >= warn_points:
        status = Severity.WARN
    else:
        status = Severity.PASS

    summary = f"Consent denial rate {rate:.1%} vs {prev_rate:.1%} last period ({shift:+.1f} points)"
    note = None
    if status is not Severity.PASS:
        direction = "more" if shift > 0 else "fewer"
        note = (
            f"The share of visitors declining analytics consent changed by {abs(shift):.1f} "
            f"percentage points this period ({direction} visitors declining), which changes how "
            "much activity is measurable. Period-over-period comparisons are affected."
        )
    return CheckResult(ctx.spec.id, ctx.spec.type, status, summary, detail, note=note)


DEFS = (
    CheckDef(
        type="metric_delta",
        run=run_metric_delta,
        description="Headline metrics moved within an agreed period-over-period tolerance.",
        source_params=("source", "previous_source"),
    ),
    CheckDef(
        type="zero_stream",
        run=run_zero_stream,
        description="No metric dropped to exactly zero after recording activity last period.",
        source_params=("source", "previous_source"),
    ),
    CheckDef(
        type="consent_shift",
        run=run_consent_shift,
        description="The consent denial rate did not move enough to reshape the numbers.",
        source_params=("source", "previous_source"),
    ),
)
