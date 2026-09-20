"""Loaders for the files an agency already has on disk.

Preflight never talks to a marketing API and never asks for credentials. It reads the
artefacts you already produce when you build a report:

===========================  ===================================================
source type                  what it expects
===========================  ===================================================
``ga4_report_json``          a saved GA4 Data API ``runReport`` response
``metrics_csv``              long format: ``date,metric,value``
``consent_csv``              ``date,granted,denied``
``conversions_csv``          ``conversion_id,action,date``
``ga4audit_json``            ``audit_report.json`` from ga4-event-auditor
``gsc_monitor_json``         the JSON report from gsc-coverage-monitor
``json``                     any JSON document (used for settings snapshots)
===========================  ===================================================

Keeping ingestion file-based is what makes the whole tool testable and what makes it
runnable by someone who does not have API access to the client's property.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .model import CheckError
from .periods import parse_date

CSV_SCHEMAS: dict[str, tuple[str, ...]] = {
    "metrics_csv": ("date", "metric", "value"),
    "consent_csv": ("date", "granted", "denied"),
    "conversions_csv": ("conversion_id", "action", "date"),
}

JSON_TYPES = ("ga4_report_json", "ga4audit_json", "gsc_monitor_json", "json")

SOURCE_TYPES = tuple(CSV_SCHEMAS) + JSON_TYPES


@dataclass
class SourceSpec:
    """Declaration of one input file, as written in the client YAML."""

    key: str
    type: str
    path_template: str

    def resolve(self, base_dir: Path, context: dict[str, str]) -> Path:
        try:
            rendered = self.path_template.format(**context)
        except KeyError as exc:  # pragma: no cover - guarded by config validation
            raise CheckError(f"source {self.key!r}: unknown placeholder {exc} in path") from exc
        path = Path(rendered)
        return path if path.is_absolute() else base_dir / path


@dataclass
class LoadedSource:
    """A source file that has been read and shape-checked."""

    key: str
    type: str
    path: Path
    data: Any
    rows: list[dict[str, str]] = field(default_factory=list)

    # -- convenience accessors used by checks -----------------------------------
    def numbers(self, column: str) -> list[float]:
        out = []
        for i, row in enumerate(self.rows, start=2):
            raw = (row.get(column) or "").strip()
            try:
                out.append(float(raw))
            except ValueError as exc:
                raise CheckError(
                    f"{self.path.name} line {i}: column {column!r} is not a number ({raw!r})"
                ) from exc
        return out

    def dates(self) -> list[date]:
        """Every distinct date in the source, ascending."""
        found: set[date] = set()
        if self.rows:
            for i, row in enumerate(self.rows, start=2):
                try:
                    found.add(parse_date(row["date"]))
                except (KeyError, ValueError) as exc:
                    raise CheckError(f"{self.path.name} line {i}: bad date value") from exc
        elif self.type == "ga4_report_json":
            found.update(_ga4_dates(self.data))
        elif self.type == "gsc_monitor_json":
            for key in ("generatedAt", "since"):
                value = self.data.get(key)
                if isinstance(value, str):
                    found.add(parse_date(value))
        return sorted(found)


def _ga4_dates(payload: dict) -> set[date]:
    """Pull dates out of a GA4 runReport response that has a ``date`` dimension."""
    headers = [h.get("name") for h in payload.get("dimensionHeaders", [])]
    if "date" not in headers:
        return set()
    index = headers.index("date")
    found = set()
    for row in payload.get("rows", []):
        values = row.get("dimensionValues", [])
        if index < len(values):
            raw = values[index].get("value", "")
            if raw:
                found.add(parse_date(raw))
    return found


def _read_csv(path: Path, source_type: str) -> list[dict[str, str]]:
    required = CSV_SCHEMAS[source_type]
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = [h.strip() for h in (reader.fieldnames or [])]
        missing = [column for column in required if column not in headers]
        if missing:
            raise CheckError(
                f"{path.name}: {source_type} needs column(s) {', '.join(missing)}; "
                f"found {', '.join(headers) or '(no header row)'}"
            )
        rows = [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]
    if not rows:
        raise CheckError(f"{path.name}: no data rows")
    return rows


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CheckError(f"{path.name}: not valid JSON ({exc.msg} at line {exc.lineno})") from exc


def load_source(spec: SourceSpec, base_dir: Path, context: dict[str, str]) -> LoadedSource:
    """Read and shape-check one source. Raises :class:`CheckError` with a usable message."""
    if spec.type not in SOURCE_TYPES:
        raise CheckError(
            f"source {spec.key!r}: unknown type {spec.type!r} "
            f"(known: {', '.join(sorted(SOURCE_TYPES))})"
        )
    path = spec.resolve(base_dir, context)
    if not path.is_file():
        raise CheckError(f"source {spec.key!r}: file not found at {path}")
    if spec.type in CSV_SCHEMAS:
        rows = _read_csv(path, spec.type)
        return LoadedSource(spec.key, spec.type, path, data=rows, rows=rows)
    data = _read_json(path)
    if spec.type == "ga4_report_json" and not isinstance(data, dict):
        raise CheckError(f"source {spec.key!r}: a GA4 runReport response must be a JSON object")
    if spec.type == "ga4audit_json":
        if not isinstance(data, dict) or "events" not in data:
            raise CheckError(
                f"source {spec.key!r}: expected ga4-event-auditor audit_report.json "
                "(an object with an 'events' array)"
            )
    if spec.type == "gsc_monitor_json":
        if not isinstance(data, dict) or "sites" not in data:
            raise CheckError(
                f"source {spec.key!r}: expected a gsc-coverage-monitor report "
                "(an object with a 'sites' array)"
            )
    return LoadedSource(spec.key, spec.type, path, data=data)


def metric_total(source: LoadedSource, metric: str) -> float:
    """Sum the ``value`` column of a ``metrics_csv`` for one metric name."""
    if source.type != "metrics_csv":
        raise CheckError(f"source {source.key!r} is {source.type}, expected metrics_csv")
    total = 0.0
    seen = False
    for i, row in enumerate(source.rows, start=2):
        if row.get("metric") != metric:
            continue
        seen = True
        raw = row.get("value", "")
        try:
            total += float(raw)
        except ValueError as exc:
            raise CheckError(
                f"{source.path.name} line {i}: value {raw!r} for metric {metric!r} is not a number"
            ) from exc
    if not seen:
        raise CheckError(f"{source.path.name}: metric {metric!r} is not present")
    return total


def metric_names(source: LoadedSource) -> list[str]:
    """Sorted distinct metric names in a ``metrics_csv``."""
    return sorted({row.get("metric", "") for row in source.rows if row.get("metric")})


def dotted(data: Any, path: str) -> tuple[bool, Any]:
    """Look up ``a.b.c`` in nested dicts. Returns ``(found, value)``."""
    current = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current
