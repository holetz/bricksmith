"""Alert persistence: replay an ordered series of statuses into opened, escalated and closed alerts.

A single bad period is noise; a bad state that persists is an alert. Suppressed periods (no data, no baseline,
too few rows, no rule) are skipped: they neither count towards nor break a run of statuses. Replays are pure
and deterministic, so alerts can be recomputed from stored statuses at any time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import GREEN, RED, YELLOW

if TYPE_CHECKING:
    from collections.abc import Sequence

WARNING = "warning"
CRITICAL = "critical"
OPEN = "open"
CLOSED = "closed"
SUPERSEDED = "superseded"


@dataclass(frozen=True)
class PersistencePolicy:
    """When an alert opens, escalates and closes; ``0`` disables a trigger.

    ``yellow``: consecutive yellow-or-worse periods that open a warning. ``red``: consecutive red periods that
    open (or escalate to) critical. ``immediate_red``: one red opens critical. ``close_after``: consecutive green
    periods that close an open alert. ``recurrence``: an alert opening within this many periods of the previous
    close is flagged as a recurrence.
    """

    yellow: int = 3
    red: int = 2
    close_after: int = 3
    immediate_red: bool = False
    recurrence: int = 12

    def __post_init__(self) -> None:
        """Reject negative counts and a policy that can never close."""
        if min(self.yellow, self.red, self.close_after, self.recurrence) < 0:
            msg = "persistence counts cannot be negative"
            raise MonitorError(msg)
        if self.close_after == 0:
            msg = "persistence close_after must be at least 1"
            raise MonitorError(msg)

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "close_after": self.close_after,
            "immediate_red": self.immediate_red,
            "recurrence": self.recurrence,
            "red": self.red,
            "yellow": self.yellow,
        }

    @classmethod
    def from_dict(cls, data: Any) -> PersistencePolicy:  # noqa: ANN401 - user configuration
        """Build a policy from a mapping, rejecting unknown keys."""
        if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
            msg = f"persistence takes yellow, red, close_after, immediate_red and recurrence; got {data!r}"
            raise MonitorError(msg)
        try:
            return cls(
                yellow=int(data.get("yellow", 3)),
                red=int(data.get("red", 2)),
                close_after=int(data.get("close_after", 3)),
                immediate_red=data.get("immediate_red", False) is True,
                recurrence=int(data.get("recurrence", 12)),
            )
        except (TypeError, ValueError):
            msg = f"persistence values must be integers, got {data!r}"
            raise MonitorError(msg) from None


@dataclass
class AlertSpan:
    """One alert: when it opened, escalated and closed, its severity and the periods that were not green."""

    severity: str
    opened: str
    escalated: str = ""
    closed: str = ""
    last_period: str = ""
    last_status: str = ""
    flagged: list[str] = field(default_factory=list)
    recurrence: bool = False

    @property
    def state(self) -> str:
        """Return ``open`` or ``closed``."""
        return CLOSED if self.closed else OPEN

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "closed": self.closed,
            "escalated": self.escalated,
            "flagged": list(self.flagged),
            "last_period": self.last_period,
            "last_status": self.last_status,
            "opened": self.opened,
            "recurrence": self.recurrence,
            "severity": self.severity,
            "state": self.state,
        }


def _trigger(policy: PersistencePolicy, status: str, yellows: int, reds: int) -> str:
    """Return the severity the current run of statuses calls for, or ``""``."""
    if status == RED and (policy.immediate_red or (policy.red and reds >= policy.red)):
        return CRITICAL
    if policy.yellow and yellows >= policy.yellow:
        return WARNING
    return ""


def alert_spans(
    series: Sequence[tuple[str, str]], policy: PersistencePolicy | None = None, *, carry: AlertSpan | None = None
) -> list[AlertSpan]:
    """Replay ``(period, status)`` pairs (sorted by period here) into alert spans, oldest first.

    ``carry`` is an alert already open before the series starts: the replay resumes it as the first span, so its
    statuses escalate and close it, and a span opened after its close can be flagged as a recurrence.
    """
    rule = policy or PersistencePolicy()
    spans: list[AlertSpan] = [carry] if carry is not None else []
    current: AlertSpan | None = carry
    yellows = reds = greens = 0
    index = 0
    last_close = -1
    for period, status in sorted(series):
        if status not in (GREEN, YELLOW, RED):
            continue
        index += 1
        yellows = yellows + 1 if status in (YELLOW, RED) else 0
        reds = reds + 1 if status == RED else 0
        greens = greens + 1 if status == GREEN else 0
        severity = _trigger(rule, status, yellows, reds)
        if current is None:
            if severity:
                recurrence = last_close >= 0 and rule.recurrence > 0 and index - last_close <= rule.recurrence
                current = AlertSpan(severity, period, recurrence=recurrence)
                spans.append(current)
        elif severity == CRITICAL and current.severity == WARNING:
            current.severity = CRITICAL
            current.escalated = period
        if current is None:
            continue
        current.last_period, current.last_status = period, status
        if status != GREEN:
            current.flagged.append(period)
        elif greens >= rule.close_after:
            current.closed = period
            current = None
            last_close = index
            yellows = reds = 0
    return spans


__all__ = [
    "CLOSED",
    "CRITICAL",
    "OPEN",
    "SUPERSEDED",
    "WARNING",
    "AlertSpan",
    "PersistencePolicy",
    "alert_spans",
]
