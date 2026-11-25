"""Trend check: whether the current value departs from the least-squares line through its recent history.

The line is fitted on the last ``window`` prior values (x = 0..k-1) by ordinary least squares and extrapolated
to x = k. The current residual is standardized by the prediction standard error
``s * sqrt(1 + 1/k + (k - x_mean)^2 / Sxx)`` with ``s = sqrt(SSE / (k - 2))`` and judged against Student-t
quantiles at ``k - 2`` degrees of freedom; only the side the direction calls worse counts. ``yellow`` and ``red``
are sigma-equivalents: each maps to the normal tail probability it implies, and the critical value is the
Student-t quantile of that tail. A residual within the practical-significance floor is never yellow or red.

``fit`` chooses the line: ``ols`` (the default), ``theil_sen`` (median of the pairwise slopes, resistant to outliers
in the history) or ``huber`` (iteratively reweighted least squares with Huber weights). ``scale`` chooses the residual
scale: ``std`` (residual standard deviation, ``k - 2`` degrees of freedom) or ``mad`` (1.4826 times the median
absolute deviation of the residuals). ``exclude`` says which judged values stay out of the history the caller keeps:
:func:`keeps` is the one rule.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist, median
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import BOTH, DIRECTIONS, GREEN, HIGHER, LOWER, RED, YELLOW

if TYPE_CHECKING:
    from collections.abc import Sequence

INSUFFICIENT_HISTORY = "insufficient_history"
MIN_FIT_POINTS = 3
TRANSFORMS = ("none", "log")
FITS = ("ols", "theil_sen", "huber")
SCALES = ("std", "mad")
EXCLUDES = ("red", "flagged", "none")
_MAD_FACTOR = 1.4826
_HUBER_C = 1.345
_HUBER_ITERATIONS = 100
_NORMAL = NormalDist()
_LARGE_DF = 100_000
_BETA_ITERATIONS = 300
_TINY = 1e-300
_BETA_EPS = 1e-15


def _beta_cf(a: float, b: float, x: float) -> float:
    """Evaluate the continued fraction of the incomplete beta function (modified Lentz)."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > _TINY else _TINY)
    h = d
    for m in range(1, _BETA_ITERATIONS):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 / _nonzero(1.0 + aa * d)
        c = _nonzero(1.0 + aa / c)
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 / _nonzero(1.0 + aa * d)
        c = _nonzero(1.0 + aa / c)
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _BETA_EPS:
            break
    return h


def _nonzero(value: float) -> float:
    return value if abs(value) > _TINY else _TINY


def _beta_inc(a: float, b: float, x: float) -> float:
    """Return the regularized incomplete beta function ``I_x(a, b)``."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_cf(a, b, x) / a
    return 1.0 - front * _beta_cf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    """Return the Student-t cumulative distribution function at ``t`` with ``df`` degrees of freedom."""
    if df >= _LARGE_DF:
        return _NORMAL.cdf(t)
    tail = 0.5 * _beta_inc(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t > 0 else tail


def t_ppf(p: float, df: float) -> float:
    """Return the Student-t quantile of probability ``p`` (0 < p < 1) with ``df`` degrees of freedom."""
    if not 0.0 < p < 1.0:
        msg = "t_ppf needs 0 < p < 1"
        raise MonitorError(msg)
    if p == 0.5:  # noqa: PLR2004 - the median
        return 0.0
    if p < 0.5:  # noqa: PLR2004 - symmetry
        return -t_ppf(1.0 - p, df)
    if df >= _LARGE_DF:
        return _NORMAL.inv_cdf(p)
    low, high = 0.0, max(1.0, _NORMAL.inv_cdf(p))
    while t_cdf(high, df) < p:
        high *= 2.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if t_cdf(mid, df) < p:
            low = mid
        else:
            high = mid
        if high - low <= 1e-13 * max(1.0, high):
            break
    return (low + high) / 2.0


def critical_value(sigmas: float, df: float) -> float:
    """Return the Student-t critical value whose one-tail probability equals the normal tail of ``sigmas``."""
    return t_ppf(_NORMAL.cdf(sigmas), df)


@dataclass(frozen=True)
class TrendPolicy:
    """How a trend is checked: history window, minimum points, yellow/red sigma-equivalents, floors and transform."""

    window: int = 12
    min_points: int = 6
    yellow: float = 2.0
    red: float = 3.0
    min_abs_change: float = 0.0
    min_rel_change: float = 0.01
    transform: str = "none"
    fit: str = "ols"
    scale: str = "std"
    exclude: str = "red"

    def __post_init__(self) -> None:
        """Validate sizes and thresholds."""
        if self.min_points < MIN_FIT_POINTS or self.window < self.min_points:
            msg = f"trend needs min_points >= {MIN_FIT_POINTS} and window >= min_points"
            raise MonitorError(msg)
        if not 0 < self.yellow <= self.red:
            msg = "trend thresholds need 0 < yellow <= red"
            raise MonitorError(msg)
        if self.min_abs_change < 0 or self.min_rel_change < 0:
            msg = "trend floors min_abs_change and min_rel_change cannot be negative"
            raise MonitorError(msg)
        if self.transform not in TRANSFORMS:
            msg = f"trend transform must be one of {', '.join(TRANSFORMS)}"
            raise MonitorError(msg)
        for name, value, allowed in (
            ("fit", self.fit, FITS),
            ("scale", self.scale, SCALES),
            ("exclude", self.exclude, EXCLUDES),
        ):
            if value not in allowed:
                msg = f"trend {name} must be one of {', '.join(allowed)}"
                raise MonitorError(msg)

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "exclude": self.exclude,
            "fit": self.fit,
            "min_abs_change": self.min_abs_change,
            "min_points": self.min_points,
            "min_rel_change": self.min_rel_change,
            "red": self.red,
            "scale": self.scale,
            "transform": self.transform,
            "window": self.window,
            "yellow": self.yellow,
        }

    @classmethod
    def from_dict(cls, data: Any) -> TrendPolicy:  # noqa: ANN401 - user configuration
        """Build a policy from a mapping, rejecting unknown keys."""
        if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
            msg = (
                "trend takes window, min_points, yellow, red, min_abs_change, min_rel_change, transform, "
                f"fit, scale and exclude; got {data!r}"
            )
            raise MonitorError(msg)
        try:
            return cls(
                window=int(data.get("window", 12)),
                min_points=int(data.get("min_points", 6)),
                yellow=float(data.get("yellow", 2.0)),
                red=float(data.get("red", 3.0)),
                min_abs_change=float(data.get("min_abs_change", 0.0)),
                min_rel_change=float(data.get("min_rel_change", 0.01)),
                transform=str(data.get("transform", "none")),
                fit=str(data.get("fit", "ols")),
                scale=str(data.get("scale", "std")),
                exclude=str(data.get("exclude", "red")),
            )
        except (TypeError, ValueError):
            msg = f"trend values must be numbers, got {data!r}"
            raise MonitorError(msg) from None


@dataclass(frozen=True)
class Trend:
    """The outcome: a color or ``insufficient_history``, the expected value, the standardized deviation, the slope.

    ``sigma`` is the prediction standard error (in the fitted scale); ``lower`` and ``upper`` bound the red-level
    prediction interval in the original scale.
    """

    status: str
    expected: float | None = None
    deviation: float | None = None
    slope: float | None = None
    points: int = 0
    sigma: float | None = None
    lower: float | None = None
    upper: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "deviation": self.deviation,
            "expected": self.expected,
            "lower": self.lower,
            "points": self.points,
            "sigma": self.sigma,
            "slope": self.slope,
            "status": self.status,
            "upper": self.upper,
        }


def _mad(values: Sequence[float]) -> float:
    """Return the consistent median absolute deviation (a standard-deviation estimate)."""
    centre = median(values)
    return _MAD_FACTOR * median([abs(v - centre) for v in values])


def _theil_sen(ys: Sequence[float]) -> tuple[float, float]:
    """Return ``(slope, intercept)``: the median pairwise slope and the median intercept through it."""
    k = len(ys)
    slope = median([(ys[j] - ys[i]) / (j - i) for i in range(k) for j in range(i + 1, k)])
    return slope, median([y - slope * i for i, y in enumerate(ys)])


def _weighted_line(ys: Sequence[float], weights: Sequence[float]) -> tuple[float, float]:
    total = sum(weights)
    x_mean = sum(w * i for i, w in enumerate(weights)) / total
    y_mean = sum(w * y for w, y in zip(weights, ys, strict=True)) / total
    sxx = sum(w * (i - x_mean) ** 2 for i, w in enumerate(weights))
    sxy = sum(w * (i - x_mean) * (y - y_mean) for i, (w, y) in enumerate(zip(weights, ys, strict=True)))
    slope = sxy / sxx
    return slope, y_mean - slope * x_mean


def _huber(ys: Sequence[float]) -> tuple[float, float]:
    """Return ``(slope, intercept)`` by Huber-weighted least squares, started at the Theil-Sen line."""
    slope, intercept = _theil_sen(ys)
    for _ in range(_HUBER_ITERATIONS):
        residuals = [y - (intercept + slope * i) for i, y in enumerate(ys)]
        scale = _mad(residuals)
        if scale <= 1e-12 * max(1.0, *(abs(y) for y in ys)):
            break
        bound = _HUBER_C * scale
        weights = [1.0 if abs(r) <= bound else bound / abs(r) for r in residuals]
        new_slope, new_intercept = _weighted_line(ys, weights)
        done = abs(new_slope - slope) <= 1e-12 * max(1.0, abs(slope))
        slope, intercept = new_slope, new_intercept
        if done:
            break
    return slope, intercept


def _fit(ys: Sequence[float], fit: str = "ols", scale: str = "std") -> tuple[float, float, float, float, float]:
    """Return ``(slope, intercept, residual sigma, x_mean, Sxx)`` of the line through ``(i, ys[i])``."""
    k = len(ys)
    x_mean = (k - 1) / 2
    sxx = sum((i - x_mean) ** 2 for i in range(k))
    if fit == "theil_sen":
        slope, intercept = _theil_sen(ys)
    elif fit == "huber":
        slope, intercept = _huber(ys)
    else:
        y_mean = sum(ys) / k
        sxy = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(ys))
        slope = sxy / sxx
        intercept = y_mean - slope * x_mean
    residuals = [y - (intercept + slope * i) for i, y in enumerate(ys)]
    sigma = _mad(residuals) if scale == "mad" else math.sqrt(sum(r * r for r in residuals) / (k - 2))
    return slope, intercept, sigma, x_mean, sxx


def keeps(policy: TrendPolicy, light: str, trend: str) -> bool:
    """Tell whether a judged value joins the history: ``red`` drops red lights or trends, ``flagged`` also yellow."""
    if policy.exclude == "none":
        return True
    bad = {RED} if policy.exclude == "red" else {RED, YELLOW}
    return light not in bad and trend not in bad


def _forward(value: float, transform: str) -> float:
    return math.log1p(value) if transform == "log" else value


def _back(value: float, transform: str) -> float:
    if transform != "log":
        return value
    try:
        return math.expm1(value)
    except OverflowError:
        return math.inf


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
    if rule.transform == "log" and (current <= -1.0 or min(points) <= -1.0):
        return Trend(INSUFFICIENT_HISTORY, points=len(points))
    ys = [_forward(v, rule.transform) for v in points]
    k = len(ys)
    slope, intercept, s, x_mean, sxx = _fit(ys, rule.fit, rule.scale)
    fitted = intercept + slope * k
    expected = _back(fitted, rule.transform)
    leverage = math.sqrt(1.0 + 1.0 / k + (k - x_mean) ** 2 / sxx)
    se = s * leverage
    residual_fit = _forward(current, rule.transform) - fitted
    tolerance = 1e-12 * max(1.0, abs(fitted))
    flat = se <= tolerance
    z = 0.0 if flat and abs(residual_fit) <= tolerance else None if flat else residual_fit / se
    df = k - 2
    half = critical_value(rule.red, df) * se
    lower, upper = _back(fitted - half, rule.transform), _back(fitted + half, rule.transform)
    residual = current - expected
    floor = max(rule.min_abs_change, rule.min_rel_change * abs(expected))
    if abs(residual) <= max(floor, 1e-12 * max(1.0, abs(expected))):
        status = GREEN
    else:
        signed = residual_fit if z is None else z
        judged = {HIGHER: signed, LOWER: -signed, BOTH: abs(signed)}[direction]
        if z is None:
            status = RED if judged > 0 else GREEN
        else:
            status = (
                GREEN
                if judged <= critical_value(rule.yellow, df)
                else YELLOW
                if judged <= critical_value(rule.red, df)
                else RED
            )
    return Trend(status, expected, z, slope, k, se, lower, upper)


__all__ = ["INSUFFICIENT_HISTORY", "Trend", "TrendPolicy", "critical_value", "keeps", "t_cdf", "t_ppf", "trend_check"]
