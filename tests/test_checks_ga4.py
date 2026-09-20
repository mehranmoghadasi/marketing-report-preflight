"""GA4 response-integrity check, one GA4 metadata condition at a time."""

from __future__ import annotations

from preflight.checks.ga4_response import sampling_ratio
from preflight.model import Severity
from tests.conftest import make_source, run_check


def _run(payload: dict, params: dict | None = None):
    sources = {"ga4": make_source("ga4", "ga4_report_json", data=payload)}
    return run_check("ga4_response_integrity", {"source": "ga4", **(params or {})}, sources)


def test_clean_response_passes(ga4_clean):
    result = _run(ga4_clean)
    assert result.status is Severity.PASS
    assert result.note is None


def test_truncation_fails_and_reports_type_and_date(ga4_clean):
    ga4_clean["metadata"]["dataTruncationReasons"] = [
        {
            "dataTruncationType": "DATA_TRUNCATION_TYPE_DATE_RANGE",
            "dataTruncationMessage": "Query date range may not be fully served.",
            "dataTruncationDate": "2026-08-29",
        }
    ]
    result = _run(ga4_clean)
    assert result.status is Severity.FAIL
    assert result.detail["data_truncation_types"] == ["DATA_TRUNCATION_TYPE_DATE_RANGE"]
    assert result.detail["data_truncation_dates"] == ["2026-08-29"]
    # dataTruncationDate means data BEFORE that date is truncated (Data API reference)
    assert "before 2026-08-29" in result.summary
    assert "dates before 2026-08-29" in result.note
    assert "onward" not in result.note


def test_truncation_ranges_are_quoted_as_spans(ga4_clean):
    ga4_clean["metadata"]["dataTruncationReasons"] = [
        {
            "dataTruncationType": "DATA_TRUNCATION_TYPE_DATE_RANGE",
            "dataTruncationDateRanges": [{"startDate": "2026-08-29", "endDate": "2026-08-31"}],
        }
    ]
    result = _run(ga4_clean)
    assert result.detail["data_truncation_ranges"] == [{"start": "2026-08-29", "end": "2026-08-31"}]
    assert "for 2026-08-29 to 2026-08-31" in result.summary
    assert "available for 2026-08-29 to 2026-08-31" in result.note


def test_client_note_never_leaks_the_api_enum(ga4_clean):
    ga4_clean["metadata"]["dataTruncationReasons"] = [
        {"dataTruncationType": "DATA_TRUNCATION_TYPE_PROPERTY", "dataTruncationDate": "2026-08-10"}
    ]
    result = _run(ga4_clean)
    assert "DATA_TRUNCATION" not in result.note.upper()


def test_empty_reason_fails(ga4_clean):
    ga4_clean["metadata"]["emptyReason"] = "NO_DATA_IN_DATE_RANGE"
    result = _run(ga4_clean)
    assert result.status is Severity.FAIL
    assert result.detail["empty_reason"] == "NO_DATA_IN_DATE_RANGE"


def test_thresholding_warns(ga4_clean):
    ga4_clean["metadata"]["subjectToThresholding"] = True
    result = _run(ga4_clean)
    assert result.status is Severity.WARN


def test_other_row_warns(ga4_clean):
    ga4_clean["metadata"]["dataLossFromOtherRow"] = True
    result = _run(ga4_clean)
    assert result.status is Severity.WARN
    assert result.detail["data_loss_from_other_row"] is True


def test_sampling_ratio_is_read_count_over_space_size():
    payload = {
        "metadata": {
            "samplingMetadatas": [
                {"samplesReadCount": "250000", "samplingSpaceSize": "1000000"},
                {"samplesReadCount": "900000", "samplingSpaceSize": "1000000"},
            ]
        }
    }
    # The worst of 0.25 and 0.90 decides.
    assert sampling_ratio(payload) == 0.25


def test_sampled_report_warns_with_the_ratio(ga4_clean):
    ga4_clean["metadata"]["samplingMetadatas"] = [
        {"samplesReadCount": "6888000", "samplingSpaceSize": "8400000"}
    ]
    result = _run(ga4_clean)
    assert result.status is Severity.WARN
    assert result.detail["sampling_ratio"] == 0.82
    assert "82" in result.summary


def test_sampling_above_the_threshold_passes(ga4_clean):
    ga4_clean["metadata"]["samplingMetadatas"] = [
        {"samplesReadCount": "990000", "samplingSpaceSize": "1000000"}
    ]
    assert _run(ga4_clean).status is Severity.PASS


def test_severity_of_each_condition_is_configurable(ga4_clean):
    ga4_clean["metadata"]["dataTruncationReasons"] = [
        {"dataTruncationType": "DATA_TRUNCATION_TYPE_CONVERSIONS"}
    ]
    assert _run(ga4_clean, {"on_truncation": "warn"}).status is Severity.WARN


def test_response_without_rows_fails(ga4_clean):
    ga4_clean["rows"] = []
    result = _run(ga4_clean)
    assert result.status is Severity.FAIL
    assert result.detail["row_count"] == 0
