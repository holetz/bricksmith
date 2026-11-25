"""The model-monitoring pure and Spark frames agree on segment names, null counts and Decimal typing.

The Spark cases skip where pyspark is missing or cannot start a local session. Run them with
``uv run --isolated --no-project --with pyspark==4.0.1 --with-editable . --with pytest pytest <this file>``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.monitor.model.engine import frame_of

if TYPE_CHECKING:
    from collections.abc import Iterator

NAN = float("nan")


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


def test_pure_decimal_is_numeric() -> None:
    assert frame_of([{"v": Decimal("1.50")}, {"v": Decimal("2.5")}, {"v": None}]).numeric("v")
    assert frame_of([{"v": 1}, {"v": 2.5}, {"v": Decimal(3)}]).numeric("v")
    assert not frame_of([{"v": "a"}, {"v": Decimal(3)}]).numeric("v")
    assert not frame_of([{"v": True}, {"v": False}]).numeric("v")


def test_pure_segment_names_are_canonical() -> None:
    frame = frame_of([{"g": True}, {"g": False}, {"g": None}, {"g": NAN}, {"g": True}])
    assert frame.segments("g") == ["(null)", "false", "true"]
    assert frame.segment("g", "(null)").count() == 2
    assert frame.segment("g", "true").count() == 2


def test_spark_segments_and_counts_equal_pure(spark: Any) -> None:  # noqa: ANN401
    rows: list[tuple[Any, ...]] = [
        (True, NAN, Decimal("1.50")), (False, 1.5, Decimal("2.25")), (None, None, None),
        (True, 1.5, Decimal("1.50")), (False, NAN, Decimal("3.00")),
    ]  # fmt: skip
    df = spark.createDataFrame(rows, "g BOOLEAN, d DOUBLE, n DECIMAL(10,2)")
    pure = frame_of([dict(zip(("g", "d", "n"), r, strict=True)) for r in rows])
    cluster = frame_of(df)
    for column in ("g", "d"):
        assert cluster.segments(column) == pure.segments(column)
        for name in pure.segments(column):
            assert cluster.segment(column, name).count() == pure.segment(column, name).count()
        assert cluster.null_count(column) == pure.null_count(column)
    assert cluster.segments("g") == ["(null)", "false", "true"]
    assert cluster.segments("d") == ["(null)", "1.5"]
    assert cluster.segment("g", "nope").count() == 0
    assert cluster.numeric("n") == frame_of([{"n": Decimal("1.5")}]).numeric("n") is True
    assert cluster.null_count("n") == pure.null_count("n") == 1
