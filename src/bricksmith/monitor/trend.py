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

``season`` (an integer ``>= 2``) models a seasonal pattern: the line and additive seasonal effects (the mean of the
detrended values of each phase; the median for a robust fit) are fitted together on the history and the expectation
is the line plus the effect of the phase being judged. It needs two observations in every phase; with fewer the plain
line is used and ``Trend.model`` says ``line:insufficient_history``. A history that keeps its phase holds ``NaN``
for a period left out (:func:`remember`). ``per``, ``calendar``, ``drift`` and ``change_point`` are read by the
stability monitor (business-day normalization of ``row_count``, sustained-shift detectors over the deviations).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist, mean, median
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.business import BusinessCalendar
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import BOTH, DIRECTIONS, GREEN, HIGHER, LOWER, RED, YELLOW
from bricksmith.monitor.shifts import DRIFTS

if TYPE_CHECKING:
    from collections.abc import Sequence

INSUFFICIENT_HISTORY = "insufficient_history"
SEASON_FALLBACK = "line:insufficient_history"
MIN_FIT_POINTS = 3
TRANSFORMS = ("none", "log")
FITS = ("ols", "theil_sen", "huber")
SCALES = ("std", "mad")
EXCLUDES = ("red", "flagged", "none")
PERS = ("period", "business_day")
MIN_SEASON = 2
_SEASON_ROUNDS = 5
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
    season: int = 0
    per: str = "period"
    calendar: BusinessCalendar | None = None
    drift: str = "none"
    change_point: bool = False

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
        if self.season and self.season < MIN_SEASON:
            msg = f"trend season must be 0 (off) or at least {MIN_SEASON}"
            raise MonitorError(msg)
        if self.per not in PERS or self.drift not in DRIFTS:
            msg = f"trend per must be one of {', '.join(PERS)} and drift one of {', '.join(DRIFTS)}"
            raise MonitorError(msg)

    @property
    def span(self) -> int:
        """Return how many periods of history the check reads: ``window``, and two cycles at least with a season."""
        return max(self.window, 2 * self.season)

    @property
    def business_calendar(self) -> BusinessCalendar:
        """Return the declared calendar, or Saturday-Sunday weekends without holidays."""
        return self.calendar or BusinessCalendar()

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form; an option at its default is left out, so a declaration keeps its hash."""
        data: dict[str, Any] = {
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
        if self.season:
            data["season"] = self.season
        if self.per != "period":
            data["per"] = self.per
            data["calendar"] = self.business_calendar.to_dict()
        if self.drift != "none":
            data["drift"] = self.drift
        if self.change_point:
            data["change_point"] = True
        return data

    @classmethod
    def from_dict(cls, data: Any) -> TrendPolicy:  # noqa: ANN401 - user configuration
        """Build a policy from a mapping, rejecting unknown keys."""
        if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
            msg = (
                "trend takes window, min_points, yellow, red, min_abs_change, min_rel_change, transform, "
                f"fit, scale, exclude, season, per, calendar, drift and change_point; got {data!r}"
            )
            raise MonitorError(msg)
        calendar = data.get("calendar")
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
                season=int(data.get("season", 0)),
                per=str(data.get("per", "period")),
                calendar=None if calendar is None else BusinessCalendar.from_dict(calendar),
                drift=str(data.get("drift", "none")),
                change_point=bool(data.get("change_point", False)),
            )
        except (TypeError, ValueError):
            msg = f"trend values must be numbers, got {data!r}"
            raise MonitorError(msg) from None


@dataclass(frozen=True)
class Trend:
    """The outcome: a color or ``insufficient_history``, the expected value, the standardized deviation, the slope.

    ``sigma`` is the prediction standard error (in the fitted scale); ``lower`` and ``upper`` bound the red-level
    prediction interval in the original scale. ``model`` names what was fitted: ``line``, ``seasonal`` or
    ``line:insufficient_history`` (a season was declared but the history has fewer than two cycles).
    """

    status: str
    expected: float | None = None
    deviation: float | None = None
    slope: float | None = None
    points: int = 0
    sigma: float | None = None
    lower: float | None = None
    upper: float | None = None
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "deviation": self.deviation,
            "expected": self.expected,
            "lower": self.lower,
            "model": self.model,
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


def _theil_sen(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    """Return ``(slope, intercept)``: the median pairwise slope and the median intercept through it."""
    k = len(ys)
    slope = median([(ys[j] - ys[i]) / (xs[j] - xs[i]) for i in range(k) for j in range(i + 1, k)])
    return slope, median([y - slope * x for x, y in zip(xs, ys, strict=True)])


def _weighted_line(xs: Sequence[float], ys: Sequence[float], weights: Sequence[float]) -> tuple[float, float]:
    total = sum(weights)
    x_mean = sum(w * x for w, x in zip(weights, xs, strict=True)) / total
    y_mean = sum(w * y for w, y in zip(weights, ys, strict=True)) / total
    sxx = sum(w * (x - x_mean) ** 2 for w, x in zip(weights, xs, strict=True))
    sxy = sum(w * (x - x_mean) * (y - y_mean) for w, x, y in zip(weights, xs, ys, strict=True))
    slope = sxy / sxx
    return slope, y_mean - slope * x_mean


def _huber(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    """Return ``(slope, intercept)`` by Huber-weighted least squares, started at the Theil-Sen line."""
    slope, intercept = _theil_sen(xs, ys)
    for _ in range(_HUBER_ITERATIONS):
        residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys, strict=True)]
        scale = _mad(residuals)
        if scale <= 1e-12 * max(1.0, *(abs(y) for y in ys)):
            break
        bound = _HUBER_C * scale
        weights = [1.0 if abs(r) <= bound else bound / abs(r) for r in residuals]
        new_slope, new_intercept = _weighted_line(xs, ys, weights)
        done = abs(new_slope - slope) <= 1e-12 * max(1.0, abs(slope))
        slope, intercept = new_slope, new_intercept
        if done:
            break
    return slope, intercept


def _line(xs: Sequence[float], ys: Sequence[float], fit: str) -> tuple[float, float]:
    """Return ``(slope, intercept)`` of the line through ``(xs, ys)`` by the chosen fit."""
    if fit == "theil_sen":
        return _theil_sen(xs, ys)
    if fit == "huber":
        return _huber(xs, ys)
    x_mean, y_mean = sum(xs) / len(xs), sum(ys) / len(ys)
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True)) / sum((x - x_mean) ** 2 for x in xs)
    return slope, y_mean - slope * x_mean


def _fit(ys: Sequence[float], fit: str = "ols", scale: str = "std") -> tuple[float, float, float, float, float]:
    """Return ``(slope, intercept, residual sigma, x_mean, Sxx)`` of the line through ``(i, ys[i])``."""
    k = len(ys)
    xs = list(range(k))
    x_mean = (k - 1) / 2
    sxx = sum((i - x_mean) ** 2 for i in range(k))
    slope, intercept = _line(xs, ys, fit)
    residuals = [y - (intercept + slope * i) for i, y in enumerate(ys)]
    sigma = _mad(residuals) if scale == "mad" else math.sqrt(sum(r * r for r in residuals) / (k - 2))
    return slope, intercept, sigma, x_mean, sxx


@dataclass(frozen=True)
class _Seasonal:
    slope: float
    intercept: float
    sigma: float
    x_mean: float
    sxx: float
    df: int
    effects: tuple[float, ...]
    counts: tuple[int, ...]


def _seasonal_fit(xs: Sequence[int], ys: Sequence[float], season: int, fit: str, scale: str) -> _Seasonal | None:
    """Fit a line plus additive seasonal effects by backfitting, or ``None`` below two cycles of observations."""
    m = len(ys)
    counts = [sum(1 for x in xs if x % season == j) for j in range(season)]
    df = m - 2 - (season - 1)
    if min(counts) < MIN_SEASON or df < 1:
        return None
    centre = mean if fit == "ols" else median
    effects = [0.0] * season
    for _ in range(_SEASON_ROUNDS):
        adjusted = [y - effects[x % season] for x, y in zip(xs, ys, strict=True)]
        slope, intercept = _line(xs, adjusted, fit)
        phases: list[list[float]] = [[] for _ in range(season)]
        for x, y in zip(xs, ys, strict=True):
            phases[x % season].append(y - (intercept + slope * x))
        raw = [centre(p) for p in phases]
        mid = sum(raw) / season
        effects = [e - mid for e in raw]
    adjusted = [y - effects[x % season] for x, y in zip(xs, ys, strict=True)]
    slope, intercept = _line(xs, adjusted, fit)
    residuals = [y - (intercept + slope * x + effects[x % season]) for x, y in zip(xs, ys, strict=True)]
    sigma = _mad(residuals) if scale == "mad" else math.sqrt(sum(r * r for r in residuals) / df)
    x_mean = sum(xs) / m
    sxx = sum((x - x_mean) ** 2 for x in xs)
    return _Seasonal(slope, intercept, sigma, x_mean, sxx, df, tuple(effects), tuple(counts))


def keeps(policy: TrendPolicy, light: str, trend: str, model: str = "") -> bool:
    """Tell whether a judged value joins the history: ``red`` drops red lights or trends, ``flagged`` also yellow.

    A trend judged by the plain line because the history held fewer than two seasonal cycles
    (``line:insufficient_history``) is not evidence of an anomaly: the season it ignores explains the peak. Only the
    light then counts.
    """
    if policy.exclude == "none":
        return True
    bad = {RED} if policy.exclude == "red" else {RED, YELLOW}
    return light not in bad and (model.startswith(SEASON_FALLBACK) or trend not in bad)


def remember(history: list[float], value: float, *, kept: bool, policy: TrendPolicy) -> None:
    """Append a judged value to ``history``; a value left out leaves ``NaN`` when a season needs the phase."""
    if kept:
        history.append(value)
    elif policy.season:
        history.append(math.nan)


def _forward(value: float, transform: str) -> float:
    return math.log1p(value) if transform == "log" else value


def _back(value: float, transform: str) -> float:
    if transform != "log":
        return value
    try:
        return math.expm1(value)
    except OverflowError:
        return math.inf


def _status(  # noqa: PLR0913, PLR0917 - the pieces of one judgment
    rule: TrendPolicy, current: float, expected: float, residual_fit: float, z: float | None, df: int, direction: str
) -> str:
    """Return the color of a residual: green inside the practical floor, else by the Student-t critical values."""
    residual = current - expected
    floor = max(rule.min_abs_change, rule.min_rel_change * abs(expected))
    if abs(residual) <= max(floor, 1e-12 * max(1.0, abs(expected))):
        return GREEN
    signed = residual_fit if z is None else z
    judged = {HIGHER: signed, LOWER: -signed, BOTH: abs(signed)}[direction]
    if z is None:
        return RED if judged > 0 else GREEN
    if judged <= critical_value(rule.yellow, df):
        return GREEN
    return YELLOW if judged <= critical_value(rule.red, df) else RED


def trend_check(
    history: Sequence[float], current: float, policy: TrendPolicy | None = None, direction: str = HIGHER
) -> Trend:
    """Check ``current`` against the extrapolation of the last ``policy.span`` values of ``history``.

    With ``policy.season`` the history keeps its positions (``NaN`` marks a period left out) and the expectation adds
    the seasonal effect of the phase being judged.
    """
    rule = policy or TrendPolicy()
    if direction not in DIRECTIONS:
        msg = f"trend direction must be one of {', '.join(DIRECTIONS)}"
        raise MonitorError(msg)
    recent: list[float] = []
    if rule.season:
        recent = [float(v) if v is not None and math.isfinite(v) else math.nan for v in history][-rule.span :]
        points = [v for v in recent if math.isfinite(v)]
    else:
        points = [float(v) for v in history if v is not None and math.isfinite(v)][-rule.window :]
    if len(points) < rule.min_points or not math.isfinite(current):
        return Trend(INSUFFICIENT_HISTORY, points=len(points))
    if rule.transform == "log" and (current <= -1.0 or min(points) <= -1.0):
        return Trend(INSUFFICIENT_HISTORY, points=len(points))
    ys = [_forward(v, rule.transform) for v in points]
    k = len(ys)
    model = "line"
    seasonal = None
    if rule.season:
        positions = [i for i, v in enumerate(recent) if math.isfinite(v)]
        seasonal = _seasonal_fit(positions, ys, rule.season, rule.fit, rule.scale)
        model = "seasonal" if seasonal else SEASON_FALLBACK
    if seasonal:
        n = len(recent)
        slope, s, x_mean, sxx, df = seasonal.slope, seasonal.sigma, seasonal.x_mean, seasonal.sxx, seasonal.df
        phase = n % rule.season
        fitted = seasonal.intercept + slope * n + seasonal.effects[phase]
        leverage = math.sqrt(1.0 + 1.0 / k + (n - x_mean) ** 2 / sxx + 1.0 / seasonal.counts[phase])
    else:
        slope, intercept, s, x_mean, sxx = _fit(ys, rule.fit, rule.scale)
        fitted = intercept + slope * k
        df = k - 2
        leverage = math.sqrt(1.0 + 1.0 / k + (k - x_mean) ** 2 / sxx)
    expected = _back(fitted, rule.transform)
    se = s * leverage
    residual_fit = _forward(current, rule.transform) - fitted
    tolerance = 1e-12 * max(1.0, abs(fitted))
    flat = se <= tolerance
    z = 0.0 if flat and abs(residual_fit) <= tolerance else None if flat else residual_fit / se
    half = critical_value(rule.red, df) * se
    lower, upper = _back(fitted - half, rule.transform), _back(fitted + half, rule.transform)
    status = _status(rule, current, expected, residual_fit, z, df, direction)
    return Trend(status, expected, z, slope, k, se, lower, upper, model)


__all__ = [
    "INSUFFICIENT_HISTORY",
    "SEASON_FALLBACK",
    "Trend",
    "TrendPolicy",
    "critical_value",
    "keeps",
    "remember",
    "t_cdf",
    "t_ppf",
    "trend_check",
]
