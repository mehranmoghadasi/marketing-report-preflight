"""HTTP API behaviour and the determinism the committed demo output depends on."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from preflight.api import create_app
from preflight.config import load_client
from preflight.engine import execute
from preflight.notes import compose
from preflight.report import render_html
from tests.conftest import DEMO_CLIENTS, EXAMPLES


@pytest.fixture
def client(tmp_path):
    app = create_app(DEMO_CLIENTS, tmp_path / "api.db")
    with TestClient(app) as test_client:
        yield test_client


def test_health_reports_the_version(client):
    payload = client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert payload["version"].count(".") == 2


def test_clients_endpoint_lists_the_demo_client(client):
    payload = client.get("/api/clients").json()
    assert [entry["slug"] for entry in payload] == ["northgate-plumbing"]
    assert payload[0]["checks"] == 10


def test_client_detail_includes_check_descriptions(client):
    payload = client.get("/api/clients/northgate-plumbing").json()
    assert len(payload["sources"]) == 11
    descriptions = {check["id"]: check["description"] for check in payload["checks"]}
    assert "ResponseMetaData" in descriptions["ga4-response-complete"]


def test_unknown_client_is_a_404(client):
    response = client.get("/api/clients/not-a-client")
    assert response.status_code == 404


def test_run_then_read_returns_the_same_verdict(client):
    created = client.post("/api/runs", json={"client": "northgate-plumbing", "period": "2026-08"})
    assert created.status_code == 201
    payload = created.json()
    assert payload["status"] == "fail"
    assert payload["counts"]["total"] == 10
    assert "Data notes" in payload["notes_markdown"]

    fetched = client.get(f"/api/runs/{payload['id']}").json()
    assert fetched["status"] == "fail"
    assert len(fetched["checks"]) == 10
    assert fetched["notes_markdown"] == payload["notes_markdown"]


def test_a_malformed_period_is_a_400(client):
    response = client.post("/api/runs", json={"client": "northgate-plumbing", "period": "August"})
    assert response.status_code == 400
    assert "YYYY-MM" in response.json()["detail"]


def test_unknown_run_is_a_404(client):
    assert client.get("/api/runs/9999").status_code == 404


def test_waiver_changes_the_next_run_verdict(client):
    for check_id in ("ga4-response-complete", "period-complete", "nothing-went-silent"):
        response = client.post(
            "/api/clients/northgate-plumbing/waivers",
            json={
                "check_id": check_id,
                "reason": "Connector outage already raised with the client",
                "waived_by": "Mehran",
            },
        )
        assert response.status_code == 201
    payload = client.post(
        "/api/runs", json={"client": "northgate-plumbing", "period": "2026-08"}
    ).json()
    assert payload["status"] == "warn"
    assert payload["counts"]["waived"] == 3


def test_waiver_for_an_unknown_check_is_rejected(client):
    response = client.post(
        "/api/clients/northgate-plumbing/waivers",
        json={"check_id": "no-such-check", "reason": "x", "waived_by": "y"},
    )
    assert response.status_code == 400


def test_notes_endpoint_returns_plain_markdown(client):
    payload = client.post(
        "/api/runs", json={"client": "northgate-plumbing", "period": "2026-08"}
    ).json()
    response = client.get(f"/api/runs/{payload['id']}/notes.md")
    assert response.status_code == 200
    assert response.text.startswith("## Data notes")


# --- determinism ------------------------------------------------------------------


def test_two_runs_over_the_same_data_are_byte_identical():
    config = load_client(DEMO_CLIENTS, "northgate-plumbing")
    first = execute(config, "2026-08")
    second = execute(config, "2026-08")
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(
        second.to_dict(), sort_keys=True
    )
    assert render_html(first) == render_html(second)
    assert compose(first) == compose(second)


def test_committed_demo_artefacts_match_a_fresh_run():
    """The committed examples/output files are what the tool produces, not hand-written."""
    config = load_client(DEMO_CLIENTS, "northgate-plumbing")
    run = execute(config, "2026-08")
    expected_json = json.loads((EXAMPLES / "output" / "preflight.json").read_text(encoding="utf-8"))
    assert expected_json == run.to_dict()
    assert (EXAMPLES / "output" / "data-notes.md").read_text(encoding="utf-8") == compose(run)
    assert (EXAMPLES / "output" / "preflight.html").read_text(encoding="utf-8") == render_html(run)
