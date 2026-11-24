"""Traffic-light limits: green, yellow and red bands plus explicit states for values that cannot be judged.

Shared by data stability and model monitoring. ``classify`` never invents a color: a missing value, too few
rows, a missing baseline, a missing rule or an informational segment each have their own suppressed state, ranked
below green.

A limit has a kind that decides which number its bands judge: ``absolute`` judges the value itself,
``relative`` the relative change ``(value - baseline) / |baseline|`` against a named reference, and
``control`` the standardized distance ``(value - mean) / sd`` from a baseline of period values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from bricksmith.monitor.errors import MonitorError

GREEN = "green"
YELLOW = "yellow"
RED = "red"
NO_DATA = "no_data"
INSUFFICIENT_VOLUME = "insufficient_volume"
NO_BASELINE = "no_baseline"
NO_RULE = "no_rule"
INFORMATIONAL = "informational"
COLORS = (GREEN, YELLOW, RED)
SUPPRESSED = (NO_DATA, INSUFFICIENT_VOLUME, NO_BASELINE, NO_RULE, INFORMATIONAL)
RANK: dict[str, int] = {GREEN: 0, YELLOW: 1, RED: 2}
HIGHER = "higher"
LOWER = "lower"
BOTH = "both"
DIRECTIONS = (HIGHER, LOWER, BOTH)
ABSOLUTE = "absolute"
RELATIVE = "relative"
CONTROL = "control"
KINDS = (ABSOLUTE, RELATIVE, CONTROL)


def rank(status: str) -> int:
    """Return ``0`` (green), ``1`` (yellow), ``2`` (red) or ``-1`` for a suppressed state."""
    return RANK.get(status, -1)


def worst(statuses: list[str] | tuple[str, ...]) -> str:
    """Return the worst color among ``statuses`` (suppressed states are ignored); ``""`` when none is a color."""
    colored = [s for s in statuses if s in RANK]
    return max(colored, key=rank) if colored else ""


@dataclass(frozen=True)
class Limits:
    """Two boundaries and the direction in which a value gets worse.

    ``higher``: green when ``value <= green``, yellow when ``value <= red``, red otherwise (``green <= red``).
    ``lower``: green when ``value >= green``, yellow when ``value >= red``, red otherwise (``green >= red``).
    ``both``: the distance ``|value - center|`` is judged like ``higher`` (``0 <= green <= red``).

    ``kind`` decides what is judged (see :func:`compared_value`); ``reference`` names the baseline a ``relative``
    limit compares with (its meaning belongs to the monitor that reads it).
    """

    green: float
    red: float
    direction: str = HIGHER
    center: float = 0.0
    kind: str = ABSOLUTE
    reference: str = ""

    def __post_init__(self) -> None:
        """Reject an unknown direction or kind, a misplaced reference, or boundaries in the wrong order."""
        if self.direction not in DIRECTIONS:
            msg = f"limits direction must be one of {', '.join(DIRECTIONS)}, got {self.direction!r}"
            raise MonitorError(msg)
        if self.kind not in KINDS:
            msg = f"limits kind must be one of {', '.join(KINDS)}, got {self.kind!r}"
            raise MonitorError(msg)
        if self.reference and self.kind != RELATIVE:
            msg = f"limits reference applies to kind {RELATIVE!r} only, got kind {self.kind!r}"
            raise MonitorError(msg)
        if any(math.isnan(v) for v in (self.green, self.red, self.center)):
            msg = "limits cannot be NaN"
            raise MonitorError(msg)
        ordered = {
            HIGHER: self.green <= self.red,
            LOWER: self.green >= self.red,
            BOTH: 0 <= self.green <= self.red,
        }[self.direction]
        if not ordered:
            msg = f"limits for direction {self.direction!r} are out of order: green={self.green}, red={self.red}"
            raise MonitorError(msg)

    def band(self, value: float) -> str:
        """Return the color of a finite or infinite ``value``."""
        if self.direction == LOWER:
            return GREEN if value >= self.green else YELLOW if value >= self.red else RED
        judged = abs(value - self.center) if self.direction == BOTH else value
        return GREEN if judged <= self.green else YELLOW if judged <= self.red else RED

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form; ``kind`` and ``reference`` appear only when they are not the default."""
        data: dict[str, Any] = {
            "center": self.center,
            "direction": self.direction,
            "green": self.green,
            "red": self.red,
        }
        if self.kind != ABSOLUTE:
            data["kind"] = self.kind
        if self.reference:
            data["reference"] = self.reference
        return data

    @classmethod
    def from_dict(cls, data: Any) -> Limits:  # noqa: ANN401 - user configuration
        """Build limits from ``{green, red, direction?, center?, kind?, reference?}``, rejecting unknown keys."""
        if not isinstance(data, dict):
            msg = f"limits must be a mapping with green and red, got {data!r}"
            raise MonitorError(msg)
        unknown = set(data) - {"green", "red", "direction", "center", "kind", "reference"}
        if unknown or "green" not in data or "red" not in data:
            msg = (
                "limits need green and red (and optionally direction, center, kind, reference); "
                f"got keys {sorted(data)}"
            )
            raise MonitorError(msg)
        try:
            return cls(
                green=float(data["green"]),
                red=float(data["red"]),
                direction=str(data.get("direction", HIGHER)),
                center=float(data.get("center", 0.0)),
                kind=str(data.get("kind", ABSOLUTE)),
                reference=str(data.get("reference") or ""),
            )
        except (TypeError, ValueError):
            msg = f"limits green, red and center must be numbers, got {data!r}"
            raise MonitorError(msg) from None


@dataclass(frozen=True)
class Light:
    """The judged state of one value: a color or a suppressed state, with its rank and an optional reason."""

    status: str
    reason: str = ""

    @property
    def rank(self) -> int:
        """Return the numeric rank (``-1`` when suppressed)."""
        return rank(self.status)

    @property
    def suppressed(self) -> bool:
        """Tell whether the value could not be judged."""
        return self.status not in RANK


def classify(  # noqa: PLR0913 - one keyword per suppression condition
    value: float | None,
    limits: Limits | None,
    *,
    count: int | None = None,
    min_count: int = 0,
    has_baseline: bool = True,
    reason: str = "",
) -> Light:
    """Judge ``value``: ``no_data``, ``insufficient_volume``, ``no_baseline`` and ``no_rule`` win, in that order."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return Light(NO_DATA, reason or "no value")
    if count is not None and count < min_count:
        return Light(INSUFFICIENT_VOLUME, f"{count} < min {min_count}")
    if not has_baseline:
        return Light(NO_BASELINE, reason or "no baseline")
    if limits is None:
        return Light(NO_RULE)
    return Light(limits.band(value))


def compared_value(
    limits: Limits | None, value: float | None, *, baseline: float | None = None, spread: float | None = None
) -> float | None:
    """Return the number the bands of ``limits`` judge, or ``None`` when its baseline is missing.

    ``absolute`` (or no limits): ``value``. ``relative``: ``(value - baseline) / |baseline|``; a missing or zero
    baseline gives ``None``. ``control``: ``(value - baseline) / spread`` with ``baseline`` the mean and ``spread``
    the standard deviation of the baseline values; a zero spread gives ``0`` on the mean and an infinite distance
    elsewhere; a missing mean or spread gives ``None``.
    """
    if value is None or limits is None or limits.kind == ABSOLUTE:
        return value
    if baseline is None or not math.isfinite(baseline):
        return None
    if limits.kind == RELATIVE:
        return None if baseline == 0 else (value - baseline) / abs(baseline)
    if spread is None or not math.isfinite(spread):
        return None
    residual = value - baseline
    tolerance = 1e-12 * max(1.0, abs(baseline))
    if spread <= tolerance:
        return 0.0 if abs(residual) <= tolerance else math.copysign(math.inf, residual)
    return residual / spread


def judge(  # noqa: PLR0913 - one keyword per suppression condition and baseline input
    value: float | None,
    limits: Limits | None,
    *,
    baseline: float | None = None,
    spread: float | None = None,
    count: int | None = None,
    min_count: int = 0,
    reason: str = "",
) -> tuple[Light, float | None]:
    """Classify ``value`` through its limit kind and return the light and the compared value.

    A value whose relative or control baseline is missing is ``no_baseline`` (after ``no_data`` and
    ``insufficient_volume``, as :func:`classify` orders them).
    """
    compared = compared_value(limits, value, baseline=baseline, spread=spread)
    if value is not None and compared is None:
        light = classify(value, limits, count=count, min_count=min_count, has_baseline=False, reason=reason)
        return light, None
    return classify(compared, limits, count=count, min_count=min_count, reason=reason), compared


__all__ = [
    "ABSOLUTE",
    "BOTH",
    "COLORS",
    "CONTROL",
    "GREEN",
    "HIGHER",
    "INFORMATIONAL",
    "INSUFFICIENT_VOLUME",
    "KINDS",
    "LOWER",
    "NO_BASELINE",
    "NO_DATA",
    "NO_RULE",
    "RED",
    "RELATIVE",
    "SUPPRESSED",
    "YELLOW",
    "Light",
    "Limits",
    "classify",
    "compared_value",
    "judge",
    "rank",
    "worst",
]
