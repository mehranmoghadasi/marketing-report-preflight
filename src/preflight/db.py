"""SQLite persistence: run history and waivers.

History is what turns a one-off check into governance: you can show, per client, that the
August report was sent with two accepted caveats and who accepted them. The schema is
created on connect, so there is no migration step for a single-file database.

Writes happen once per run (and once per waiver) — never per request.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .model import CheckResult, RunResult, Severity, Waiver

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    slug        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    first_seen  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_slug TEXT NOT NULL REFERENCES clients(slug),
    period      TEXT NOT NULL,
    status      TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    total       INTEGER NOT NULL,
    passed      INTEGER NOT NULL,
    warned      INTEGER NOT NULL,
    failed      INTEGER NOT NULL,
    waived      INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    tool_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_client_period ON runs(client_slug, period);
CREATE TABLE IF NOT EXISTS check_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    check_id    TEXT NOT NULL,
    check_type  TEXT NOT NULL,
    status      TEXT NOT NULL,
    effective_status TEXT NOT NULL,
    summary     TEXT NOT NULL,
    note        TEXT,
    detail_json TEXT NOT NULL,
    waiver_reason TEXT,
    waiver_by   TEXT
);
CREATE INDEX IF NOT EXISTS check_results_run ON check_results(run_id);
CREATE TABLE IF NOT EXISTS waivers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_slug TEXT NOT NULL,
    check_id    TEXT NOT NULL,
    reason      TEXT NOT NULL,
    waived_by   TEXT NOT NULL,
    expires_on  TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS waivers_client ON waivers(client_slug, check_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the preflight database and ensure the schema exists."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


def register_client(connection: sqlite3.Connection, slug: str, name: str) -> None:
    connection.execute(
        "INSERT INTO clients (slug, name, first_seen) VALUES (?, ?, ?) "
        "ON CONFLICT(slug) DO UPDATE SET name = excluded.name",
        (slug, name, now_iso()),
    )
    connection.commit()


def save_run(connection: sqlite3.Connection, run: RunResult) -> int:
    """Persist a run and its check results; returns the new run id."""
    register_client(connection, run.client_slug, run.client_name)
    counts = run.counts()
    cursor = connection.execute(
        "INSERT INTO runs (client_slug, period, status, verdict, total, passed, warned, failed,"
        " waived, created_at, tool_version) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            run.client_slug,
            run.period,
            run.status.value,
            run.verdict,
            counts["total"],
            counts["passed"],
            counts["warned"],
            counts["failed"],
            counts["waived"],
            now_iso(),
            run.tool_version,
        ),
    )
    run_id = int(cursor.lastrowid)
    connection.executemany(
        "INSERT INTO check_results (run_id, check_id, check_type, status, effective_status,"
        " summary, note, detail_json, waiver_reason, waiver_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                run_id,
                result.check_id,
                result.check_type,
                result.status.value,
                result.effective_status.value,
                result.summary,
                result.note,
                json.dumps(result.detail, sort_keys=True),
                result.waiver.reason if result.waived and result.waiver else None,
                result.waiver.waived_by if result.waived and result.waiver else None,
            )
            for result in run.results
        ],
    )
    connection.commit()
    return run_id


def list_clients(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        "SELECT c.slug, c.name, COUNT(r.id) AS runs, MAX(r.period) AS latest_period"
        " FROM clients c LEFT JOIN runs r ON r.client_slug = c.slug"
        " GROUP BY c.slug ORDER BY c.slug"
    ).fetchall()
    return [dict(row) for row in rows]


def list_runs(
    connection: sqlite3.Connection, client_slug: str | None = None, limit: int = 50
) -> list[dict]:
    if client_slug:
        rows = connection.execute(
            "SELECT * FROM runs WHERE client_slug = ? ORDER BY id DESC LIMIT ?",
            (client_slug, limit),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_run(connection: sqlite3.Connection, run_id: int) -> dict | None:
    row = connection.execute(
        "SELECT r.*, c.name AS client_name FROM runs r"
        " JOIN clients c ON c.slug = r.client_slug WHERE r.id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    run = dict(row)
    checks = connection.execute(
        "SELECT check_id, check_type, status, effective_status, summary, note, detail_json,"
        " waiver_reason, waiver_by"
        " FROM check_results WHERE run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    run["checks"] = [
        {
            **{key: value for key, value in dict(check).items() if key != "detail_json"},
            "detail": json.loads(check["detail_json"]),
        }
        for check in checks
    ]
    return run


def add_waiver(connection: sqlite3.Connection, client_slug: str, waiver: Waiver) -> int:
    cursor = connection.execute(
        "INSERT INTO waivers (client_slug, check_id, reason, waived_by, expires_on, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (
            client_slug,
            waiver.check_id,
            waiver.reason,
            waiver.waived_by,
            waiver.expires_on,
            waiver.created_at or now_iso(),
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)


def list_waivers(connection: sqlite3.Connection, client_slug: str) -> list[Waiver]:
    rows = connection.execute(
        "SELECT check_id, reason, waived_by, expires_on, created_at FROM waivers"
        " WHERE client_slug = ? ORDER BY id",
        (client_slug,),
    ).fetchall()
    return [
        Waiver(
            check_id=row["check_id"],
            reason=row["reason"],
            waived_by=row["waived_by"],
            expires_on=row["expires_on"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


def rebuild_results(run: dict) -> list[CheckResult]:
    """Turn stored rows back into :class:`CheckResult` objects (for re-rendering)."""
    out = []
    for check in run.get("checks", []):
        waiver = None
        if check.get("waiver_reason"):
            waiver = Waiver(
                check_id=check["check_id"],
                reason=check["waiver_reason"],
                waived_by=check.get("waiver_by") or "",
            )
        out.append(
            CheckResult(
                check["check_id"],
                check["check_type"],
                Severity(check["status"]),
                check["summary"],
                check["detail"],
                note=check.get("note"),
                waiver=waiver,
            )
        )
    return out
