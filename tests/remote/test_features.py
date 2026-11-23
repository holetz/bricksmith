"""Feature engineering on the selected workspace: native Spark equals the numpy reference."""

from __future__ import annotations

import math
import random
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.features import (
    CATALOG,
    CategoricalCount,
    Difference,
    FeatureSpec,
    PointInTimeError,
    Ratio,
    WindowFeature,
    compute_features,
    compute_rows,
)

if TYPE_CHECKING:
    from bricksmith.clients import Clients

DDL = "entity BIGINT, branch STRING, closing DATE, x DOUBLE, y DOUBLE, status STRING"
SPEC = FeatureSpec(
    keys=("entity", "branch"),
    closing="closing",
    features=(WindowFeature(("x", "y"), (1, 2, 3, 6, 12), (*CATALOG, "p10", "p50", "p95")),),
    ratios=(Ratio("x_over_y_3p", "x_mean_3p", "y_mean_3p"), Ratio("x_over_y", "x", "y")),
    differences=(Difference("x_sum_less_y", "x_sum_12p", "y"),),
    categorical=(CategoricalCount("status", ("open", "late"), (3, 6)),),
)


def synthetic() -> list[dict[str, Any]]:
    """Entities with nulls, gaps, short histories and constant series (seeded)."""
    rnd = random.Random(11)  # noqa: S311 - seeded synthetic data
    rows: list[dict[str, Any]] = []
    for entity in range(60):
        kind = entity % 5
        first = 22 if kind == 4 else rnd.randint(0, 10)  # kind 4: one or two closings only
        for m in range(first, 24):
            if kind != 4 and rnd.random() < 0.1:
                continue  # a missing closing
            x = 5.0 if kind == 0 else None if rnd.random() < 0.2 else float(rnd.randint(-3, 9))
            y = None if rnd.random() < 0.15 else 0.0 if rnd.random() < 0.1 else round(rnd.gauss(100, 30), 2)
            rows.append(
                {
                    "entity": entity,
                    "branch": "ns"[entity % 2],
                    "closing": date(2024 + m // 12, m % 12 + 1, 28),
                    "x": x,
                    "y": y,
                    "status": rnd.choice(["open", "late", None, "closed"]),
                }
            )
    return rows


def same(a: object, b: object) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is b
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)  # pyright: ignore[reportArgumentType]
    return a == b


def assert_equal_to_reference(frame: Any, rows: list[dict[str, Any]]) -> None:  # noqa: ANN401
    expected = {(r["entity"], r["branch"], r["closing"]): r for r in compute_rows(rows, SPEC)}
    got = [r.asDict() for r in frame.collect()]
    assert len(got) == len(expected)
    mismatched: dict[str, int] = {}
    for row in got:
        want = expected[row["entity"], row["branch"], row["closing"]]
        assert list(row) == list(want)
        for name, value in want.items():
            if not same(row[name], value):
                mismatched[name] = mismatched.get(name, 0) + 1
    assert not mismatched, mismatched


def test_native_spark_equals_the_reference(remote_clients: Clients) -> None:
    spark = remote_clients.spark()
    rows = synthetic()
    frame = spark.createDataFrame(rows, DDL)
    assert_equal_to_reference(compute_features(frame, SPEC, engine="spark"), rows)


def test_native_spark_refuses_the_future(remote_clients: Clients) -> None:
    spark = remote_clients.spark()
    frame = spark.createDataFrame(synthetic(), DDL)
    with pytest.raises(PointInTimeError):
        compute_features(frame, SPEC, engine="spark", as_of="2025-06-28")
    at = compute_features(frame.where("closing <= DATE'2025-06-28'"), SPEC, engine="spark", as_of="2025-06-28")
    assert {r["closing"] for r in at.select("closing").distinct().collect()} == {date(2025, 6, 28)}
