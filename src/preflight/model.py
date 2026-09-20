"""Core value objects: severity, check results, run results.

The verdict rule for a whole run is deliberately *not* a weighted score. It is:

    run_status = the worst severity among all check results that are not waived

with severity ordered  pass < warn < fail.  A reader can recompute a run verdict by
eye from the per-check statuses, which is the point: an agency has to be able to
defend the verdict to a client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    """Check outcome. Ordered by ``RANK`` below (pass < warn < fail)."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


#: Severity ranking used by :func:`worst`. Higher is worse.
RANK: dict[Severity, int] = {Severity.PASS: 0, Severity.WARN: 1, Severity.FAIL: 2}

#: What each run status means for the person about to send the report.
VERDICT_TEXT: dict[Severity, str] = {
    Severity.PASS: "Clear to send",
    Severity.WARN: "Send with data notes",
    Severity.FAIL: "Do not send yet",
}


def worst(statuses: list[Severity]) -> Severity:
    """Return the worst severity in ``statuses`` (``pass`` for an empty list)."""
    if not statuses:
        return Severity.PASS
    return max(statuses, key=lambda s: RANK[s])


class CheckError(Exception):
    """Raised when a check cannot run at all (bad config, unreadable source)."""


@dataclass
class Waiver:
    """A human decision to let a failing check through, recorded with a reason."""

    check_id: str
    reason: str
    waived_by: str
    expires_on: str | None = None
    created_at: str | None = None

    def active_on(self, date_str: str) -> bool:
        """True if the waiver still applies on ``date_str`` (YYYY-MM-DD)."""
        if not self.expires_on:
            return True
        return date_str <= self.expires_on


@dataclass
class CheckResult:
    """Outcome of one check against one client-period."""

    check_id: str
    check_type: str
    status: Severity
    summary: str
    detail: dict = field(default_factory=dict)
    note: str | None = None
    waiver: Waiver | None = None

    @property
    def waived(self) -> bool:
        return self.waiver is not None and self.status is not Severity.PASS

    @property
    def effective_status(self) -> Severity:
        """Status used for the run verdict: a waived check cannot block."""
        return Severity.PASS if self.waived else self.status

    def to_dict(self) -> dict:
        out = {
            "check_id": self.check_id,
            "check_type": self.check_type,
            "status": self.status.value,
            "effective_status": self.effective_status.value,
            "summary": self.summary,
            "detail": self.detail,
        }
        if self.note:
            out["note"] = self.note
        if self.waiver:
            out["waiver"] = {
                "reason": self.waiver.reason,
                "waived_by": self.waiver.waived_by,
                "expires_on": self.waiver.expires_on,
            }
        return out


@dataclass
class RunResult:
    """All check results for one client-period, plus the derived verdict."""

    client_slug: str
    client_name: str
    period: str
    results: list[CheckResult]
    tool_version: str

    @property
    def status(self) -> Severity:
        return worst([r.effective_status for r in self.results])

    @property
    def verdict(self) -> str:
        return VERDICT_TEXT[self.status]

    def counts(self) -> dict[str, int]:
        return {
            "total": len(self.results),
            "passed": sum(1 for r in self.results if r.status is Severity.PASS),
            "warned": sum(1 for r in self.results if r.status is Severity.WARN),
            "failed": sum(1 for r in self.results if r.status is Severity.FAIL),
            "blocking": sum(1 for r in self.results if r.status is Severity.FAIL and not r.waived),
            "waived": sum(1 for r in self.results if r.waived),
        }

    def blocking(self) -> list[CheckResult]:
        return [r for r in self.results if r.effective_status is Severity.FAIL]

    def to_dict(self) -> dict:
        return {
            "client": {"slug": self.client_slug, "name": self.client_name},
            "period": self.period,
            "status": self.status.value,
            "verdict": self.verdict,
            "counts": self.counts(),
            "checks": [r.to_dict() for r in self.results],
            "tool_version": self.tool_version,
        }
