"""Client configuration: one ``preflight.yml`` per client, validated on load.

Layout on disk::

    clients/
      acme-dental/
        preflight.yml
        data/2026-08/ga4-sessions.json
        data/2026-08/metrics.csv
        ...

Validation is strict and the errors name the offending key, because the whole point of
the tool is that nothing fails silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .checks import CheckSpec, get_check
from .model import CheckError
from .sources import SOURCE_TYPES, SourceSpec

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CONFIG_FILENAME = "preflight.yml"


@dataclass
class ClientConfig:
    """A validated client configuration."""

    slug: str
    name: str
    base_dir: Path
    sources: dict[str, SourceSpec] = field(default_factory=dict)
    checks: list[CheckSpec] = field(default_factory=list)
    config_path: Path | None = None

    def check(self, check_id: str) -> CheckSpec:
        for spec in self.checks:
            if spec.id == check_id:
                return spec
        raise CheckError(f"client {self.slug!r} has no check with id {check_id!r}")


def _require_mapping(value: object, where: str) -> dict:
    if not isinstance(value, dict):
        raise CheckError(f"{where} must be a mapping")
    return value


def parse_client(raw: object, base_dir: Path, config_path: Path | None = None) -> ClientConfig:
    """Build a :class:`ClientConfig` from already-parsed YAML data."""
    document = _require_mapping(raw, "the configuration file")
    client_block = _require_mapping(document.get("client", {}), "client")
    slug = str(client_block.get("slug", "")).strip()
    if not SLUG_RE.match(slug):
        raise CheckError(f"client.slug must be kebab-case (got {slug!r})")
    name = str(client_block.get("name") or slug)

    sources: dict[str, SourceSpec] = {}
    for key, value in _require_mapping(document.get("sources", {}), "sources").items():
        entry = _require_mapping(value, f"sources.{key}")
        source_type = str(entry.get("type", "")).strip()
        if source_type not in SOURCE_TYPES:
            raise CheckError(
                f"sources.{key}.type is {source_type!r}; known types: "
                + ", ".join(sorted(SOURCE_TYPES))
            )
        path_template = str(entry.get("path", "")).strip()
        if not path_template:
            raise CheckError(f"sources.{key}.path is required")
        sources[str(key)] = SourceSpec(str(key), source_type, path_template)
    if not sources:
        raise CheckError("at least one entry under sources: is required")

    raw_checks = document.get("checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise CheckError("checks: must be a non-empty list")

    checks: list[CheckSpec] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_checks, start=1):
        entry = _require_mapping(item, f"checks[{index}]")
        check_id = str(entry.get("id", "")).strip()
        if not check_id:
            raise CheckError(f"checks[{index}].id is required")
        if check_id in seen_ids:
            raise CheckError(f"duplicate check id {check_id!r}")
        seen_ids.add(check_id)
        check_type = str(entry.get("type", "")).strip()
        definition = get_check(check_type)
        params = {key: value for key, value in entry.items() if key not in ("id", "type", "title")}
        spec = CheckSpec(check_id, check_type, str(entry.get("title", "")), params)
        _validate_source_refs(spec, definition, sources)
        checks.append(spec)

    return ClientConfig(slug, name, base_dir, sources, checks, config_path)


def _validate_source_refs(spec: CheckSpec, definition, sources: dict[str, SourceSpec]) -> None:
    for param in definition.source_params:
        if param not in spec.params:
            raise CheckError(f"check {spec.id!r} ({spec.type}) requires {param}:")
        _assert_known(spec, str(spec.params[param]), sources)
    for param in definition.optional_source_params:
        if param in spec.params and spec.params[param]:
            _assert_known(spec, str(spec.params[param]), sources)
    for param in definition.source_list_params:
        keys = spec.params.get(param)
        if keys is None:
            raise CheckError(f"check {spec.id!r} ({spec.type}) requires {param}:")
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list) or not keys:
            raise CheckError(f"check {spec.id!r}: {param} must be a non-empty list")
        for key in keys:
            _assert_known(spec, str(key), sources)


def _assert_known(spec: CheckSpec, key: str, sources: dict[str, SourceSpec]) -> None:
    if key not in sources:
        known = ", ".join(sorted(sources))
        raise CheckError(
            f"check {spec.id!r} refers to source {key!r}, which is not defined ({known})"
        )


def load_client_file(path: str | Path) -> ClientConfig:
    """Load and validate one ``preflight.yml``."""
    config_path = Path(path)
    if not config_path.is_file():
        raise CheckError(f"configuration file not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CheckError(f"{config_path.name}: invalid YAML ({exc})") from exc
    return parse_client(raw, config_path.parent, config_path)


def discover_clients(config_dir: str | Path) -> list[ClientConfig]:
    """Load every ``<config_dir>/*/preflight.yml``, sorted by slug."""
    directory = Path(config_dir)
    if not directory.is_dir():
        raise CheckError(f"clients directory not found: {directory}")
    found = [
        load_client_file(candidate) for candidate in sorted(directory.glob(f"*/{CONFIG_FILENAME}"))
    ]
    if not found:
        raise CheckError(f"no {CONFIG_FILENAME} files under {directory}")
    return sorted(found, key=lambda client: client.slug)


def load_client(config_dir: str | Path, slug: str) -> ClientConfig:
    """Load one client by slug from a clients directory."""
    path = Path(config_dir) / slug / CONFIG_FILENAME
    if not path.is_file():
        available = ", ".join(
            sorted(p.parent.name for p in Path(config_dir).glob(f"*/{CONFIG_FILENAME}"))
        )
        raise CheckError(
            f"unknown client {slug!r}" + (f" (available: {available})" if available else "")
        )
    return load_client_file(path)
