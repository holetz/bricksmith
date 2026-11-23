"""Rows engine: lags from closings, gaps as nulls, ratios, differences, category counts and point in time."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from bricksmith.features import (
    CategoricalCount,
    Difference,
    FeatureError,
    FeatureSpec,
    PointInTimeError,
    Ratio,
    WindowFeature,
    compute_rows,
)
from bricksmith.features.engine import Frame, lag_positions

SPEC = FeatureSpec(
    keys=("entity", "branch"),
    closing="closing",
    features=(WindowFeature(("balance", "limit"), (1, 3), ("sum", "count", "last", "mean")),),
    ratios=(Ratio("use_3p", "balance_sum_3p", "limit_sum_3p"), Ratio("use_now", "balance", "limit")),
    differences=(Difference("headroom", "limit", "balance"),),
    categorical=(CategoricalCount("status", ("paid", "late"), (3,)),),
)


def rows() -> list[dict[str, object]]:
    return [
        {"entity": 1, "branch": "a", "closing": "2026-01-31", "balance": 10, "limit": 100, "status": "paid"},
        {"entity": 2, "branch": "a", "closing": date(2026, 1, 31), "balance": 5, "limit": 0, "status": "late"},
        {"entity": 1, "branch": "a", "closing": "2026-02-28", "balance": None, "limit": 100, "status": "late"},
        # entity 1 skips March: April's 3-period window is April, March (missing) and February
        {"entity": 1, "branch": "a", "closing": "2026-04-30", "balance": 30, "limit": 50, "status": "paid"},
        {"entity": 1, "branch": "b", "closing": "2026-04-30", "balance": 7, "limit": 7, "status": None},
    ]


def by_target(result: list[dict[str, object]]) -> dict[tuple[object, ...], dict[str, object]]:
    return {(r["entity"], r["branch"], r["closing"]): r for r in result}


def test_one_row_per_entity_and_closing_in_input_order() -> None:
    result = compute_rows(rows(), SPEC)
    assert [(r["entity"], r["branch"], r["closing"]) for r in result] == [
        (1, "a", date(2026, 1, 31)),
        (2, "a", date(2026, 1, 31)),
        (1, "a", date(2026, 2, 28)),
        (1, "a", date(2026, 4, 30)),
        (1, "b", date(2026, 4, 30)),
    ]
    assert list(result[0]) == ["entity", "branch", "closing", *(o.name for o in SPEC.outputs)]


def test_windows_follow_period_lags_not_row_positions() -> None:
    r = by_target(compute_rows(rows(), SPEC))
    april = r[1, "a", date(2026, 4, 30)]
    assert april["balance_sum_3p"] == 30.0  # February is null, March is missing
    assert april["balance_count_3p"] == 1
    assert april["limit_sum_3p"] == 150.0
    assert april["limit_mean_3p"] == 75.0
    feb = r[1, "a", date(2026, 2, 28)]
    assert feb["balance_last_1p"] is None
    assert feb["balance_sum_3p"] == 10.0
    assert feb["balance_count_1p"] == 0
    other = r[1, "b", date(2026, 4, 30)]
    assert other["limit_sum_3p"] == 7.0  # another branch is another entity
    assert isinstance(april["balance_count_3p"], int)
    assert isinstance(april["balance_sum_3p"], float)


def test_ratios_differences_and_category_counts() -> None:
    r = by_target(compute_rows(rows(), SPEC))
    april = r[1, "a", date(2026, 4, 30)]
    assert april["use_3p"] == pytest.approx(30 / 150)
    assert april["use_now"] == pytest.approx(30 / 50)
    assert april["headroom"] == 20.0
    assert april["status_count_paid_3p"] == 1
    assert april["status_count_late_3p"] == 1
    zero = r[2, "a", date(2026, 1, 31)]
    assert zero["use_now"] is None  # zero denominator
    assert zero["use_3p"] is None
    assert zero["headroom"] == -5.0
    feb = r[1, "a", date(2026, 2, 28)]
    assert feb["use_now"] is None  # null numerator
    assert r[1, "b", date(2026, 4, 30)]["status_count_paid_3p"] == 0


def test_as_of_targets_one_closing_and_refuses_the_future() -> None:
    result = compute_rows(rows(), SPEC, as_of="2026-04-30")
    assert {(r["entity"], r["branch"]) for r in result} == {(1, "a"), (1, "b")}
    with pytest.raises(PointInTimeError, match="2026-04-30"):
        compute_rows(rows(), SPEC, as_of=date(2026, 2, 28))
    assert compute_rows([], SPEC) == []
    early = [r for r in rows() if str(r["closing"]) < "2026-03"]
    assert compute_rows(early, SPEC, as_of="2026-03-31") == []  # no row at that closing


def test_bad_input_is_refused() -> None:
    duplicate = [*rows(), {**rows()[0], "closing": "2026-01-15"}]
    with pytest.raises(FeatureError, match="more than one row"):
        compute_rows(duplicate, SPEC)
    with pytest.raises(FeatureError, match="cannot be null"):
        compute_rows([{**rows()[0], "entity": None}], SPEC)
    with pytest.raises(FeatureError, match="numeric"):
        compute_rows([{**rows()[0], "balance": "ten"}], SPEC)


def test_quarter_periods() -> None:
    spec = FeatureSpec(("e",), "c", (WindowFeature(("x",), (2,), ("sum",)),), period="quarter")
    data = [{"e": 1, "c": "2026-03-31", "x": 1}, {"e": 1, "c": "2026-06-30", "x": 2}]
    assert [r["x_sum_2p"] for r in compute_rows(data, spec)] == [1.0, 3.0]


def test_lag_positions_on_columnar_frames() -> None:
    frame = Frame(
        entities=np.array([0, 0, 1, 0], dtype=np.int64),
        periods=np.array([5, 3, 4, 4], dtype=np.int64),
        values={},
        categories={},
    )
    positions = lag_positions(frame, np.array([0, 2], dtype=np.int64), 3)
    assert positions.tolist() == [[0, 3, 1], [2, -1, -1]]
    clash = Frame(np.array([0, 0]), np.array([1, 1]), {}, {})  # pyright: ignore[reportArgumentType]
    with pytest.raises(FeatureError, match="more than one row"):
        lag_positions(clash, np.array([0], dtype=np.int64), 2)
