"""Business-day calendar: how many business days a period holds, for volume metrics that scale with the days.

The calendar is the project's: weekend weekdays (Monday is 0) and a list of holidays. The library ships no holiday
list. A period is a day (``YYYY-MM-DD``, ``YYYYMMDD``) or a month (``YYYY-MM``, ``YYYYMM``).
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from bricksmith.monitor.errors import MonitorError

_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$|^(\d{4})(\d{2})(\d{2})$")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$|^(\d{4})(\d{2})$")
_WEEK = 7


@dataclass(frozen=True)
class BusinessCalendar:
    """Weekend weekdays (Monday is 0; default Saturday and Sunday) and holidays (ISO dates)."""

    weekend: tuple[int, ...] = (5, 6)
    holidays: frozenset[date] = frozenset()

    def __post_init__(self) -> None:
        """Validate the weekend days."""
        if any(not 0 <= d < _WEEK for d in self.weekend) or len(set(self.weekend)) >= _WEEK:
            msg = "calendar weekend takes weekday numbers 0 (Monday) to 6 (Sunday), and not all seven"
            raise MonitorError(msg)

    def is_business_day(self, day: date) -> bool:
        """Tell whether ``day`` is neither a weekend day nor a holiday."""
        return day.weekday() not in self.weekend and day not in self.holidays

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {"holidays": sorted(h.isoformat() for h in self.holidays), "weekend": sorted(self.weekend)}

    @classmethod
    def from_dict(cls, data: Any) -> BusinessCalendar:  # noqa: ANN401 - user configuration
        """Build a calendar from ``{weekend: [...], holidays: [...]}``, rejecting unknown keys."""
        if not isinstance(data, dict) or set(data) - {"weekend", "holidays"}:
            msg = f"calendar takes weekend and holidays; got {data!r}"
            raise MonitorError(msg)
        try:
            weekend = tuple(int(d) for d in data.get("weekend", (5, 6)))
            holidays = frozenset(date.fromisoformat(str(h)) for h in data.get("holidays", ()))
        except (TypeError, ValueError):
            msg = f"calendar weekend must be weekday numbers and holidays ISO dates, got {data!r}"
            raise MonitorError(msg) from None
        return cls(weekend=weekend, holidays=holidays)


def business_days(period: str, calendar_: BusinessCalendar) -> int | None:
    """Return the number of business days of a day or month period, or ``None`` when the text is neither."""
    try:
        found = _DAY.match(period)
        if found:
            groups = [g for g in found.groups() if g]
            return int(calendar_.is_business_day(date(int(groups[0]), int(groups[1]), int(groups[2]))))
        found = _MONTH.match(period)
        if found:
            groups = [g for g in found.groups() if g]
            year, month = int(groups[0]), int(groups[1])
            last = calendar.monthrange(year, month)[1]
            return sum(calendar_.is_business_day(date(year, month, d)) for d in range(1, last + 1))
    except ValueError:
        return None
    return None


__all__ = ["BusinessCalendar", "business_days"]
