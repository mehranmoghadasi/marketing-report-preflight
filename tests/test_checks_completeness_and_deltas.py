"""Completeness, freshness and period-over-period checks, with hand-computed numbers."""

from __future__ import annotations

import pytest

from preflight.model import CheckError, Severity
from tests.conftest import make_source, metrics_rows, run_check


def metrics_source(days: int, metric: str = "sessions", value: float = 10.0, month="2026-08"):
    return make_source(
        "metrics",
        "metrics_csv",
        rows=metrics_rows({metric: [value] * days}, month=month),
        filename="metrics.csv",
    )


# --- period_completeness ----------------------------------------------------------


def test_full_month_passes():
    result = run_check(
        "period_completeness", {"source": "metrics"}, {"metrics": metrics_source(31)}
    )
    assert result.status is Severity.PASS
    assert result.detail["missing_days"] == []


def test_three_missing_days_fails_and_lists_them():
    result = run_check(
        "period_completeness",
        {"source": "metrics", "fail_above": 2},
        {"metrics": metrics_source(28)},
    )
    assert result.status is Severity.FAIL
    assert result.detail["missing_days"] == ["2026-08-29", "2026-08-30", "2026-08-31"]
    assert "28 of the 31 days" in result.note


def test_one_missing_day_only_warns():
    result = run_check(
        "period_completeness",
        {"source": "metrics", "warn_above": 0, "fail_above": 2},
        {"metrics": metrics_source(30)},
    )
    assert result.status is Severity.WARN


def test_dates_outside_the_period_are_flagged():
    rows = metrics_rows({"sessions": [10] * 31})
    rows.append({"date": "2026-09-02", "metric": "sessions", "value": "9"})
    source = make_source("metrics", "metrics_csv", rows=rows, filename="metrics.csv")
    result = run_check("period_completeness", {"source": "metrics"}, {"metrics": source})
    assert result.detail["dates_outside_period"] == ["2026-09-02"]
    assert result.status is Severity.WARN


# --- source_freshness -------------------------------------------------------------


def test_freshness_lag_is_period_end_minus_latest_date():
    result = run_check(
        "source_freshness",
        {"sources": ["metrics"], "warn_after_days": 1, "fail_after_days": 5},
        {"metrics": metrics_source(28)},
    )
    assert result.detail["sources"]["metrics"]["lag_days"] == 3
    assert result.status is Severity.WARN


def test_freshness_fails_past_the_fail_threshold():
    result = run_check(
        "source_freshness",
        {"sources": ["metrics"], "warn_after_days": 1, "fail_after_days": 2},
        {"metrics": metrics_source(28)},
    )
    assert result.status is Severity.FAIL


def test_freshness_passes_when_data_reaches_period_end():
    result = run_check(
        "source_freshness", {"sources": ["metrics"]}, {"metrics": metrics_source(31)}
    )
    assert result.status is Severity.PASS
    assert result.detail["sources"]["metrics"]["lag_days"] == 0


# --- metric_delta -----------------------------------------------------------------


def _delta(current: float, previous: float, **params):
    sources = {
        "now": make_source("now", "metrics_csv", rows=metrics_rows({"sessions": [current]})),
        "before": make_source(
            "before", "metrics_csv", rows=metrics_rows({"sessions": [previous]}, month="2026-07")
        ),
    }
    return run_check(
        "metric_delta",
        {"source": "now", "previous_source": "before", "metrics": ["sessions"], **params},
        sources,
    )


def test_metric_delta_percentage_is_hand_checkable():
    # (620 - 1000) / 1000 * 100 = -38.0
    result = _delta(620, 1000, warn_pct=20, fail_pct=35)
    assert result.detail["metrics"]["sessions"]["pct_change"] == -38.0
    assert result.status is Severity.FAIL


def test_metric_delta_warns_between_the_thresholds():
    # (750 - 1000) / 1000 * 100 = -25.0
    result = _delta(750, 1000, warn_pct=20, fail_pct=35)
    assert result.detail["metrics"]["sessions"]["pct_change"] == -25.0
    assert result.status is Severity.WARN


def test_metric_delta_passes_inside_tolerance():
    result = _delta(950, 1000, warn_pct=20, fail_pct=35)
    assert result.status is Severity.PASS


def test_metric_delta_handles_a_zero_baseline_without_dividing():
    result = _delta(400, 0)
    assert result.status is Severity.WARN
    assert result.detail["metrics"]["sessions"]["pct_change"] is None


def test_metric_delta_thresholds_can_be_set_per_metric():
    sources = {
        "now": make_source(
            "now", "metrics_csv", rows=metrics_rows({"sessions": [900], "revenue": [500]})
        ),
        "before": make_source(
            "before",
            "metrics_csv",
            rows=metrics_rows({"sessions": [1000], "revenue": [1000]}, month="2026-07"),
        ),
    }
    result = run_check(
        "metric_delta",
        {
            "source": "now",
            "previous_source": "before",
            "metrics": {
                "sessions": {"warn_pct": 25, "fail_pct": 50},
                "revenue": {"warn_pct": 10, "fail_pct": 40},
            },
        },
        sources,
    )
    assert result.status is Severity.FAIL  # revenue -50% breaches its own fail threshold
    assert result.detail["metrics"]["sessions"]["pct_change"] == -10.0


# --- zero_stream ------------------------------------------------------------------


def test_zero_after_activity_fails():
    sources = {
        "now": make_source("now", "metrics_csv", rows=metrics_rows({"phone_calls": [0] * 31})),
        "before": make_source(
            "before", "metrics_csv", rows=metrics_rows({"phone_calls": [10] * 31}, month="2026-07")
        ),
    }
    result = run_check(
        "zero_stream",
        {"source": "now", "previous_source": "before", "metrics": ["phone_calls"]},
        sources,
    )
    assert result.status is Severity.FAIL
    assert result.detail["metrics"]["phone_calls"] == {"current": 0.0, "previous": 310.0}


def test_zero_in_both_periods_only_warns():
    sources = {
        "now": make_source("now", "metrics_csv", rows=metrics_rows({"phone_calls": [0]})),
        "before": make_source(
            "before", "metrics_csv", rows=metrics_rows({"phone_calls": [0]}, month="2026-07")
        ),
    }
    result = run_check(
        "zero_stream",
        {"source": "now", "previous_source": "before", "metrics": ["phone_calls"]},
        sources,
    )
    assert result.status is Severity.WARN


# --- consent_shift ----------------------------------------------------------------


def _consent(now: tuple[int, int], before: tuple[int, int], **params):
    sources = {
        "now": make_source(
            "now",
            "consent_csv",
            rows=[{"date": "2026-08-01", "granted": str(now[0]), "denied": str(now[1])}],
        ),
        "before": make_source(
            "before",
            "consent_csv",
            rows=[{"date": "2026-07-01", "granted": str(before[0]), "denied": str(before[1])}],
        ),
    }
    return run_check(
        "consent_shift", {"source": "now", "previous_source": "before", **params}, sources
    )


def test_consent_denial_rate_and_shift_are_hand_checkable():
    # 300/1000 = 30% now, 120/1000 = 12% before -> +18.0 points
    result = _consent((700, 300), (880, 120), warn_points=5, fail_points=15)
    assert result.detail["denial_rate"] == 0.3
    assert result.detail["previous_denial_rate"] == 0.12
    assert result.detail["shift_points"] == 18.0
    assert result.status is Severity.FAIL


def test_small_consent_movement_passes():
    result = _consent((790, 210), (800, 200), warn_points=5, fail_points=15)
    assert result.status is Severity.PASS


def test_consent_with_no_sessions_is_an_error():
    with pytest.raises(CheckError):
        _consent((0, 0), (800, 200))
