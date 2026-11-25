"""Expected cadence of a table: missing and late periods, gaps between periods, and periods still settling.

Pure and calendar-based. A declared ``cadence`` (``day``, ``week`` or ``month``) turns the period texts into a
calendar over their ISO start date (``period_date``). A period is *due* once ``as_of`` is strictly after its end
plus ``expected_by`` days. ``period_missing`` marks a due calendar period between the first data period and the
expected latest one that has no rows; ``period_late`` marks the expected latest period when it has no rows.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import PERIOD_LATE, PERIOD_MISSING
from bricksmith.monitor.stability.metrics import TABLE, Context, judge, result_row
from bricksmith.monitor.stability.profile import period_date

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from bricksmith.monitor.reference import Reference
    from bricksmith.monitor.stability.config import StabilityConfig

_FORMATS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\d{4}-\d{2}-\d{2}"), "%Y-%m-%d"),
    (re.compile(r"^\d{4}-\d{2}$"), "%Y-%m"),
    (re.compile(r"^\d{8}$"), "%Y%m%d"),
    (re.compile(r"^\d{6}$"), "%Y%m"),
)


def today() -> date:
    """Return today's UTC date, the default ``as_of``."""
    return datetime.now(UTC).date()


def parse_as_of(value: str | date | None) -> date:
    """Return ``value`` (an ISO date or a date) as a date; ``None`` is today in UTC."""
    if value is None:
        return today()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        msg = f"stability: as_of must be an ISO date, got {value!r}"
        raise MonitorError(msg) from None


def step(start: date, cadence: str, count: int = 1) -> date:
    """Return the start of the period ``count`` periods after ``start`` (negative counts go back)."""
    if cadence == "day":
        return start + timedelta(days=count)
    if cadence == "week":
        return start + timedelta(weeks=count)
    index = start.year * 12 + start.month - 1 + count
    year, month = divmod(index, 12)
    return date(year, month + 1, min(start.day, calendar.monthrange(year, month + 1)[1]))


def period_end(start: date, cadence: str) -> date:
    """Return the last day of the period that starts on ``start``."""
    return step(start, cadence) - timedelta(days=1)


def is_due(start: date, config: StabilityConfig, as_of: date) -> bool:
    """Tell whether the period starting on ``start`` is overdue: ``as_of`` is after its end plus ``expected_by``."""
    return as_of > period_end(start, config.cadence) + timedelta(days=config.expected_by)


def period_text_format(sample: str) -> Callable[[date], str]:
    """Return the function that writes a date in the text format of ``sample`` (a data period)."""
    for pattern, fmt in _FORMATS:
        if pattern.match(sample):
            return lambda d: d.strftime(fmt)
    return lambda d: d.isoformat()


@dataclass(frozen=True)
class Calendar:
    """The data periods of a table on its cadence: their dates, and how to write a missing one."""

    cadence: str
    dates: dict[str, date]
    write: Callable[[date], str]

    @property
    def present(self) -> set[date]:
        """Return the start dates that have data."""
        return set(self.dates.values())


def build_calendar(config: StabilityConfig, periods: Iterable[str]) -> Calendar:
    """Return the calendar of ``periods``; a period that is not a date refuses the cadence."""
    found: dict[str, date] = {}
    for text in periods:
        iso = period_date(text)
        if iso is None:
            msg = f"stability: cadence {config.cadence!r} needs date periods, but {text!r} is not a date"
            raise MonitorError(msg)
        found[text] = date.fromisoformat(iso)
    latest = max(found, key=found.__getitem__, default="")
    return Calendar(config.cadence, found, period_text_format(latest))


def expected_latest(cal: Calendar, config: StabilityConfig, as_of: date) -> date | None:
    """Return the start of the latest period due at ``as_of`` (never before the latest data period)."""
    if not cal.dates:
        return None
    latest = max(cal.dates.values())
    while is_due(step(latest, cal.cadence), config, as_of):
        latest = step(latest, cal.cadence)
    return latest


def missing_starts(cal: Calendar, config: StabilityConfig, as_of: date) -> tuple[list[date], date | None]:
    """Return ``(due calendar periods without rows, the expected latest period)``, bounded by ``max_periods``."""
    last = expected_latest(cal, config, as_of)
    if last is None:
        return [], None
    first = max(min(cal.dates.values()), step(last, cal.cadence, -(config.max_periods - 1)))
    found: list[date] = []
    cursor = first
    while cursor <= last:
        if cursor not in cal.present and is_due(cursor, config, as_of):
            found.append(cursor)
        cursor = step(cursor, cal.cadence)
    return found, last


def gap_between(cal: Calendar, first: date, current: date) -> int:
    """Return how many calendar periods without rows lie between ``first`` and ``current`` (exclusive)."""
    present = cal.present
    count, cursor = 0, step(first, cal.cadence)
    while cursor < current:
        count += cursor not in present
        cursor = step(cursor, cal.cadence)
    return count


def settling_periods(
    config: StabilityConfig, periods: Sequence[str], as_of: date, cal: Calendar | None = None
) -> set[str]:
    """Return the periods still receiving late data: the latest ``settle_periods``, or ending within ``settle_days``."""
    found = set(periods[len(periods) - config.settle_periods :]) if config.settle_periods else set()
    if config.settle_days and cal is not None:
        found |= {
            p
            for p, start in cal.dates.items()
            if as_of - period_end(start, cal.cadence) < timedelta(days=config.settle_days)
        }
    return found


def _row(  # noqa: PLR0913 - one keyword per result field
    ctx: Context, metric: str, start: date, cal: Calendar, *, value: float, reason: str
) -> dict[str, Any]:
    period = cal.write(start)
    light = judge(ctx, metric, TABLE, value, where=(period, ""), rows=0, has_baseline=True, reason=reason)
    row = result_row(ctx, metric=metric, column=TABLE, period=period, segment="", value=value, light=light)
    row["period_date"] = start.isoformat()
    row["baseline_kind"], row["baseline_label"] = "cadence", f"{ctx.config.cadence} cadence"
    if value:
        row["reason"] = reason
    return row


def cadence_results(  # noqa: PLR0913 - one keyword per result field
    config: StabilityConfig,
    cal: Calendar,
    *,
    binning: Reference,
    dataset: str,
    as_of: date,
    stored: Iterable[Mapping[str, Any]] = (),
    run_id: str = "",
    computed_at: str = "",
) -> list[dict[str, Any]]:
    """Return the ``period_missing`` and ``period_late`` results of this run.

    A missing or late period is red (value 1). A stored non-green cadence result whose period has data now is
    rewritten green (value 0), so no stale red outlives the late arrival.
    """
    ctx = Context(config, binning, dataset, run_id, computed_at)
    missing, last = missing_starts(cal, config, as_of)
    out = []
    for start in missing:
        metric = PERIOD_LATE if start == last else PERIOD_MISSING
        due = period_end(start, cal.cadence) + timedelta(days=config.expected_by)
        reason = f"no rows for {cal.write(start)} (due {due})"
        out.append(_row(ctx, metric, start, cal, value=1.0, reason=reason))
    open_keys = {(r["period"], r["metric"]) for r in out}
    for r in stored:
        key = (str(r["period"]), str(r["metric"]))
        if r["metric"] in (PERIOD_MISSING, PERIOD_LATE) and r["status"] != "green" and key not in open_keys:
            start = date.fromisoformat(str(r["period_date"])[:10]) if r.get("period_date") else None
            if start is not None and start in cal.present:
                out.append(_row(ctx, str(r["metric"]), start, cal, value=0.0, reason=""))
    return out


def has_cadence(config: StabilityConfig) -> bool:
    """Tell whether the declaration expects a cadence."""
    return config.cadence != "none"


__all__ = [
    "Calendar",
    "build_calendar",
    "cadence_results",
    "expected_latest",
    "gap_between",
    "has_cadence",
    "is_due",
    "missing_starts",
    "parse_as_of",
    "period_end",
    "period_text_format",
    "settling_periods",
    "step",
    "today",
]
