"""Golden values of the numpy reference engine, including the classic bugs of positional-array approaches."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
import pytest

from bricksmith.features import CATALOG
from bricksmith.features.reference import compute, compute_many, tercile_bounds

if TYPE_CHECKING:
    from collections.abc import Sequence

N = None


def lags(*chronological: Sequence[float | None]) -> np.ndarray:
    """Build a lag-order block from rows written oldest first (the last value is the current closing)."""
    rows = [[math.nan if v is None else float(v) for v in reversed(row)] for row in chronological]
    return np.array(rows, dtype=np.float64)


def one(name: str, row: Sequence[float | None]) -> float | None:
    value = float(compute(name, lags(row))[0])
    return None if math.isnan(value) else value


def close(actual: float | None, expected: float | None) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)


A = [1, 3, 2, 5, 4, 6]
SLOPE = 15.5 / 17.5
INTERCEPT = 3.5 - SLOPE * 2.5
SSE = 17.5 - 15.5**2 / 17.5


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("count", 6),
        ("count_positive", 6),
        ("sum", 21),
        ("mean", 3.5),
        ("mean_zero", 3.5),
        ("min", 1),
        ("max", 6),
        ("range", 5),
        ("last", 6),
        ("p25", 2.25),
        ("p50", 3.5),
        ("p90", 5.5),
        ("mode", 1),
        ("mode_count", 1),
        ("distinct", 6),
        ("null_run_trailing", 0),
        ("null_run_max", 0),
        ("increments", 3),
        ("decrements", 2),
        ("increment_run_current", 1),
        ("decrement_run_current", 0),
        ("increment_run_max", 1),
        ("decrement_run_max", 1),
        ("delta", 5),
        ("delta_mean", 1),
        ("abs_variation", 9),  # the non-absolute sum of differences would be 5
        ("since_positive", 0),
        ("since_max", 0),
        ("tercile_old_mean", 2),
        ("tercile_mid_mean", 3.5),
        ("tercile_new_mean", 5),
        ("tercile_diff", 3),
        ("var_pop", 17.5 / 6),
        ("var_samp", 3.5),
        ("std_pop", math.sqrt(17.5 / 6)),
        ("std_samp", math.sqrt(3.5)),
        ("cv", math.sqrt(3.5) / 3.5),
        ("rms", math.sqrt(91 / 6)),
        ("skew", 0),
        ("slope", SLOPE),
        ("intercept", INTERCEPT),
        ("r2", 1 - SSE / 17.5),  # the SSE/SST "R2" would be about 0.216
        ("resid_std", math.sqrt(SSE / 4)),
        ("trend_distance", 6 - (INTERCEPT + SLOPE * 5)),
    ],
)
def test_full_window(name: str, expected: float) -> None:
    assert close(one(name, list(A)), expected), (name, one(name, list(A)))


def test_skew_and_kurtosis_use_the_windows_own_mean() -> None:
    row: list[float | None] = [1, 1, 1, 1, 10]
    assert close(one("skew", row), 1.5)
    assert close(one("kurtosis", row), 0.25)
    shifted = compute_many(["skew", "kurtosis"], lags(row, [v + 100 for v in (1, 1, 1, 1, 10)]))
    assert np.allclose(shifted["skew"], 1.5)
    assert np.allclose(shifted["kurtosis"], 0.25)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("count", 3),
        ("count_positive", 2),
        ("sum", 4),
        ("mean", 4 / 3),
        ("mean_zero", 4 / 6),
        ("min", 0),
        ("max", 2),
        ("range", 2),
        ("last", None),
        ("p50", 2),
        ("mode", 2),
        ("mode_count", 2),
        ("distinct", 2),
        ("null_run_trailing", 1),
        ("null_run_max", 1),
        ("increments", 0),
        ("decrements", 1),
        ("decrement_run_current", 0),
        ("decrement_run_max", 1),
        ("abs_variation", 2),
        ("delta", -2),
        ("delta_mean", -2 / 3),
        ("since_positive", 2),
        ("since_max", 2),
        ("tercile_old_mean", 2),
        ("tercile_mid_mean", 2),
        ("tercile_new_mean", 0),
        ("tercile_diff", -2),
        ("trend_distance", None),
    ],
)
def test_nulls_and_gaps(name: str, expected: float | None) -> None:
    row: list[float | None] = [N, 2, N, 2, 0, N]
    assert close(one(name, row), expected), (name, one(name, row))


def test_short_windows_never_divide_by_zero_or_overflow() -> None:
    short: list[float | None] = [5, 7]
    for name in ("tercile_old_mean", "tercile_mid_mean", "tercile_new_mean", "tercile_diff", "skew", "kurtosis"):
        assert one(name, short) is None, name
    assert close(one("p99", short), 6.98)
    assert close(one("p1", short), 5.02)
    assert close(one("var_samp", short), 2)
    assert close(one("r2", short), 1)
    assert one("resid_std", short) is None
    sparse: Sequence[float | None] = [*([N] * 11), 4]
    for name in ("p75", "p1", "p99", "mean", "min", "max"):
        assert close(one(name, sparse), 4), name
    assert one("var_samp", sparse) is None
    assert close(one("var_pop", sparse), 0)
    assert one("slope", sparse) is None
    assert close(one("null_run_max", sparse), 11)


def test_constant_series() -> None:
    for value in (3.0, 0.1):
        row: list[float | None] = [value] * 4
        assert one("var_pop", row) == 0
        assert one("std_samp", row) == 0
        assert one("cv", row) == 0
        assert one("skew", row) is None
        assert one("kurtosis", row) is None
        assert one("r2", row) is None
        assert close(one("slope", row), 0)
        assert one("increments", row) == 0
        assert close(one("mode_count", row), 4)
    zeros: list[float | None] = [0, 0, 0]
    assert one("cv", zeros) is None
    assert one("since_positive", zeros) is None
    assert close(one("since_max", zeros), 0)


def test_all_null_window() -> None:
    row: list[float | None] = [N, N, N]
    never_null = {name for name, entry in CATALOG.items() if entry.min_points == 0}
    values = {name: one(name, row) for name in CATALOG}
    assert {name for name, value in values.items() if value is not None} == never_null
    assert values["null_run_trailing"] == 3
    assert values["count"] == 0


def test_mode_ties_go_to_the_smallest_value_and_runs_count_steps() -> None:
    assert one("mode", [2, 1, 2, 1]) == 1
    assert one("mode_count", [2, 1, 2, 1]) == 2
    rising: list[float | None] = [5, 4, 5, 6, 7]
    assert one("increment_run_current", rising) == 3
    assert one("increment_run_max", rising) == 3
    assert one("decrement_run_current", rising) == 0
    broken: list[float | None] = [1, 2, N, 3, 4]
    assert one("increments", broken) == 2
    assert one("increment_run_current", broken) == 1


def test_tercile_bounds_follow_array_split() -> None:
    assert tercile_bounds(3) == [(0, 1), (1, 2), (2, 3)]
    assert tercile_bounds(7) == [(0, 3), (3, 5), (5, 7)]
    assert tercile_bounds(8) == [(0, 3), (3, 6), (6, 8)]


def test_every_catalog_entry_is_computed_on_many_rows() -> None:
    rng = np.random.default_rng(7)
    block = rng.normal(size=(50, 12))
    block[rng.random(block.shape) < 0.2] = np.nan
    values = compute_many([*CATALOG, "p10", "p95"], block)
    assert all(v.shape == (50,) for v in values.values())
    assert np.all(values["count"] == (~np.isnan(block)).sum(axis=1))
    with pytest.raises(ValueError, match="rows, w"):
        compute("mean", np.zeros(3))
