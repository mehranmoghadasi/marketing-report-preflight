"""Reporting-period arithmetic.

A period is a calendar month written ``YYYY-MM``. Monthly is what agencies report on,
and it is also where most silent data problems hide: a month that is missing its last
three days still renders a perfectly confident-looking chart.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

PERIOD_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def parse_period(period: str) -> tuple[int, int]:
    """Return ``(year, month)`` for ``period``; raise ``ValueError`` if malformed."""
    m = PERIOD_RE.match(period.strip())
    if not m:
        raise ValueError(f"period must look like YYYY-MM (got {period!r})")
    return int(m.group(1)), int(m.group(2))


def previous_period(period: str) -> str:
    """The month before ``period``. ``2026-01`` -> ``2025-12``."""
    year, month = parse_period(period)
    if month == 1:
        return f"{year - 1}-12"
    return f"{year}-{month - 1:02d}"


def period_bounds(period: str) -> tuple[date, date]:
    """First and last calendar day of ``period``, inclusive."""
    year, month = parse_period(period)
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def period_days(period: str) -> list[date]:
    """Every calendar day in ``period``, ascending."""
    start, end = period_bounds(period)
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def parse_date(value: str) -> date:
    """Parse ``YYYY-MM-DD`` or GA4's compact ``YYYYMMDD``."""
    raw = value.strip()
    if len(raw) == 8 and raw.isdigit():
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
    parts = raw.split("-")
    if len(parts) != 3:
        raise ValueError(f"unrecognised date {value!r} (expected YYYY-MM-DD or YYYYMMDD)")
    return date(int(parts[0]), int(parts[1]), int(parts[2]))


def days_between(earlier: date, later: date) -> int:
    """Whole days from ``earlier`` to ``later`` (negative if ``later`` is first)."""
    return (later - earlier).days
