"""Checks that read other tools' output, plus duplicate detection and config drift."""

from __future__ import annotations

from preflight.model import Severity
from tests.conftest import make_source, run_check


def audit_payload(events: list[tuple[str, str]], health: int = 95, naming=None) -> dict:
    return {
        "health_score": health,
        "source": "export",
        "events": [
            {
                "plan": {"name": name, "required_parameters": ["id"]},
                "status": status,
                "count": 0 if status == "missing" else 100,
                "missing_params": ["id"] if status == "param_issues" else [],
                "partial_params": {},
            }
            for name, status in events
        ],
        "naming_issues": naming or [],
        "ghost_events": [],
    }


def _events(payload: dict, **params):
    sources = {"audit": make_source("audit", "ga4audit_json", data=payload)}
    return run_check("event_coverage", {"source": "audit", **params}, sources)


def test_all_events_passing_passes():
    result = _events(audit_payload([("purchase", "pass"), ("generate_lead", "pass")]))
    assert result.status is Severity.PASS


def test_missing_event_fails_and_is_named():
    result = _events(audit_payload([("purchase", "pass"), ("generate_lead", "missing")]))
    assert result.status is Severity.FAIL
    assert result.detail["missing_events"] == ["generate_lead"]
    assert "1 tracked action" in result.note


def test_parameter_gaps_only_warn():
    result = _events(audit_payload([("purchase", "param_issues")]))
    assert result.status is Severity.WARN
    assert result.detail["parameter_issue_events"] == ["purchase"]


def test_naming_drift_warns():
    result = _events(
        audit_payload([("generate_lead", "pass")], naming=[{"observed": "Generate_Lead"}])
    )
    assert result.status is Severity.WARN
    assert result.detail["naming_issues"] == ["Generate_Lead"]


def test_required_events_filter_ignores_other_events():
    payload = audit_payload([("purchase", "missing"), ("generate_lead", "pass")])
    result = _events(payload, required_events=["generate_lead"])
    assert result.status is Severity.PASS


def test_health_score_below_the_minimum_fails():
    result = _events(audit_payload([("purchase", "pass")], health=55), min_health_score=70)
    assert result.status is Severity.FAIL


# --- index_coverage ---------------------------------------------------------------


def gsc_payload(known: int, regressions=None) -> dict:
    return {
        "generatedAt": "2026-08-31",
        "since": "2026-08-01",
        "sites": [
            {
                "siteUrl": "https://example.test/",
                "label": "Example",
                "knownUrls": known,
                "clicks": 100,
                "impressions": 2000,
                "inspected": {"PASS": 20},
            }
        ],
        "regressions": regressions or [],
    }


def _index(now: int, before: int | None = None, regressions=None, **params):
    sources = {"gsc": make_source("gsc", "gsc_monitor_json", data=gsc_payload(now, regressions))}
    spec = {"source": "gsc", **params}
    if before is not None:
        sources["prev"] = make_source("prev", "gsc_monitor_json", data=gsc_payload(before))
        spec["previous_source"] = "prev"
    return run_check("index_coverage", spec, sources)


def test_index_drop_percentage_is_hand_checkable():
    # (5000 - 4300) / 5000 * 100 = 14.0
    result = _index(4300, 5000, warn_drop_pct=5, fail_drop_pct=10)
    assert result.detail["sites"]["https://example.test/"]["drop_pct"] == 14.0
    assert result.status is Severity.FAIL


def test_small_index_drop_warns():
    # (1284 - 1180) / 1284 * 100 = 8.099... -> 8.1
    result = _index(1180, 1284, warn_drop_pct=5, fail_drop_pct=10)
    assert result.detail["sites"]["https://example.test/"]["drop_pct"] == 8.1
    assert result.status is Severity.WARN


def test_steady_index_passes():
    assert _index(1284, 1284).status is Severity.PASS


def test_high_severity_regression_fails():
    result = _index(1284, 1284, regressions=[{"severity": "high", "type": "deindexed"}])
    assert result.status is Severity.FAIL
    assert result.detail["regressions_by_severity"] == {"high": 1}


# --- duplicate_conversions --------------------------------------------------------


def conversions_rows(ids: list[str]) -> list[dict]:
    return [
        {"conversion_id": value, "action": "form_submit", "date": "2026-08-01"} for value in ids
    ]


def _dupes(ids: list[str], **params):
    sources = {
        "conv": make_source("conv", "conversions_csv", rows=conversions_rows(ids), filename="c.csv")
    }
    return run_check("duplicate_conversions", {"source": "conv", **params}, sources)


def test_duplicate_rate_is_rows_minus_distinct_over_rows():
    # 10 rows, 8 distinct -> (10 - 8) / 10 * 100 = 20.0
    ids = [f"c{i}" for i in range(8)] + ["c1", "c2"]
    result = _dupes(ids, warn_rate_pct=1, fail_rate_pct=5)
    assert result.detail["duplicate_rows"] == 2
    assert result.detail["duplicate_rate_pct"] == 20.0
    assert result.status is Severity.FAIL


def test_a_few_duplicates_only_warn():
    # 100 rows, 2 repeats -> 2.0%
    ids = [f"c{i}" for i in range(98)] + ["c1", "c2"]
    result = _dupes(ids, warn_rate_pct=1, fail_rate_pct=5)
    assert result.detail["duplicate_rate_pct"] == 2.0
    assert result.status is Severity.WARN


def test_no_duplicates_passes():
    result = _dupes([f"c{i}" for i in range(50)])
    assert result.status is Severity.PASS
    assert result.detail["duplicate_rows"] == 0


# --- config_drift -----------------------------------------------------------------


def _drift(now: dict, before: dict, keys: list[str], **params):
    sources = {
        "now": make_source("now", "json", data=now),
        "before": make_source("before", "json", data=before),
    }
    return run_check(
        "config_drift",
        {"source": "now", "previous_source": "before", "watched_keys": keys, **params},
        sources,
    )


def test_changed_watched_key_fails_with_both_values():
    result = _drift(
        {"ga4": {"conversion_window_days": 90}},
        {"ga4": {"conversion_window_days": 30}},
        ["ga4.conversion_window_days"],
    )
    assert result.status is Severity.FAIL
    changed = [entry for entry in result.detail["changes"] if entry["status"] == "changed"]
    assert changed[0]["previous"] == 30
    assert changed[0]["current"] == 90


def test_change_outside_the_watched_keys_is_ignored():
    result = _drift(
        {"ga4": {"conversion_window_days": 30, "timezone": "America/Edmonton"}},
        {"ga4": {"conversion_window_days": 30, "timezone": "UTC"}},
        ["ga4.conversion_window_days"],
    )
    assert result.status is Severity.PASS


def test_missing_watched_key_warns_and_names_the_file():
    result = _drift(
        {"ga4": {}}, {"ga4": {"conversion_window_days": 30}}, ["ga4.conversion_window_days"]
    )
    assert result.status is Severity.WARN
    assert result.detail["changes"][0]["absent_from"] == "now"
