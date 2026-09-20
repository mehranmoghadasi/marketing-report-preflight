"""Duplicate-conversion check.

Duplicated conversion rows inflate every conversion, cost-per-conversion and ROAS figure
in a report, and they usually appear after a site change adds a second tag or a thank-you
page starts firing twice.

Formula, over a ``conversions_csv`` of one row per recorded conversion:

    duplicate_rows  = total_rows - distinct(conversion_id)
    duplicate_rate  = duplicate_rows / total_rows * 100

Status: ``fail`` when ``duplicate_rate >= fail_rate_pct``, ``warn`` when
``duplicate_rate >= warn_rate_pct``, otherwise ``pass``. Worked example: 10 rows with 2
repeated ids gives ``(10 - 8) / 10 * 100 = 20%``.
"""

from __future__ import annotations

from collections import Counter

from ..model import CheckError, CheckResult, Severity
from . import CheckContext, CheckDef


def run_duplicate_conversions(ctx: CheckContext) -> CheckResult:
    source = ctx.source("source")
    if source.type != "conversions_csv":
        raise CheckError(
            f"check {ctx.spec.id!r}: source {source.key!r} is {source.type}, "
            "expected conversions_csv"
        )
    warn_rate = ctx.number("warn_rate_pct", 1.0)
    fail_rate = ctx.number("fail_rate_pct", 5.0)

    ids = Counter(row.get("conversion_id", "") for row in source.rows)
    total = sum(ids.values())
    distinct = len(ids)
    duplicate_rows = total - distinct
    rate = duplicate_rows / total * 100 if total else 0.0

    repeated = sorted(
        ((conversion_id, count) for conversion_id, count in ids.items() if count > 1),
        key=lambda pair: (-pair[1], pair[0]),
    )
    per_action: Counter[str] = Counter()
    seen: set[str] = set()
    for row in source.rows:
        conversion_id = row.get("conversion_id", "")
        if conversion_id in seen:
            per_action[row.get("action", "(unknown)")] += 1
        else:
            seen.add(conversion_id)

    detail = {
        "source": source.key,
        "file": source.path.name,
        "total_rows": total,
        "distinct_conversion_ids": distinct,
        "duplicate_rows": duplicate_rows,
        "duplicate_rate_pct": round(rate, 2),
        "warn_rate_pct": warn_rate,
        "fail_rate_pct": fail_rate,
        "most_repeated": [{"conversion_id": cid, "occurrences": n} for cid, n in repeated[:5]],
        "duplicates_by_action": dict(sorted(per_action.items())),
    }

    if rate >= fail_rate:
        status = Severity.FAIL
    elif rate >= warn_rate:
        status = Severity.WARN
    else:
        status = Severity.PASS

    if duplicate_rows:
        actions = ", ".join(f"{name} ({count})" for name, count in sorted(per_action.items()))
        summary = f"{duplicate_rows} of {total} conversion rows are repeats ({rate:.1f}%)" + (
            f" — {actions}" if actions else ""
        )
    else:
        summary = f"No repeated conversion ids across {total} rows"

    note = None
    if status is not Severity.PASS:
        note = (
            f"About {rate:.1f}% of recorded conversions this period were duplicate records. "
            "Conversion counts and cost-per-conversion have been reviewed with that in mind."
        )
    return CheckResult(ctx.spec.id, ctx.spec.type, status, summary, detail, note=note)


DEFS = (
    CheckDef(
        type="duplicate_conversions",
        run=run_duplicate_conversions,
        description="Conversion rows are not double-counted (repeated conversion ids).",
        source_params=("source",),
    ),
)
