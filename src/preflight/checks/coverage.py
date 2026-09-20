"""Checks that consume the output of two other tools in this ecosystem.

``event_coverage`` reads ``audit_report.json`` written by
`ga4-event-auditor <https://github.com/mehranmoghadasi/ga4-event-auditor>`_ — the schema
is ``{"health_score": int, "events": [{"plan": {"name": ...}, "status": ..., "count": ...,
"missing_params": [...], "partial_params": {...}}], "naming_issues": [...],
"ghost_events": [...]}``. Statuses in that file are ``pass``, ``param_issues``,
``below_expected`` and ``missing``.

``index_coverage`` reads the JSON report written by
`gsc-coverage-monitor <https://github.com/mehranmoghadasi/gsc-coverage-monitor>`_ —
``{"generatedAt": ..., "sites": [{"siteUrl": ..., "label": ..., "knownUrls": int,
"clicks": int, "impressions": int, "inspected": {...}}], "regressions": [{"severity": ...}]}``.

``index_coverage`` drop formula:
``drop_pct = (previous_knownUrls - current_knownUrls) / previous_knownUrls * 100``
evaluated per site; the worst site decides the outcome.
"""

from __future__ import annotations

from ..model import CheckResult, Severity, worst
from . import CheckContext, CheckDef
from .ga4_response import severity_param

_SEVERITY_WORDS = {"pass": Severity.PASS, "warn": Severity.WARN, "fail": Severity.FAIL}


def _event_name(event: dict) -> str:
    plan = event.get("plan") or {}
    return str(plan.get("name") or event.get("event_name") or event.get("name") or "(unnamed)")


def run_event_coverage(ctx: CheckContext) -> CheckResult:
    source = ctx.source("source")
    events = source.data.get("events") or []
    required = ctx.param("required_events")
    required_set = {str(name) for name in required} if required else None

    on_missing = severity_param(ctx, "on_missing", "fail")
    on_param_issues = severity_param(ctx, "on_param_issues", "warn")
    on_below_expected = severity_param(ctx, "on_below_expected", "warn")
    on_naming = severity_param(ctx, "on_naming", "warn")

    missing: list[str] = []
    param_issues: list[str] = []
    below: list[str] = []
    for event in events:
        name = _event_name(event)
        if required_set is not None and name not in required_set:
            continue
        status = str(event.get("status", "")).lower()
        if status == "missing":
            missing.append(name)
        elif status == "param_issues":
            param_issues.append(name)
        elif status == "below_expected":
            below.append(name)

    naming = [str(item.get("observed", "?")) for item in (source.data.get("naming_issues") or [])]
    health = source.data.get("health_score")

    detail = {
        "source": source.key,
        "file": source.path.name,
        "health_score": health,
        "missing_events": sorted(missing),
        "parameter_issue_events": sorted(param_issues),
        "below_expected_events": sorted(below),
        "naming_issues": sorted(naming),
        "ghost_events": sorted(
            str(item.get("name", "?")) for item in (source.data.get("ghost_events") or [])
        ),
    }

    findings: list[tuple[Severity, str]] = []
    if missing:
        findings.append(
            (
                on_missing,
                f"{len(missing)} planned event(s) not firing: {', '.join(sorted(missing))}",
            )
        )
    if param_issues:
        findings.append(
            (
                on_param_issues,
                f"{len(param_issues)} event(s) with parameter gaps: "
                + ", ".join(sorted(param_issues)),
            )
        )
    if below:
        findings.append(
            (
                on_below_expected,
                f"{len(below)} event(s) below expected volume: {', '.join(sorted(below))}",
            )
        )
    if naming:
        findings.append(
            (on_naming, f"{len(naming)} event name(s) drifted: {', '.join(sorted(naming))}")
        )

    minimum_health = ctx.param("min_health_score")
    if minimum_health is not None and isinstance(health, (int, float)):
        detail["min_health_score"] = minimum_health
        if health < float(minimum_health):
            findings.append(
                (
                    Severity.FAIL,
                    f"tracking health score {health} is below the agreed minimum {minimum_health}",
                )
            )

    if not findings:
        return CheckResult(
            ctx.spec.id,
            ctx.spec.type,
            Severity.PASS,
            "All planned events are firing with their required parameters",
            detail,
        )
    status = worst([severity for severity, _ in findings])
    note = None
    if status is not Severity.PASS:
        if missing:
            note = (
                f"{len(missing)} tracked action(s) recorded nothing this period, so any "
                "figure that "
                "depends on them is understated in this report."
            )
        else:
            note = (
                "Some tracked actions recorded incomplete detail this period; breakdowns that rely "
                "on that detail are approximate."
            )
    return CheckResult(
        ctx.spec.id,
        ctx.spec.type,
        status,
        "; ".join(message for _, message in findings),
        detail,
        note=note,
    )


def _sites(payload: dict, wanted: str | None) -> list[dict]:
    sites = [site for site in (payload.get("sites") or []) if isinstance(site, dict)]
    if not wanted:
        return sites
    return [s for s in sites if wanted in (s.get("siteUrl"), s.get("label"))]


def run_index_coverage(ctx: CheckContext) -> CheckResult:
    source = ctx.source("source")
    previous = ctx.optional_source("previous_source")
    wanted = ctx.param("site")
    warn_drop = ctx.number("warn_drop_pct", 5.0)
    fail_drop = ctx.number("fail_drop_pct", 10.0)

    severity_map_raw = ctx.param("severity_map") or {"high": "fail", "medium": "warn"}
    severity_map = {
        str(key).lower(): _SEVERITY_WORDS.get(str(value).lower(), Severity.WARN)
        for key, value in severity_map_raw.items()
    }

    current_sites = _sites(source.data, wanted)
    previous_sites = (
        {str(site.get("siteUrl")): site for site in _sites(previous.data, wanted)}
        if previous
        else {}
    )

    per_site: dict[str, dict] = {}
    findings: list[tuple[Severity, str]] = []
    for site in current_sites:
        site_url = str(site.get("siteUrl", "(unknown)"))
        known = site.get("knownUrls")
        entry: dict = {"known_urls": known}
        before_site = previous_sites.get(site_url)
        if before_site and isinstance(known, (int, float)):
            before = before_site.get("knownUrls")
            if isinstance(before, (int, float)) and before > 0:
                drop = (before - known) / before * 100
                entry["previous_known_urls"] = before
                entry["drop_pct"] = round(drop, 2)
                if drop >= fail_drop:
                    findings.append(
                        (
                            Severity.FAIL,
                            f"{site_url}: indexed URL count down {drop:.1f}% "
                            f"({before:,.0f} to {known:,.0f})",
                        )
                    )
                elif drop >= warn_drop:
                    findings.append(
                        (Severity.WARN, f"{site_url}: indexed URL count down {drop:.1f}%")
                    )
        per_site[site_url] = entry

    regressions = [r for r in (source.data.get("regressions") or []) if isinstance(r, dict)]
    by_severity: dict[str, int] = {}
    for regression in regressions:
        level = str(regression.get("severity", "unknown")).lower()
        by_severity[level] = by_severity.get(level, 0) + 1
    for level, count in sorted(by_severity.items()):
        mapped = severity_map.get(level)
        if mapped and mapped is not Severity.PASS:
            findings.append((mapped, f"{count} {level}-severity indexing regression(s) detected"))

    detail = {
        "source": source.key,
        "previous_source": previous.key if previous else None,
        "warn_drop_pct": warn_drop,
        "fail_drop_pct": fail_drop,
        "sites": per_site,
        "regressions_by_severity": by_severity,
    }
    if not current_sites:
        return CheckResult(
            ctx.spec.id,
            ctx.spec.type,
            Severity.WARN,
            "No matching property in the Search Console report",
            detail,
            note=None,
        )
    if not findings:
        return CheckResult(
            ctx.spec.id,
            ctx.spec.type,
            Severity.PASS,
            f"Indexing coverage steady across {len(current_sites)} property/properties",
            detail,
        )
    status = worst([severity for severity, _ in findings])
    return CheckResult(
        ctx.spec.id,
        ctx.spec.type,
        status,
        "; ".join(message for _, message in findings),
        detail,
        note=(
            "Search Console shows fewer pages known or indexed than in the previous period. Some "
            "of the change in search performance in this report is explained by that, not by "
            "ranking movement."
        )
        if status is not Severity.PASS
        else None,
    )


DEFS = (
    CheckDef(
        type="event_coverage",
        run=run_event_coverage,
        description="Planned GA4 events fired with required parameters (ga4-event-auditor JSON).",
        source_params=("source",),
    ),
    CheckDef(
        type="index_coverage",
        run=run_index_coverage,
        description="Search Console indexing did not regress (reads gsc-coverage-monitor JSON).",
        source_params=("source",),
        optional_source_params=("previous_source",),
    ),
)
