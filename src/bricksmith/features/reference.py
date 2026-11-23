"""The numpy reference engine: every catalog aggregate over a block of windows, vectorized across rows.

A block is a ``(rows, w)`` float64 array; column ``k`` holds lag ``k`` (column 0 is the current closing) and NaN
is null. :func:`compute` returns one float64 value per row, NaN for null; counts come back as whole floats. The
definitions are the ones of :mod:`bricksmith.features.catalog`, and this module is the oracle every other engine
is tested against.
"""

from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

from bricksmith.features._numpy import np
from bricksmith.features.catalog import aggregate, percentile_of

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from numpy.typing import NDArray

    Array = NDArray[np.float64]


def tercile_bounds(w: int) -> list[tuple[int, int]]:
    """Return the ``(start, stop)`` chronological positions of the oldest, middle and newest thirds."""
    size, extra = divmod(w, 3)
    bounds: list[tuple[int, int]] = []
    start = 0
    for part in range(3):
        stop = start + size + (1 if part < extra else 0)
        bounds.append((start, stop))
        start = stop
    return bounds


def _masked_sum(values: Array, mask: NDArray[np.bool_]) -> Array:
    return np.where(mask, values, 0.0).sum(axis=1)


def _null_where(values: Array, condition: NDArray[np.bool_]) -> Array:
    return np.where(condition, np.nan, values)


class Window:
    """Shared intermediate values of one block, computed once and reused by every aggregate."""

    def __init__(self, block: Array) -> None:
        """Hold the block (lag order) and its chronological view."""
        self.lags = np.asarray(block, dtype=np.float64)
        if self.lags.ndim != 2:  # noqa: PLR2004 - a block is two-dimensional
            msg = "a window block must be a (rows, w) array"
            raise ValueError(msg)
        self.rows, self.w = self.lags.shape
        self.chrono = self.lags[:, ::-1]

    @cached_property
    def mask(self) -> NDArray[np.bool_]:
        """Non-null positions, chronological."""
        return ~np.isnan(self.chrono)

    @cached_property
    def n(self) -> Array:
        """Number of non-null values per row."""
        return self.mask.sum(axis=1).astype(np.float64)

    @cached_property
    def total(self) -> Array:
        """Sum of the non-null values (0 when there are none)."""
        return _masked_sum(self.chrono, self.mask)

    @cached_property
    def mean(self) -> Array:
        """Mean of the non-null values."""
        with np.errstate(invalid="ignore", divide="ignore"):
            return self.total / self.n

    @cached_property
    def minimum(self) -> Array:
        """Smallest non-null value."""
        return np.where(self.mask, self.chrono, np.inf).min(axis=1, initial=np.inf)

    @cached_property
    def maximum(self) -> Array:
        """Largest non-null value."""
        return np.where(self.mask, self.chrono, -np.inf).max(axis=1, initial=-np.inf)

    @cached_property
    def constant(self) -> NDArray[np.bool_]:
        """Rows whose non-null values are all equal (exactly)."""
        return self.maximum == self.minimum

    @cached_property
    def deviations(self) -> Array:
        """Deviations from the row mean, 0 at null positions."""
        return np.where(self.mask, self.chrono - self.mean[:, None], 0.0)

    def moment(self, power: int) -> Array:
        """Return the sum of deviations raised to ``power`` (0 for a constant row)."""
        return np.where(self.constant, 0.0, (self.deviations**power).sum(axis=1))

    @cached_property
    def sorted_values(self) -> Array:
        """Each row sorted ascending, nulls last."""
        return np.sort(self.chrono, axis=1)

    @cached_property
    def runs(self) -> tuple[Array, NDArray[np.bool_]]:
        """Run lengths of equal values in the sorted rows (0 at nulls) and where a new value starts."""
        values = self.sorted_values
        valid = ~np.isnan(values)
        lengths = np.zeros_like(values)
        starts = np.zeros(values.shape, dtype=bool)
        for j in range(self.w):
            same = (values[:, j] == values[:, j - 1]) if j else np.zeros(self.rows, dtype=bool)
            lengths[:, j] = np.where(valid[:, j], np.where(same, lengths[:, j - 1] + 1, 1), 0)
            starts[:, j] = valid[:, j] & ~same
        return lengths, starts

    @cached_property
    def steps(self) -> Array:
        """Chronological step differences (``w - 1`` columns), NaN where either side is null."""
        return self.chrono[:, 1:] - self.chrono[:, :-1]

    def run_lengths(self, flags: NDArray[np.bool_]) -> tuple[Array, Array]:
        """Return the run of ``True`` ending at the last column and the longest run, per row."""
        current = np.zeros(self.rows)
        longest = np.zeros(self.rows)
        for j in range(flags.shape[1]):
            current = np.where(flags[:, j], current + 1, 0.0)
            longest = np.maximum(longest, current)
        return current, longest

    @cached_property
    def positions(self) -> Array:
        """Chronological positions ``t = 0 .. w - 1``."""
        return np.arange(self.w, dtype=np.float64)

    @cached_property
    def fit(self) -> tuple[Array, Array, Array]:
        """OLS of the value on the position over the non-null points: slope, intercept and SSE."""
        t = np.where(self.mask, self.positions[None, :], 0.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            t_mean = t.sum(axis=1) / self.n
            dt = np.where(self.mask, self.positions[None, :] - t_mean[:, None], 0.0)
            slope = (dt * self.deviations).sum(axis=1) / (dt**2).sum(axis=1)
            intercept = self.mean - slope * t_mean
            fitted = intercept[:, None] + slope[:, None] * self.positions[None, :]
            sse = np.where(self.mask, (self.chrono - fitted) ** 2, 0.0).sum(axis=1)
        return slope, intercept, sse

    def latest_lag(self, flags: NDArray[np.bool_]) -> Array:
        """Return the smallest lag where ``flags`` (lag order) holds, NaN when it never does."""
        found = flags.any(axis=1)
        return np.where(found, flags.argmax(axis=1).astype(np.float64), np.nan)


def _percentile(window: Window, q: int) -> Array:
    values = window.sorted_values
    n = window.n
    position = q / 100 * np.maximum(n - 1, 0)
    low = np.floor(position).astype(np.int64)
    high = np.minimum(low + 1, np.maximum(n - 1, 0).astype(np.int64))
    rows = np.arange(window.rows)
    lo = values[rows, low]
    hi = values[rows, high]
    return lo + (position - low) * (hi - lo)


def _mode(window: Window, count: bool) -> Array:  # noqa: FBT001 - private switch
    lengths, _ = window.runs
    best = lengths.argmax(axis=1)
    rows = np.arange(window.rows)
    return lengths[rows, best] if count else window.sorted_values[rows, best]


def _null_runs(window: Window, trailing: bool) -> Array:  # noqa: FBT001 - private switch
    current, longest = window.run_lengths(~window.mask)
    return current if trailing else longest


def _steps(window: Window, sign: int) -> NDArray[np.bool_]:
    with np.errstate(invalid="ignore"):
        return (window.steps * sign) > 0


def _delta(window: Window, per_step: bool) -> Array:  # noqa: FBT001 - private switch
    mask = window.mask
    rows = np.arange(window.rows)
    oldest = mask.argmax(axis=1)
    newest = window.w - 1 - mask[:, ::-1].argmax(axis=1)
    delta = window.chrono[rows, newest] - window.chrono[rows, oldest]
    if not per_step:
        return delta
    with np.errstate(invalid="ignore", divide="ignore"):
        return delta / (newest - oldest)


def _abs_variation(window: Window) -> Array:
    valid = ~np.isnan(window.steps)
    total = np.where(valid, np.abs(window.steps), 0.0).sum(axis=1)
    return _null_where(total, ~valid.any(axis=1))


def _tercile(window: Window, part: int) -> Array:
    if window.w < 3:  # noqa: PLR2004 - terciles need three positions
        return np.full(window.rows, np.nan)
    start, stop = tercile_bounds(window.w)[part]
    mask = window.mask[:, start:stop]
    count = mask.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return _masked_sum(window.chrono[:, start:stop], mask) / count


def _variance(window: Window, ddof: int) -> Array:
    with np.errstate(invalid="ignore", divide="ignore"):
        return window.moment(2) / (window.n - ddof)


def _cv(window: Window) -> Array:
    with np.errstate(invalid="ignore", divide="ignore"):
        cv = np.sqrt(_variance(window, 1)) / np.abs(window.mean)
    return _null_where(cv, window.mean == 0)


def _rms(window: Window) -> Array:
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.sqrt(_masked_sum(window.chrono**2, window.mask) / window.n)


def _standardized(window: Window, power: int) -> Array:
    with np.errstate(invalid="ignore", divide="ignore"):
        m2 = window.moment(2) / window.n
        value = (window.moment(power) / window.n) / m2 ** (power / 2)
    if power == 4:  # noqa: PLR2004 - excess kurtosis
        value = value - 3.0
    return _null_where(value, window.constant)


def _r2(window: Window) -> Array:
    _, _, sse = window.fit
    with np.errstate(invalid="ignore", divide="ignore"):
        r2 = 1.0 - sse / window.moment(2)
    return _null_where(r2, window.constant)


def _resid_std(window: Window) -> Array:
    _, _, sse = window.fit
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.sqrt(sse / (window.n - 2))


def _trend_distance(window: Window) -> Array:
    slope, intercept, _ = window.fit
    return window.lags[:, 0] - (intercept + slope * (window.w - 1))


def _positive_lag(window: Window) -> Array:
    with np.errstate(invalid="ignore"):
        return window.latest_lag(window.lags > 0)


def _max_lag(window: Window) -> Array:
    return window.latest_lag(window.lags == window.maximum[:, None])


def _with_nan(values: Array, window: Window) -> Array:
    """Replace the infinities of an empty row's min or max with NaN."""
    return _null_where(values, window.n == 0)


_COMPUTE: dict[str, Callable[[Window], Array]] = {
    "count": lambda x: x.n,
    "count_positive": lambda x: (np.nan_to_num(x.chrono, nan=0.0) > 0).sum(axis=1).astype(np.float64),
    "sum": lambda x: x.total,
    "mean": lambda x: x.mean,
    "mean_zero": lambda x: x.total / x.w,
    "min": lambda x: _with_nan(x.minimum, x),
    "max": lambda x: _with_nan(x.maximum, x),
    "range": lambda x: _with_nan(x.maximum - x.minimum, x),
    "last": lambda x: x.lags[:, 0],
    "mode": lambda x: _mode(x, count=False),
    "mode_count": lambda x: _mode(x, count=True),
    "distinct": lambda x: x.runs[1].sum(axis=1).astype(np.float64),
    "null_run_trailing": lambda x: _null_runs(x, trailing=True),
    "null_run_max": lambda x: _null_runs(x, trailing=False),
    "increments": lambda x: _steps(x, 1).sum(axis=1).astype(np.float64),
    "decrements": lambda x: _steps(x, -1).sum(axis=1).astype(np.float64),
    "increment_run_current": lambda x: x.run_lengths(_steps(x, 1))[0],
    "decrement_run_current": lambda x: x.run_lengths(_steps(x, -1))[0],
    "increment_run_max": lambda x: x.run_lengths(_steps(x, 1))[1],
    "decrement_run_max": lambda x: x.run_lengths(_steps(x, -1))[1],
    "delta": lambda x: _delta(x, per_step=False),
    "delta_mean": lambda x: _delta(x, per_step=True),
    "abs_variation": _abs_variation,
    "since_positive": _positive_lag,
    "since_max": _max_lag,
    "tercile_old_mean": lambda x: _tercile(x, 0),
    "tercile_mid_mean": lambda x: _tercile(x, 1),
    "tercile_new_mean": lambda x: _tercile(x, 2),
    "tercile_diff": lambda x: _tercile(x, 2) - _tercile(x, 0),
    "var_pop": lambda x: _variance(x, 0),
    "var_samp": lambda x: _variance(x, 1),
    "std_pop": lambda x: np.sqrt(_variance(x, 0)),
    "std_samp": lambda x: np.sqrt(_variance(x, 1)),
    "cv": _cv,
    "rms": _rms,
    "skew": lambda x: _standardized(x, 3),
    "kurtosis": lambda x: _standardized(x, 4),
    "slope": lambda x: x.fit[0],
    "intercept": lambda x: x.fit[1],
    "r2": _r2,
    "resid_std": _resid_std,
    "trend_distance": _trend_distance,
}


def _compute_one(name: str, window: Window) -> Array:
    entry = aggregate(name)
    if window.w < entry.min_window:
        return np.full(window.rows, np.nan)
    q = percentile_of(name)
    values = _percentile(window, q) if q is not None else _COMPUTE[name](window)
    return _null_where(np.asarray(values, dtype=np.float64), window.n < entry.min_points)


def compute(name: str, block: Array) -> Array:
    """Return aggregate ``name`` of every row of ``block`` (NaN where the catalog says null)."""
    return _compute_one(name, Window(block))


def compute_many(names: Iterable[str], block: Array) -> dict[str, Array]:
    """Return several aggregates of one block, sharing their intermediate values."""
    window = Window(block)
    return {name: _compute_one(name, window) for name in names}
