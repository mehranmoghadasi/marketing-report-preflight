"""Shared fixtures: build sources in memory and run a single check without touching disk."""

from __future__ import annotations

from pathlib import Path

import pytest

from preflight.checks import CheckContext, CheckSpec, get_check
from preflight.model import CheckResult
from preflight.sources import LoadedSource

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
DEMO_CLIENTS = EXAMPLES / "clients"


def make_source(
    key: str, source_type: str, data=None, rows=None, filename="fixture"
) -> LoadedSource:
    """A LoadedSource built from literals, as if a file had been read."""
    rows = rows or []
    return LoadedSource(key, source_type, Path(filename), data if data is not None else rows, rows)


def metrics_rows(values: dict[str, list[float]], start_day: int = 1, month: str = "2026-08"):
    """Long-format metrics rows: ``{"sessions": [10, 12]}`` -> two days of rows."""
    rows = []
    length = max(len(series) for series in values.values())
    for index in range(length):
        day = f"{month}-{start_day + index:02d}"
        for metric, series in values.items():
            if index < len(series):
                rows.append({"date": day, "metric": metric, "value": str(series[index])})
    return rows


def run_check(
    check_type: str,
    params: dict,
    sources: dict[str, LoadedSource],
    period: str = "2026-08",
    check_id: str = "check-1",
) -> CheckResult:
    spec = CheckSpec(check_id, check_type, "", params)
    context = CheckContext(spec, period, lambda key: sources[key])
    return get_check(check_type).run(context)


@pytest.fixture
def ga4_clean() -> dict:
    return {
        "dimensionHeaders": [{"name": "date"}],
        "metricHeaders": [{"name": "sessions"}],
        "rows": [{"dimensionValues": [{"value": "20260801"}], "metricValues": [{"value": "100"}]}],
        "metadata": {"currencyCode": "CAD", "subjectToThresholding": False},
    }


@pytest.fixture
def demo_clients_dir() -> Path:
    return DEMO_CLIENTS
