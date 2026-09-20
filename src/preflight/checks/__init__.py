"""Check registry and the context object every check receives.

A check is a pure function ``(CheckContext) -> CheckResult``. It reads sources through
the context, never from disk directly, which is why the whole suite can be tested
against in-memory fixtures.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..model import CheckError, CheckResult
from ..sources import LoadedSource


@dataclass
class CheckSpec:
    """One entry from the ``checks:`` list in a client's YAML."""

    id: str
    type: str
    title: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def display_title(self) -> str:
        return self.title or self.id


class CheckContext:
    """What a check is allowed to see: its own spec, the period, and its sources."""

    def __init__(self, spec: CheckSpec, period: str, resolver: Callable[[str], LoadedSource]):
        self.spec = spec
        self.period = period
        self._resolver = resolver

    # -- parameters --------------------------------------------------------------
    def param(self, name: str, default: Any = None) -> Any:
        return self.spec.params.get(name, default)

    def required_param(self, name: str) -> Any:
        if name not in self.spec.params:
            raise CheckError(f"check {self.spec.id!r}: missing required setting {name!r}")
        return self.spec.params[name]

    def number(self, name: str, default: float) -> float:
        value = self.spec.params.get(name, default)
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise CheckError(
                f"check {self.spec.id!r}: {name} must be a number (got {value!r})"
            ) from exc

    # -- sources -----------------------------------------------------------------
    def source(self, param: str = "source") -> LoadedSource:
        key = self.required_param(param)
        return self._resolver(str(key))

    def optional_source(self, param: str) -> LoadedSource | None:
        key = self.spec.params.get(param)
        return self._resolver(str(key)) if key else None

    def source_list(self, param: str = "sources") -> list[LoadedSource]:
        keys = self.required_param(param)
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list) or not keys:
            raise CheckError(
                f"check {self.spec.id!r}: {param} must be a non-empty list of source keys"
            )
        return [self._resolver(str(key)) for key in keys]


@dataclass(frozen=True)
class CheckDef:
    """Registry entry: the callable plus which params name sources (for validation)."""

    type: str
    run: Callable[[CheckContext], CheckResult]
    description: str
    source_params: tuple[str, ...] = ()
    optional_source_params: tuple[str, ...] = ()
    source_list_params: tuple[str, ...] = ()


def _build_registry() -> dict[str, CheckDef]:
    from . import completeness, config_drift, conversions, coverage, deltas, ga4_response

    defs = [
        *ga4_response.DEFS,
        *completeness.DEFS,
        *deltas.DEFS,
        *coverage.DEFS,
        *conversions.DEFS,
        *config_drift.DEFS,
    ]
    return {definition.type: definition for definition in defs}


_REGISTRY: dict[str, CheckDef] | None = None


def registry() -> dict[str, CheckDef]:
    """All known check types, keyed by type name."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def get_check(check_type: str) -> CheckDef:
    try:
        return registry()[check_type]
    except KeyError as exc:
        known = ", ".join(sorted(registry()))
        raise CheckError(f"unknown check type {check_type!r} (known types: {known})") from exc
