"""Period arithmetic and source loading, including the failure messages."""

from __future__ import annotations

import json
from datetime import date

import pytest

from preflight.model import CheckError
from preflight.periods import parse_date, period_bounds, period_days, previous_period
from preflight.sources import SourceSpec, load_source, metric_names, metric_total
from tests.conftest import make_source


def test_previous_period_crosses_the_year_boundary():
    assert previous_period("2026-01") == "2025-12"
    assert previous_period("2026-08") == "2026-07"


def test_period_days_counts_a_leap_february():
    assert len(period_days("2024-02")) == 29
    assert len(period_days("2026-02")) == 28
    assert period_bounds("2026-08") == (date(2026, 8, 1), date(2026, 8, 31))


def test_malformed_period_is_rejected():
    with pytest.raises(ValueError):
        period_days("2026-13")


def test_parse_date_accepts_ga4_compact_form():
    assert parse_date("20260815") == date(2026, 8, 15)
    assert parse_date("2026-08-15") == date(2026, 8, 15)


def test_metrics_csv_missing_column_names_the_column(tmp_path):
    path = tmp_path / "metrics.csv"
    path.write_text("date,metric\n2026-08-01,sessions\n", encoding="utf-8")
    spec = SourceSpec("metrics", "metrics_csv", "metrics.csv")
    with pytest.raises(CheckError) as excinfo:
        load_source(spec, tmp_path, {})
    assert "value" in str(excinfo.value)


def test_source_path_template_uses_period_placeholders(tmp_path):
    folder = tmp_path / "data" / "2026-07"
    folder.mkdir(parents=True)
    (folder / "settings.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    spec = SourceSpec("prev_settings", "json", "data/{prev_period}/settings.json")
    loaded = load_source(spec, tmp_path, {"period": "2026-08", "prev_period": "2026-07"})
    assert loaded.data == {"a": 1}


def test_unknown_source_type_lists_known_types(tmp_path):
    (tmp_path / "x.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CheckError) as excinfo:
        load_source(SourceSpec("x", "sqlite_dump", "x.json"), tmp_path, {})
    assert "ga4_report_json" in str(excinfo.value)


def test_metric_total_sums_only_the_named_metric():
    source = make_source(
        "metrics",
        "metrics_csv",
        rows=[
            {"date": "2026-08-01", "metric": "sessions", "value": "10"},
            {"date": "2026-08-01", "metric": "revenue", "value": "5.5"},
            {"date": "2026-08-02", "metric": "sessions", "value": "20"},
        ],
    )
    assert metric_total(source, "sessions") == 30
    assert metric_total(source, "revenue") == 5.5
    assert metric_names(source) == ["revenue", "sessions"]
    with pytest.raises(CheckError):
        metric_total(source, "phone_calls")


def test_dates_are_read_from_a_ga4_response(ga4_clean):
    source = make_source("ga4", "ga4_report_json", data=ga4_clean)
    assert source.dates() == [date(2026, 8, 1)]
