"""Run every configured check for one client-period and derive the verdict.

Two deliberate decisions live here:

1. **A check that cannot run counts as a failure.** If a source file is missing or
   malformed, the run reports ``fail`` for that check rather than skipping it. A gate that
   silently skips is worse than no gate.
2. **Sources are loaded lazily and cached.** A client config can declare sources used by
   only some checks, and each file is read at most once per run.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from . import __version__
from .checks import CheckContext, get_check
from .config import ClientConfig
from .model import CheckError, CheckResult, RunResult, Severity, Waiver
from .periods import period_bounds, previous_period
from .sources import LoadedSource, load_source


def _source_loader(config: ClientConfig, period: str) -> Callable[[str], LoadedSource]:
    context = {"period": period, "prev_period": previous_period(period)}
    cache: dict[str, LoadedSource] = {}

    def resolve(key: str) -> LoadedSource:
        if key not in cache:
            spec = config.sources.get(key)
            if spec is None:
                raise CheckError(f"source {key!r} is not defined for client {config.slug!r}")
            cache[key] = load_source(spec, Path(config.base_dir), context)
        return cache[key]

    return resolve


def execute(
    config: ClientConfig,
    period: str,
    waivers: list[Waiver] | None = None,
) -> RunResult:
    """Run all checks for ``period`` and return the :class:`RunResult`."""
    resolve = _source_loader(config, period)
    _, period_end = period_bounds(period)
    active: dict[str, Waiver] = {}
    for waiver in waivers or []:
        if waiver.active_on(period_end.isoformat()):
            active[waiver.check_id] = waiver

    results: list[CheckResult] = []
    for spec in config.checks:
        definition = get_check(spec.type)
        context = CheckContext(spec, period, resolve)
        try:
            result = definition.run(context)
        except CheckError as exc:
            result = CheckResult(
                spec.id,
                spec.type,
                Severity.FAIL,
                f"check could not run: {exc}",
                {"error": str(exc)},
                note=(
                    "One data-quality check could not be completed for this period, so the "
                    "figures below have not been fully verified."
                ),
            )
        waiver = active.get(spec.id)
        if waiver and result.status is not Severity.PASS:
            result.waiver = waiver
        results.append(result)

    return RunResult(config.slug, config.name, period, results, __version__)
