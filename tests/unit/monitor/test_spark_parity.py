"""The Spark engine equals the pure engine on NaN, double categories and boolean or NaN segments (local pyspark).

Skipped where pyspark is missing or cannot start a local session (Databricks Connect refuses one). Run with
``uv run --isolated --no-project --with pyspark==4.0.1 --with-editable . --with pytest pytest <this file>``.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.monitor import fit_reference
from bricksmith.monitor.measure import NULL_SEGMENT, measure_rows

if TYPE_CHECKING:
    from collections.abc import Iterator

    from bricksmith.monitor.reference import Reference

NAN = float("nan")
SCHEMA = "x DOUBLE, k DOUBLE, s DOUBLE, y DOUBLE, g BOOLEAN, d DOUBLE"
NAMES = ("x", "k", "s", "y", "g", "d")
FIT = [  # x, k (double categories), s, y, g, d (double segment)
    (1.0, 1e7, 0.1, 0.0, True, 0.5), (2.0, 1e7, 0.2, 0.0, False, 0.5),
    (3.0, 1e-5, 0.4, 1.0, True, 1e7), (4.0, 0.5, 0.7, 1.0, False, 1e7),
    (5.0, 1e-5, 0.9, 1.0, True, 0.5), (6.0, 0.5, 0.3, 0.0, None, 1e7),
]  # fmt: skip
NOW = [  # NaN label, NaN category, NaN and null segments, an unseen category
    (1.5, 1e7, 0.2, NAN, True, 0.5), (2.5, NAN, 0.6, 1.0, False, NAN),
    (3.5, 1e-5, 0.8, 1.0, None, 1e7), (4.5, 2.0, 0.4, 0.0, True, None),
    (5.5, 0.5, 0.9, 1.0, False, 0.5), (0.5, None, 0.1, 0.0, True, 1e7),
]  # fmt: skip
COLUMNS = {"x": {"bins": 2}, "k": {"type": "categorical"}, "s": {"edges": [0, 0.5, 1]}}


@pytest.fixture(scope="module")
def spark() -> Iterator[Any]:
    sql = pytest.importorskip("pyspark.sql")
    try:
        session = (
            sql.SparkSession.builder.master("local[1]")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "1")
            .getOrCreate()
        )
    except Exception as exc:  # noqa: BLE001 - any refusal to start a local session skips
        pytest.skip(f"no local Spark session: {exc}")
    yield session
    session.stop()


def rows_of(data: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    return [dict(zip(NAMES, r, strict=True)) for r in data]


def close(a: Any, b: Any) -> bool:  # noqa: ANN401
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(close(a[k], b[k]) for k in a)
    if isinstance(a, float) and isinstance(b, float):
        return (math.isnan(a) and math.isnan(b)) or abs(a - b) < 1e-9
    return bool(a == b)


def reference(segment: str) -> Reference:
    return fit_reference(rows_of(FIT), COLUMNS, name="p", version=1, score="s", label="y", segment=segment)


def assert_same(spark: Any, ref: Reference) -> dict[tuple[str, str, str], Any]:  # noqa: ANN401
    from bricksmith.monitor.spark import measure_spark  # noqa: PLC0415

    pure = {m.key: m for m in measure_rows(ref, rows_of(NOW))}
    remote = {m.key: m for m in measure_spark(ref, spark.createDataFrame(NOW, SCHEMA))}
    assert sorted(remote) == sorted(pure)
    for key, expected in pure.items():
        actual = remote[key]
        assert (actual.status, actual.reason, actual.count) == (expected.status, expected.reason, expected.count), key
        assert close(actual.value, expected.value), key
        assert close(dict(actual.detail), dict(expected.detail)), key
    return pure


def test_fit_spark_spells_double_categories_like_the_pure_fit(spark: Any) -> None:  # noqa: ANN401
    from bricksmith.monitor.spark import fit_spark  # noqa: PLC0415

    fitted = fit_spark(spark.createDataFrame(FIT, SCHEMA), COLUMNS, name="p", version=1, score="s", label="y")
    pure = fit_reference(rows_of(FIT), COLUMNS, name="p", version=1, score="s", label="y")
    assert fitted.fingerprint == pure.fingerprint
    assert set(pure.column("k").categories) == {"10000000.0", "1e-05", "0.5"}


def test_nan_label_is_unlabeled_on_both_engines(spark: Any) -> None:  # noqa: ANN401
    pure = assert_same(spark, reference(""))
    assert pure["auc", "s", ""].status == "ok"


def test_nan_and_double_categories_bin_alike(spark: Any) -> None:  # noqa: ANN401
    pure = assert_same(spark, reference(""))
    assert pure["null_rate", "k", ""].value == pytest.approx(2 / 6)
    assert pure["coverage", "k", ""].detail["unseen"] == pytest.approx(1 / 6)


def test_boolean_segments_are_named_alike(spark: Any) -> None:  # noqa: ANN401
    pure = assert_same(spark, reference("g"))
    assert {k[2] for k in pure} == {"", "true", "false", NULL_SEGMENT}


def test_double_and_nan_segments_are_named_alike(spark: Any) -> None:  # noqa: ANN401
    pure = assert_same(spark, reference("d"))
    assert {k[2] for k in pure} == {"", "0.5", "10000000.0", NULL_SEGMENT}
