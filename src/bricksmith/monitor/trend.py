"""Trend check: whether the current value departs from the least-squares line through its recent history.

The line is fitted on the last ``window`` prior values (x = 0..k-1) by ordinary least squares and extrapolated
to x = k. The current residual is standardized by the fit's residual standard deviation
``sqrt(SSE / (k - 2))``; only the side the direction calls worse counts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import BOTH, DIRECTIONS, GREEN, HIGHER, LOWER, RED, YELLOW

if TYPE_CHECKING:
    from collections.abc import Sequence

INSUFFICIENT_HISTORY = "insufficient_history"
MIN_FIT_POINTS = 3


@dataclass(frozen=True)
class TrendPolicy:
    """How a trend is checked: history window, minimum points and the yellow/red standardized deviations."""

    window: int = 12
    min_points: int = 6
    yellow: float = 2.0
    red: float = 3.0

    def __post_init__(self) -> None:
        """Validate sizes and thresholds."""
        if self.min_points < MIN_FIT_POINTS or self.window < self.min_points:
            msg = f"trend needs min_points >= {MIN_FIT_POINTS} and window >= min_points"
            raise MonitorError(msg)
        if not 0 < self.yellow <= self.red:
            msg = "trend thresholds need 0 < yellow <= red"
            raise MonitorError(msg)

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {"min_points": self.min_points, "red": self.red, "window": self.window, "yellow": self.yellow}

    @classmethod
    def from_dict(cls, data: Any) -> TrendPolicy:  # noqa: ANN401 - user configuration
        """Build a policy from a mapping, rejecting unknown keys."""
        if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
            msg = f"trend takes window, min_points, yellow and red; got {data!r}"
            raise MonitorError(msg)
        try:
            return cls(
                window=int(data.get("window", 12)),
                min_points=int(data.get("min_points", 6)),
                yellow=float(data.get("yellow", 2.0)),
                red=float(data.get("red", 3.0)),
            )
        except (TypeError, ValueError):
            msg = f"trend values must be numbers, got {data!r}"
            raise MonitorError(msg) from None


@dataclass(frozen=True)
class Trend:
    """The outcome: a color or ``insufficient_history``, the expected value, the standardized deviation, the slope."""

    status: str
    expected: float | None = None
    deviation: float | None = None
    slope: float | None = None
    points: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "deviation": self.deviation,
            "expected": self.expected,
            "points": self.points,
            "slope": self.slope,
            "status": self.status,
        }


def _fit(ys: Sequence[float]) -> tuple[float, float, float]:
    """Return ``(slope, intercept, residual sigma)`` of the OLS line through ``(i, ys[i])``."""
    k = len(ys)
    x_mean = (k - 1) / 2
    y_mean = sum(ys) / k
    sxx = sum((i - x_mean) ** 2 for i in range(k))
    sxy = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(ys))
    slope = sxy / sxx
    intercept = y_mean - slope * x_mean
    sse = sum((y - (intercept + slope * i)) ** 2 for i, y in enumerate(ys))
    return slope, intercept, math.sqrt(sse / (k - 2))


def trend_check(
    history: Sequence[float], current: float, policy: TrendPolicy | None = None, direction: str = HIGHER
) -> Trend:
    """Check ``current`` against the OLS extrapolation of the last ``policy.window`` values of ``history``."""
    rule = policy or TrendPolicy()
    if direction not in DIRECTIONS:
        msg = f"trend direction must be one of {', '.join(DIRECTIONS)}"
        raise MonitorError(msg)
    points = [float(v) for v in history if v is not None and math.isfinite(v)][-rule.window :]
    if len(points) < rule.min_points or not math.isfinite(current):
        return Trend(INSUFFICIENT_HISTORY, points=len(points))
    slope, intercept, sigma = _fit(points)
    expected = intercept + slope * len(points)
    residual = current - expected
    tolerance = 1e-12 * max(1.0, abs(expected))
    if sigma <= tolerance:
        z = 0.0 if abs(residual) <= tolerance else math.copysign(math.inf, residual)
    else:
        z = residual / sigma
    judged = {HIGHER: z, LOWER: -z, BOTH: abs(z)}[direction]
    status = GREEN if judged <= rule.yellow else YELLOW if judged <= rule.red else RED
    return Trend(status, expected, z, slope, len(points))


__all__ = ["INSUFFICIENT_HISTORY", "Trend", "TrendPolicy", "trend_check"]
