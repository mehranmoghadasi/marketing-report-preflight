"""GA4 response-integrity check.

GA4's Data API tells you, in the response itself, when the numbers it just gave you are
incomplete — and virtually no reporting stack reads those fields. This check reads them.

Fields used, all from ``ResponseMetaData`` in the Data API v1beta reference
(https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/ResponseMetaData,
page last updated 2026-09-14):

``dataTruncationReasons[]``
    Present when the report is truncated. Each entry carries ``dataTruncationType``,
    ``dataTruncationMessage``, ``dataTruncationDateRanges[]`` (the truncated spans,
    inclusive) and ``dataTruncationDate`` (data *before* this date is truncated — the
    reference's wording, and the wording this check uses).
``emptyReason``
    Present when the report came back empty for a stated reason.
``subjectToThresholding``
    True when GA4 withheld rows that fall under its minimum aggregation thresholds.
``dataLossFromOtherRow``
    True when high-cardinality rows were rolled into ``(other)``.
``samplingMetadatas[]``
    One entry per requested date range, each with ``samplesReadCount`` and
    ``samplingSpaceSize``.

Sampling formula (Google's own): ``ratio = samplesReadCount / samplingSpaceSize``.
The check uses the **minimum** ratio across date ranges, so the worst-sampled range
decides the outcome.
"""

from __future__ import annotations

from ..model import CheckError, CheckResult, Severity, worst
from . import CheckContext, CheckDef

_SEVERITIES = {"pass": Severity.PASS, "warn": Severity.WARN, "fail": Severity.FAIL}


def severity_param(ctx: CheckContext, name: str, default: str) -> Severity:
    """Read a severity setting such as ``on_truncation: warn``."""
    raw = str(ctx.param(name, default)).lower()
    if raw not in _SEVERITIES:
        raise CheckError(
            f"check {ctx.spec.id!r}: {name} must be one of pass, warn, fail (got {raw!r})"
        )
    return _SEVERITIES[raw]


def sampling_ratio(payload: dict) -> float | None:
    """Minimum ``samplesReadCount / samplingSpaceSize`` across date ranges, or None."""
    meta = payload.get("metadata") or payload.get("responseMetaData") or {}
    entries = meta.get("samplingMetadatas") or []
    ratios = []
    for entry in entries:
        try:
            read = float(entry["samplesReadCount"])
            space = float(entry["samplingSpaceSize"])
        except (KeyError, TypeError, ValueError):
            continue
        if space > 0:
            ratios.append(read / space)
    return min(ratios) if ratios else None


def truncated_ranges(truncations: list) -> list[tuple[str, str]]:
    """Sorted, de-duplicated (startDate, endDate) pairs from ``dataTruncationDateRanges[]``."""
    found: set[tuple[str, str]] = set()
    for reason in truncations:
        if not isinstance(reason, dict):
            continue
        for span in reason.get("dataTruncationDateRanges") or []:
            if isinstance(span, dict) and span.get("startDate") and span.get("endDate"):
                found.add((str(span["startDate"]), str(span["endDate"])))
    return sorted(found)


def _metadata(payload: dict) -> dict:
    """GA4 returns this block as ``metadata`` in JSON; accept the proto name too."""
    return payload.get("metadata") or payload.get("responseMetaData") or {}


def run_response_integrity(ctx: CheckContext) -> CheckResult:
    source = ctx.source("source")
    if source.type != "ga4_report_json":
        raise CheckError(
            f"check {ctx.spec.id!r}: source {source.key!r} is {source.type}, "
            "expected ga4_report_json"
        )
    meta = _metadata(source.data)
    findings: list[tuple[Severity, str]] = []
    # Client-facing sentences are written separately from the internal summary so that no
    # API constant (DATA_TRUNCATION_TYPE_*) ever reaches the client-facing notes.
    client_notes: list[str] = []
    detail: dict = {"source": source.key, "file": source.path.name}

    truncations = meta.get("dataTruncationReasons") or []
    if truncations:
        kinds = sorted(
            {
                str(t.get("dataTruncationType", "UNSPECIFIED"))
                for t in truncations
                if isinstance(t, dict)
            }
        )
        dates = sorted(
            {
                str(t.get("dataTruncationDate"))
                for t in truncations
                if isinstance(t, dict) and t.get("dataTruncationDate")
            }
        )
        detail["data_truncation_types"] = kinds
        detail["data_truncation_dates"] = dates
        detail["data_truncation_messages"] = [
            str(t.get("dataTruncationMessage", "")) for t in truncations if isinstance(t, dict)
        ]
        ranges = truncated_ranges(truncations)
        detail["data_truncation_ranges"] = [{"start": start, "end": end} for start, end in ranges]
        # Wording follows the reference exactly: ``dataTruncationDateRanges`` are the
        # truncated spans (inclusive); ``dataTruncationDate`` means data BEFORE that date
        # is truncated. Never say "from <date> onward" for the latter.
        if ranges:
            spans = ", ".join(
                start if start == end else f"{start} to {end}" for start, end in ranges
            )
            where = f" for {spans}"
            client_where = f"available for {spans}"
        elif dates:
            where = f" before {dates[-1]}"
            client_where = f"available for dates before {dates[-1]}"
        else:
            where = ""
            client_where = "available"
        findings.append(
            (
                severity_param(ctx, "on_truncation", "fail"),
                f"GA4 reports truncated data{where} ({', '.join(kinds)})",
            )
        )
        client_notes.append(
            "Google Analytics reported that some of this period's data was not fully "
            + client_where
            + ", so the totals shown understate actual activity."
        )

    empty_reason = meta.get("emptyReason")
    if empty_reason:
        detail["empty_reason"] = str(empty_reason)
        findings.append(
            (
                severity_param(ctx, "on_empty", "fail"),
                f"GA4 returned an empty report: {empty_reason}",
            )
        )
        client_notes.append(
            "Google Analytics returned no data for this period, so the figures below could not "
            "be verified."
        )

    if meta.get("subjectToThresholding"):
        detail["subject_to_thresholding"] = True
        findings.append(
            (
                severity_param(ctx, "on_thresholding", "warn"),
                "GA4 withheld rows under its minimum aggregation thresholds",
            )
        )
        client_notes.append(
            "Google Analytics withheld some low-volume detail for privacy reasons, so the "
            "breakdowns in this report do not add up exactly to the totals."
        )

    if meta.get("dataLossFromOtherRow"):
        detail["data_loss_from_other_row"] = True
        findings.append(
            (
                severity_param(ctx, "on_other_row", "warn"),
                "High-cardinality rows were rolled into the (other) row",
            )
        )
        client_notes.append(
            'Google Analytics grouped some lower-volume rows together under "(other)", so the '
            "longer tail of this report's breakdowns is approximate."
        )

    ratio = sampling_ratio(source.data)
    minimum = ctx.number("min_sampling_ratio", 0.95)
    if ratio is not None:
        detail["sampling_ratio"] = round(ratio, 6)
        detail["min_sampling_ratio"] = minimum
        if ratio < minimum:
            findings.append(
                (
                    severity_param(ctx, "on_sampled", "warn"),
                    f"Report is sampled: {ratio:.1%} of events analysed (threshold {minimum:.0%})",
                )
            )
            client_notes.append(
                "Google Analytics calculated this period from a sample covering "
                f"{ratio:.0%} of events rather than all of them, so the figures are close "
                "estimates rather than exact counts."
            )

    if not payload_has_rows(source.data):
        detail["row_count"] = 0
        findings.append(
            (severity_param(ctx, "on_no_rows", "fail"), "GA4 response contains no rows")
        )
        client_notes.append(
            "The analytics export behind this report contained no rows, so its figures could not "
            "be verified."
        )
    else:
        detail["row_count"] = len(source.data.get("rows", []))

    if not findings:
        return CheckResult(
            ctx.spec.id,
            ctx.spec.type,
            Severity.PASS,
            "GA4 response is complete: not truncated, not sampled, no withheld rows",
            detail,
        )

    status = worst([severity for severity, _ in findings])
    summary = "; ".join(message for _, message in findings)
    note = " ".join(client_notes) if status is not Severity.PASS and client_notes else None
    return CheckResult(ctx.spec.id, ctx.spec.type, status, summary, detail, note=note)


def payload_has_rows(payload: dict) -> bool:
    rows = payload.get("rows")
    if rows:
        return True
    # A totals-only request is legitimate: treat present totals as rows.
    return bool(payload.get("totals"))


DEFS = (
    CheckDef(
        type="ga4_response_integrity",
        run=run_response_integrity,
        description=(
            "Reads GA4's own ResponseMetaData (truncation, sampling, thresholding, "
            "(other) row, empty reason) and fails the report when the API said the data "
            "was incomplete."
        ),
        source_params=("source",),
    ),
)
