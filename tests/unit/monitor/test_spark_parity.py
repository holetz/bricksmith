"""The Spark engine equals the pure engine on NaN, double categories and boolean or NaN segments (local pyspark).

Skipped where pyspark is missing or cannot start a local session (Databricks Connect refuses one). Run with
``uv run --isolated --no-project --with pyspark==4.0.1 --with-editable . --with pytest pytest <this file>``.
"""

from __future__ import annotations

import dataclasses
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


STABILITY_SCHEMA = "month STRING, rate DOUBLE, seg DOUBLE"
STABILITY_VALUES = (1e7, 0.5, 2.0, NAN, None, 1e-5, 0.25)
STABILITY_ROWS = [
    {"month": month, "rate": value, "seg": STABILITY_VALUES[(i + 3) % len(STABILITY_VALUES)]}
    for month in ("2026-01", "2026-02")
    for i, value in enumerate(STABILITY_VALUES * 6)
]


def stability_profiles(spark: Any, config_extra: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:  # noqa: ANN401
    from bricksmith.monitor.stability import parse_config  # noqa: PLC0415
    from bricksmith.monitor.stability.profile import fit_rows_binning, profile_rows  # noqa: PLC0415
    from bricksmith.monitor.stability.spark import profile_spark  # noqa: PLC0415

    config = parse_config(
        {"period": "month", "columns": {"rate": {"type": "categorical"}}, **config_extra, "max_segments": 50}
    )
    fit = [r for r in STABILITY_ROWS if r["month"] == "2026-01" and r["rate"] in (1e7, 0.5)]
    binning, _ = fit_rows_binning(fit, config, ["2026-01"], dataset="d", version=1)
    periods = ["2026-01", "2026-02"]
    pure, _ = profile_rows(binning, STABILITY_ROWS, config, periods, dataset="d", run_id="r")
    frame = spark.createDataFrame([tuple(r.values()) for r in STABILITY_ROWS], STABILITY_SCHEMA)
    remote, _ = profile_spark(binning, frame, config, periods, dataset="d", run_id="r")
    return pure, remote


def keyed(rows: list[dict[str, Any]]) -> dict[tuple[str, ...], Any]:
    return {(r["period"], r["segment"], r["column"], r["bin"]): r["count"] for r in rows}


def test_stability_profile_bins_double_and_nan_categories_alike(spark: Any) -> None:  # noqa: ANN401
    pure, remote = stability_profiles(spark, {})
    assert keyed(remote) == keyed(pure)
    got = keyed(pure)
    assert got["2026-01", "", "rate", "null"] > 0
    assert got["2026-01", "", "rate", "unseen"] > 0
    assert got["2026-01", "", "rate", "cat:10000000.0"] > 0


def test_stability_segment_keys_spell_double_and_nan_alike(spark: Any) -> None:  # noqa: ANN401
    pure, remote = stability_profiles(spark, {"segments": ["seg"]})
    assert keyed(remote) == keyed(pure)
    assert {k[1] for k in keyed(pure)} >= {"", "seg=10000000.0", "seg=1e-05", "seg=0.5", "seg=(null)"}


@pytest.mark.parametrize("kind", ["DOUBLE", "FLOAT"])
@pytest.mark.parametrize("stored", ["0.0", "-0.0"])
def test_both_zero_signs_are_one_stability_category_on_both_engines(spark: Any, kind: str, stored: str) -> None:  # noqa: ANN401
    from bricksmith.monitor.bins import BinSpec  # noqa: PLC0415
    from bricksmith.monitor.stability import parse_config  # noqa: PLC0415
    from bricksmith.monitor.stability.profile import fit_rows_binning, profile_rows  # noqa: PLC0415
    from bricksmith.monitor.stability.spark import profile_spark  # noqa: PLC0415

    values = (-0.0, 0.0, 0.5, 0.5, -0.0, 0.0, 2.0)
    rows = [{"month": m, "rate": v, "seg": v} for m in ("2026-01", "2026-02") for v in values]
    config = parse_config(
        {"period": "month", "columns": {"rate": {"type": "categorical"}}, "segments": ["seg"], "max_segments": 50}
    )
    periods = ["2026-01", "2026-02"]
    binning, _ = fit_rows_binning([r for r in rows if r["rate"] != 2.0], config, ["2026-01"], dataset="d", version=1)
    spec = binning.columns[0]
    assert "0.0" in spec.categories
    categories = tuple(stored if c == "0.0" else c for c in spec.categories)
    binning = dataclasses.replace(
        binning, columns=(BinSpec(spec.column, spec.kind, categories=categories, method=spec.method, counts={}),)
    )
    pure, _ = profile_rows(binning, rows, config, periods, dataset="d", run_id="r")
    frame = spark.createDataFrame(
        [(r["month"], r["rate"], r["seg"]) for r in rows], f"month STRING, rate {kind}, seg {kind}"
    )
    remote, _ = profile_spark(binning, frame, config, periods, dataset="d", run_id="r")
    assert keyed(remote) == keyed(pure)
    got = keyed(pure)
    assert got["2026-01", "", "rate", f"cat:{stored}"] == 4
    assert got["2026-01", "", "rate", "unseen"] == 1
    assert {k[1] for k in got} >= {"", "seg=0.0", "seg=0.5"}
    assert not any("-0.0" in k[1] for k in got)
