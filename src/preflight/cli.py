"""Command line interface.

``report-preflight run`` is the piece that makes this a gate rather than a dashboard:
with ``--exit-code`` it returns 1 when a check fails, so it can sit in front of whatever
actually sends the report::

    report-preflight run --client acme-dental --period 2026-08 --exit-code \\
      && agency-report-builder --client acme-dental --period 2026-08
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .checks import registry
from .config import discover_clients, load_client
from .db import add_waiver, connect, list_runs, list_waivers, save_run
from .engine import execute
from .model import CheckError, Severity, Waiver
from .notes import compose
from .report import render_console, render_html

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="report-preflight",
        description="Check the data behind a client report before you send it.",
    )
    parser.add_argument("--version", action="version", version=f"report-preflight {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--clients-dir",
            default="clients",
            help="directory containing <slug>/preflight.yml (default: clients)",
        )
        sub.add_argument(
            "--db",
            default="preflight.db",
            help="SQLite history database (default: preflight.db)",
        )

    run_cmd = subparsers.add_parser("run", help="run all checks for one client-period")
    common(run_cmd)
    run_cmd.add_argument("--client", required=True, help="client slug")
    run_cmd.add_argument("--period", required=True, help="reporting period, YYYY-MM")
    run_cmd.add_argument("--out", help="directory to write preflight.json/.html and data-notes.md")
    run_cmd.add_argument("--format", choices=("text", "json"), default="text")
    run_cmd.add_argument(
        "--exit-code",
        action="store_true",
        help=f"return {EXIT_BLOCKED} when any check fails (for cron/CI gating)",
    )
    run_cmd.add_argument("--no-db", action="store_true", help="do not record the run in history")
    run_cmd.add_argument("--no-color", action="store_true", help="plain console output")

    serve_cmd = subparsers.add_parser("serve", help="run the dashboard")
    common(serve_cmd)
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8000)

    waive_cmd = subparsers.add_parser("waive", help="record a decision to accept a failing check")
    common(waive_cmd)
    waive_cmd.add_argument("--client", required=True)
    waive_cmd.add_argument("--check", required=True, help="check id from the client config")
    waive_cmd.add_argument("--reason", required=True, help="client-safe reason, used in data notes")
    waive_cmd.add_argument("--by", required=True, help="who accepted it")
    waive_cmd.add_argument("--expires", help="last date the waiver applies, YYYY-MM-DD")

    history_cmd = subparsers.add_parser("history", help="show recent runs")
    common(history_cmd)
    history_cmd.add_argument("--client", help="limit to one client slug")
    history_cmd.add_argument("--limit", type=int, default=20)

    clients_cmd = subparsers.add_parser("clients", help="list configured clients")
    common(clients_cmd)

    subparsers.add_parser("checks", help="list available check types")
    return parser


def _write_outputs(out_dir: Path, run, notes: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    json_path = out_dir / "preflight.json"
    json_path.write_text(
        json.dumps(run.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    written.append(json_path)
    html_path = out_dir / "preflight.html"
    html_path.write_text(render_html(run), encoding="utf-8")
    written.append(html_path)
    notes_path = out_dir / "data-notes.md"
    notes_path.write_text(notes, encoding="utf-8")
    written.append(notes_path)
    return written


def cmd_run(args: argparse.Namespace) -> int:
    client = load_client(args.clients_dir, args.client)
    waivers: list[Waiver] = []
    if not args.no_db:
        connection = connect(args.db)
        try:
            waivers = list_waivers(connection, client.slug)
        finally:
            connection.close()
    run = execute(client, args.period, waivers)
    notes = compose(run)

    if not args.no_db:
        connection = connect(args.db)
        try:
            save_run(connection, run)
        finally:
            connection.close()

    if args.out:
        written = _write_outputs(Path(args.out), run, notes)
    else:
        written = []

    if args.format == "json":
        payload = run.to_dict()
        payload["notes_markdown"] = notes
        payload["written"] = [str(path) for path in written]
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_console(run, colour=not args.no_color and sys.stdout.isatty()))
        print()
        print(notes.rstrip())
        for path in written:
            print(f"wrote {path}")

    if args.exit_code and run.status is Severity.FAIL:
        return EXIT_BLOCKED
    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    discover_clients(args.clients_dir)  # fail fast on a broken config
    app = create_app(args.clients_dir, args.db)
    print(f"Report Preflight v{__version__} — http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return EXIT_OK


def cmd_waive(args: argparse.Namespace) -> int:
    client = load_client(args.clients_dir, args.client)
    client.check(args.check)
    waiver = Waiver(args.check, args.reason, args.by, args.expires)
    connection = connect(args.db)
    try:
        add_waiver(connection, client.slug, waiver)
    finally:
        connection.close()
    expiry = f" until {args.expires}" if args.expires else " with no expiry"
    print(f"Recorded waiver for {client.slug}/{args.check}{expiry} (by {args.by}).")
    return EXIT_OK


def cmd_history(args: argparse.Namespace) -> int:
    connection = connect(args.db)
    try:
        rows = list_runs(connection, args.client, args.limit)
    finally:
        connection.close()
    if not rows:
        print("No runs recorded yet.")
        return EXIT_OK
    print(
        f"{'id':>4}  {'client':<20} {'period':<8} {'status':<6} "
        f"{'checks':>6} {'fail':>5} {'warn':>5}"
    )
    for row in rows:
        print(
            f"{row['id']:>4}  {row['client_slug']:<20} {row['period']:<8} "
            f"{row['status']:<6} {row['total']:>6} {row['failed']:>5} {row['warned']:>5}"
        )
    return EXIT_OK


def cmd_clients(args: argparse.Namespace) -> int:
    for client in discover_clients(args.clients_dir):
        print(f"{client.slug:<20} {client.name:<32} {len(client.checks)} checks")
    return EXIT_OK


def cmd_checks(_: argparse.Namespace) -> int:
    for name, definition in sorted(registry().items()):
        print(f"{name}\n    {definition.description}")
    return EXIT_OK


HANDLERS = {
    "run": cmd_run,
    "serve": cmd_serve,
    "waive": cmd_waive,
    "history": cmd_history,
    "clients": cmd_clients,
    "checks": cmd_checks,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return HANDLERS[args.command](args)
    except (CheckError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
