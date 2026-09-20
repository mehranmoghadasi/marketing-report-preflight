#!/usr/bin/env python3
"""Regenerate the synthetic example data for the northgate-plumbing demo client.

The data is invented, deterministic, and shaped to exercise every check: July 2026 is a
clean month, August 2026 is a month where the connector stopped on the 28th, call tracking
went silent, the consent banner changed, and a few conversions were double-recorded.

Run from the repository root:

    python examples/make_example_data.py
"""

from __future__ import annotations

import csv
import json
import random
from calendar import monthrange
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "clients" / "northgate-plumbing" / "data"

JULY = "2026-07"
AUGUST = "2026-08"

# Totals chosen so the demo produces a readable mix of pass / warn / fail.
TOTALS = {
    JULY: {"sessions": 9300, "conversions": 370, "revenue": 46000.0, "phone_calls": 412},
    AUGUST: {"sessions": 8400, "conversions": 229, "revenue": 35880.0, "phone_calls": 0},
}
DAYS_WITH_DATA = {JULY: 31, AUGUST: 28}
CONSENT = {JULY: (7254, 2046), AUGUST: (5544, 2856)}
ACTIONS = ["form_submit", "book_appointment", "quote_request"]


def days(period: str) -> list[date]:
    year, month = (int(part) for part in period.split("-"))
    return [date(year, month, day) for day in range(1, DAYS_WITH_DATA[period] + 1)]


def calendar_days(period: str) -> int:
    year, month = (int(part) for part in period.split("-"))
    return monthrange(year, month)[1]


def spread(total: float, count: int, seed: int, decimals: int = 0) -> list[float]:
    """Split ``total`` over ``count`` days with a stable weekday-ish wobble."""
    rng = random.Random(seed)
    weights = [0.8 + rng.random() * 0.4 for _ in range(count)]
    scale = total / sum(weights)
    values = [round(weight * scale, decimals) for weight in weights]
    drift = round(total - sum(values), decimals)
    values[-1] = round(values[-1] + drift, decimals)
    return values


def write_metrics(period: str) -> None:
    dates = days(period)
    totals = TOTALS[period]
    series = {
        "sessions": [int(v) for v in spread(totals["sessions"], len(dates), 11)],
        "conversions": [int(v) for v in spread(totals["conversions"], len(dates), 22)],
        "revenue": spread(totals["revenue"], len(dates), 33, decimals=2),
        "phone_calls": (
            [int(v) for v in spread(totals["phone_calls"], len(dates), 44)]
            if totals["phone_calls"]
            else [0] * len(dates)
        ),
    }
    target = ROOT / period / "metrics.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "metric", "value"])
        for index, day in enumerate(dates):
            for metric in ("sessions", "conversions", "revenue", "phone_calls"):
                writer.writerow([day.isoformat(), metric, series[metric][index]])


def write_consent(period: str) -> None:
    dates = days(period)
    granted_total, denied_total = CONSENT[period]
    granted = [int(v) for v in spread(granted_total, len(dates), 55)]
    denied = [int(v) for v in spread(denied_total, len(dates), 66)]
    target = ROOT / period / "consent.csv"
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "granted", "denied"])
        for index, day in enumerate(dates):
            writer.writerow([day.isoformat(), granted[index], denied[index]])


def write_conversions(period: str, duplicates: int) -> None:
    """One row per recorded conversion, with ``duplicates`` rows repeating an earlier id."""
    dates = days(period)
    total = int(TOTALS[period]["conversions"])
    unique = total - duplicates
    rng = random.Random(77 + int(period.replace("-", "")))
    rows = []
    for index in range(unique):
        day = dates[index % len(dates)]
        rows.append(
            {
                "conversion_id": f"{period.replace('-', '')}-{index + 1:05d}",
                "action": ACTIONS[index % len(ACTIONS)],
                "date": day.isoformat(),
            }
        )
    for _ in range(duplicates):
        original = rows[rng.randrange(0, unique)]
        rows.append(dict(original))
    rows.sort(key=lambda row: (row["date"], row["conversion_id"]))
    target = ROOT / period / "conversions.csv"
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["conversion_id", "action", "date"])
        writer.writeheader()
        writer.writerows(rows)


def write_ga4_report(period: str) -> None:
    dates = days(period)
    sessions = [int(v) for v in spread(TOTALS[period]["sessions"], len(dates), 11)]
    metadata: dict = {
        "currencyCode": "CAD",
        "timeZone": "America/Edmonton",
        "subjectToThresholding": False,
        "dataLossFromOtherRow": False,
    }
    if period == AUGUST:
        metadata["samplingMetadatas"] = [
            {"samplesReadCount": "6888000", "samplingSpaceSize": "8400000"}
        ]
        metadata["dataTruncationReasons"] = [
            {
                "dataTruncationType": "DATA_TRUNCATION_TYPE_DATE_RANGE",
                "dataTruncationMessage": "Query date range may not be fully served.",
                "dataTruncationDateRanges": [{"startDate": "2026-08-29", "endDate": "2026-08-31"}],
            }
        ]
    payload = {
        "dimensionHeaders": [{"name": "date"}],
        "metricHeaders": [{"name": "sessions", "type": "TYPE_INTEGER"}],
        "rows": [
            {
                "dimensionValues": [{"value": day.strftime("%Y%m%d")}],
                "metricValues": [{"value": str(sessions[index])}],
            }
            for index, day in enumerate(dates)
        ],
        "rowCount": len(dates),
        "metadata": metadata,
        "kind": "analyticsData#runReport",
    }
    (ROOT / period / "ga4-sessions.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def write_audit_report(period: str) -> None:
    counts = {JULY: (370, 152, 96, 412), AUGUST: (229, 118, 74, 0)}[period]
    events = [
        ("form_submit", ["form_id", "form_name"], counts[0]),
        ("book_appointment", ["appointment_type"], counts[1]),
        ("quote_request", ["service"], counts[2]),
        ("view_service_page", ["service"], 1840 if period == JULY else 1602),
    ]
    payload = {
        "health_score": 96 if period == JULY else 92,
        "source": "export",
        "period": period,
        "events": [
            {
                "plan": {
                    "name": name,
                    "required_parameters": params,
                    "expected_minimum_count": 20,
                },
                "status": "pass",
                "count": count,
                "missing_params": [],
                "partial_params": {},
                "unverifiable_params": [],
                "unexpected_params": [],
            }
            for name, params, count in events
        ],
        "naming_issues": [],
        "ghost_events": [{"name": "scroll_depth_75", "count": 604}],
    }
    (ROOT / period / "audit_report.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def write_gsc_report(period: str) -> None:
    figures = {
        JULY: (1284, 2210, 48120, {"PASS": 96, "NEUTRAL": 4}, "2026-07-31", "2026-07-01"),
        AUGUST: (1180, 1980, 44310, {"PASS": 88, "NEUTRAL": 12}, "2026-08-31", "2026-08-01"),
    }[period]
    known, clicks, impressions, inspected, generated, since = figures
    payload = {
        "generatedAt": generated,
        "since": since,
        "days": 31,
        "sites": [
            {
                "siteUrl": "https://northgateplumbing.example/",
                "label": "Northgate Plumbing",
                "daysWithData": 31,
                "clicks": clicks,
                "impressions": impressions,
                "knownUrls": known,
                "inspected": inspected,
            }
        ],
        "regressions": [],
    }
    (ROOT / period / "gsc-report.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def write_settings(period: str) -> None:
    payload = {
        "ga4": {
            "property_id": "398271465",
            "attribution_model": "data_driven",
            "conversion_window_days": 30,
            "data_filters": ["internal_traffic_exclude"],
        },
        "google_ads": {
            "conversion_counting": "one",
            "click_through_conversion_window_days": 30,
        },
    }
    (ROOT / period / "reporting-settings.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    for period in (JULY, AUGUST):
        (ROOT / period).mkdir(parents=True, exist_ok=True)
        write_metrics(period)
        write_consent(period)
        write_conversions(period, duplicates=0 if period == JULY else 6)
        write_ga4_report(period)
        write_audit_report(period)
        write_gsc_report(period)
        write_settings(period)
        print(
            f"{period}: {DAYS_WITH_DATA[period]} of {calendar_days(period)} calendar days written"
        )


if __name__ == "__main__":
    main()
