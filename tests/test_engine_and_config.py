"""Config validation, the verdict rule, waivers, history and the notes composer."""

from __future__ import annotations

import pytest
import yaml

from preflight.config import load_client, parse_client
from preflight.db import connect, list_runs, save_run
from preflight.engine import execute
from preflight.model import CheckError, CheckResult, RunResult, Severity, Waiver, worst
from preflight.notes import compose, note_lines
from preflight.report import render_html
from tests.conftest import DEMO_CLIENTS

MINIMAL = """
client:
  slug: test-client
  name: Test Client
sources:
  metrics:
    type: metrics_csv
    path: data/{period}/metrics.csv
checks:
  - id: period-complete
    type: period_completeness
    source: metrics
"""


def _config(text: str, tmp_path):
    return parse_client(yaml.safe_load(text), tmp_path)


def test_minimal_config_loads(tmp_path):
    config = _config(MINIMAL, tmp_path)
    assert config.slug == "test-client"
    assert [spec.id for spec in config.checks] == ["period-complete"]


def test_non_kebab_slug_is_rejected(tmp_path):
    with pytest.raises(CheckError) as excinfo:
        _config(MINIMAL.replace("test-client", "Test Client"), tmp_path)
    assert "kebab-case" in str(excinfo.value)


def test_unknown_check_type_is_rejected(tmp_path):
    with pytest.raises(CheckError) as excinfo:
        _config(MINIMAL.replace("period_completeness", "vibes_check"), tmp_path)
    assert "unknown check type" in str(excinfo.value)


def test_check_referring_to_an_undefined_source_is_rejected(tmp_path):
    with pytest.raises(CheckError) as excinfo:
        _config(MINIMAL.replace("source: metrics", "source: mystery"), tmp_path)
    assert "mystery" in str(excinfo.value)


def test_duplicate_check_ids_are_rejected(tmp_path):
    doubled = (
        MINIMAL
        + """
  - id: period-complete
    type: period_completeness
    source: metrics
"""
    )
    with pytest.raises(CheckError):
        _config(doubled, tmp_path)


def test_missing_required_source_param_is_rejected(tmp_path):
    without_source = MINIMAL.replace("    source: metrics\n", "")
    with pytest.raises(CheckError) as excinfo:
        _config(without_source, tmp_path)
    assert "requires source" in str(excinfo.value)


# --- verdict rule -----------------------------------------------------------------


def _result(check_id: str, status: Severity) -> CheckResult:
    return CheckResult(check_id, "period_completeness", status, "summary", {}, note="note text")


def test_worst_severity_wins():
    assert worst([Severity.PASS, Severity.WARN, Severity.FAIL]) is Severity.FAIL
    assert worst([Severity.PASS, Severity.WARN]) is Severity.WARN
    assert worst([]) is Severity.PASS


def test_run_status_is_the_worst_unwaived_check():
    run = RunResult(
        "c",
        "Client",
        "2026-08",
        [_result("a", Severity.PASS), _result("b", Severity.FAIL)],
        "1.0.0",
    )
    assert run.status is Severity.FAIL
    assert run.verdict == "Do not send yet"
    assert run.counts() == {
        "total": 2,
        "passed": 1,
        "warned": 0,
        "failed": 1,
        "blocking": 1,
        "waived": 0,
    }


def test_a_waived_failure_does_not_block_but_is_still_reported():
    failing = _result("b", Severity.FAIL)
    failing.waiver = Waiver("b", "Client agreed the gap is known", "Mehran")
    run = RunResult("c", "Client", "2026-08", [_result("a", Severity.PASS), failing], "1.0.0")
    assert run.status is Severity.PASS
    assert run.counts()["waived"] == 1
    assert run.counts()["failed"] == 1
    markdown = compose(run)
    assert "Client agreed the gap is known" in markdown
    # A waived failure is reported but no longer counted as blocking.
    assert "2 data checks run · 0 blocking" in markdown


def test_waiver_expiry_is_respected():
    waiver = Waiver("b", "temporary", "Mehran", expires_on="2026-08-31")
    assert waiver.active_on("2026-08-31") is True
    assert waiver.active_on("2026-09-01") is False


def test_notes_skip_passing_checks_and_deduplicate():
    duplicate = _result("b", Severity.WARN)
    run = RunResult(
        "c",
        "Client",
        "2026-08",
        [_result("a", Severity.PASS), _result("b", Severity.WARN), duplicate],
        "1.0.0",
    )
    assert note_lines(run) == ["note text"]
    markdown = compose(run)
    assert markdown.count("- note text") == 1
    assert "3 data checks run · 0 blocking · 2 caveats" in markdown


# --- end to end on the demo client ------------------------------------------------


def test_demo_client_produces_the_documented_mix():
    config = load_client(DEMO_CLIENTS, "northgate-plumbing")
    run = execute(config, "2026-08")
    counts = run.counts()
    assert counts == {
        "total": 10,
        "passed": 2,
        "warned": 5,
        "failed": 3,
        "blocking": 3,
        "waived": 0,
    }
    assert run.status is Severity.FAIL
    statuses = {result.check_id: result.status for result in run.results}
    assert statuses["ga4-response-complete"] is Severity.FAIL
    assert statuses["period-complete"] is Severity.FAIL
    assert statuses["nothing-went-silent"] is Severity.FAIL
    assert statuses["events-firing"] is Severity.PASS
    assert statuses["settings-unchanged"] is Severity.PASS


def test_a_check_that_cannot_run_fails_rather_than_being_skipped(tmp_path):
    config = _config(MINIMAL, tmp_path)  # no data files exist under tmp_path
    run = execute(config, "2026-08")
    assert run.status is Severity.FAIL
    assert "could not run" in run.results[0].summary
    assert "file not found" in run.results[0].detail["error"]


def test_html_report_contains_the_verdict_and_the_notes():
    config = load_client(DEMO_CLIENTS, "northgate-plumbing")
    run = execute(config, "2026-08")
    html = render_html(run)
    assert "Do not send yet" in html
    assert "Data notes" in html
    assert "<svg" not in html  # the standalone page has no chart, by design


def test_run_history_is_persisted_and_returned_newest_first(tmp_path):
    config = load_client(DEMO_CLIENTS, "northgate-plumbing")
    connection = connect(tmp_path / "history.db")
    try:
        first = save_run(connection, execute(config, "2026-08"))
        second = save_run(connection, execute(config, "2026-08"))
        rows = list_runs(connection, "northgate-plumbing")
        assert [row["id"] for row in rows] == [second, first]
        assert rows[0]["status"] == "fail"
        assert rows[0]["total"] == 10
    finally:
        connection.close()


def test_waivers_recorded_in_the_database_apply_to_the_next_run(tmp_path):
    from preflight.db import add_waiver, list_waivers

    config = load_client(DEMO_CLIENTS, "northgate-plumbing")
    connection = connect(tmp_path / "history.db")
    try:
        for check_id in ("ga4-response-complete", "period-complete", "nothing-went-silent"):
            add_waiver(
                connection,
                config.slug,
                Waiver(check_id, "Known connector outage, client informed", "Mehran"),
            )
        waivers = list_waivers(connection, config.slug)
        run = execute(config, "2026-08", waivers)
    finally:
        connection.close()
    assert run.status is Severity.WARN  # the three failures are accepted, warnings remain
    assert run.counts()["waived"] == 3
