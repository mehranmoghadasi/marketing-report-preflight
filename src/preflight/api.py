"""HTTP API and static hosting for the dashboard.

The API is the same code path the CLI uses: ``POST /api/runs`` calls
:func:`preflight.engine.execute` and persists the result, so a run triggered from the UI
and a run triggered from cron are indistinguishable in the history.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .checks import get_check
from .config import discover_clients, load_client
from .db import (
    add_waiver,
    connect,
    get_run,
    list_clients,
    list_runs,
    list_waivers,
    rebuild_results,
    save_run,
)
from .engine import execute
from .model import CheckError, RunResult, Waiver
from .notes import compose

WEB_DIR = Path(__file__).parent / "web"


class RunRequest(BaseModel):
    client: str
    period: str


class WaiverRequest(BaseModel):
    check_id: str
    reason: str
    waived_by: str
    expires_on: str | None = None


def create_app(clients_dir: str | Path, db_path: str | Path) -> FastAPI:
    """Build the FastAPI application for a given clients directory and database."""
    clients_root = Path(clients_dir)
    database = Path(db_path)
    app = FastAPI(title="Report Preflight", version=__version__)

    def open_db():
        return connect(database)

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "clients_dir": str(clients_root),
            "database": str(database),
        }

    @app.get("/api/clients")
    def clients() -> list[dict]:
        try:
            configured = discover_clients(clients_root)
        except CheckError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        connection = open_db()
        try:
            history = {row["slug"]: row for row in list_clients(connection)}
        finally:
            connection.close()
        return [
            {
                "slug": client.slug,
                "name": client.name,
                "checks": len(client.checks),
                "runs": history.get(client.slug, {}).get("runs", 0),
                "latest_period": history.get(client.slug, {}).get("latest_period"),
            }
            for client in configured
        ]

    @app.get("/api/clients/{slug}")
    def client_detail(slug: str) -> dict:
        client = _load(clients_root, slug)
        connection = open_db()
        try:
            waivers = list_waivers(connection, slug)
        finally:
            connection.close()
        return {
            "slug": client.slug,
            "name": client.name,
            "config_path": str(client.config_path) if client.config_path else None,
            "sources": [
                {"key": key, "type": spec.type, "path": spec.path_template}
                for key, spec in client.sources.items()
            ],
            "checks": [
                {
                    "id": spec.id,
                    "type": spec.type,
                    "title": spec.display_title(),
                    "description": get_check(spec.type).description,
                }
                for spec in client.checks
            ],
            "waivers": [
                {
                    "check_id": waiver.check_id,
                    "reason": waiver.reason,
                    "waived_by": waiver.waived_by,
                    "expires_on": waiver.expires_on,
                }
                for waiver in waivers
            ],
        }

    @app.get("/api/clients/{slug}/runs")
    def client_runs(slug: str, limit: int = 20) -> list[dict]:
        _load(clients_root, slug)
        connection = open_db()
        try:
            return list_runs(connection, slug, limit)
        finally:
            connection.close()

    @app.post("/api/runs", status_code=201)
    def create_run(request: RunRequest) -> dict:
        client = _load(clients_root, request.client)
        connection = open_db()
        try:
            waivers = list_waivers(connection, client.slug)
            try:
                run = execute(client, request.period, waivers)
            except (CheckError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            run_id = save_run(connection, run)
        finally:
            connection.close()
        payload = run.to_dict()
        payload["id"] = run_id
        payload["notes_markdown"] = compose(run)
        return payload

    @app.get("/api/runs/{run_id}")
    def read_run(run_id: int) -> dict:
        connection = open_db()
        try:
            run = get_run(connection, run_id)
        finally:
            connection.close()
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        run["notes_markdown"] = compose(_as_run_result(run))
        return run

    @app.get("/api/runs/{run_id}/notes.md", response_class=PlainTextResponse)
    def read_notes(run_id: int) -> str:
        connection = open_db()
        try:
            run = get_run(connection, run_id)
        finally:
            connection.close()
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        return compose(_as_run_result(run))

    @app.post("/api/clients/{slug}/waivers", status_code=201)
    def create_waiver(slug: str, request: WaiverRequest) -> dict:
        client = _load(clients_root, slug)
        try:
            client.check(request.check_id)
        except CheckError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        waiver = Waiver(
            check_id=request.check_id,
            reason=request.reason,
            waived_by=request.waived_by,
            expires_on=request.expires_on,
        )
        connection = open_db()
        try:
            waiver_id = add_waiver(connection, slug, waiver)
        finally:
            connection.close()
        return {"id": waiver_id, "client": slug, "check_id": request.check_id}

    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(str(WEB_DIR / "index.html"))

    return app


def _load(clients_root: Path, slug: str):
    try:
        return load_client(clients_root, slug)
    except CheckError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _as_run_result(run: dict) -> RunResult:
    return RunResult(
        client_slug=run["client_slug"],
        client_name=run.get("client_name") or run["client_slug"],
        period=run["period"],
        results=rebuild_results(run),
        tool_version=run.get("tool_version", __version__),
    )
