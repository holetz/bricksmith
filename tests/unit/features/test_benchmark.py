"""Benchmark harness, offline: seeded data, family specs, reference timings and the markdown report."""

from __future__ import annotations

import pytest

from bricksmith.features import CATALOG, FeatureError
from bricksmith.features.benchmark import (
    ALL,
    REFERENCE,
    Size,
    Timing,
    benchmark_spec,
    render_report,
    run_benchmark,
    synthetic_rows,
    value_columns,
)
from bricksmith.features.catalog import FAMILIES


def test_size_parsing() -> None:
    size = Size.parse("30X12x2")
    assert (size.entities, size.closings, size.columns, size.rows, size.label) == (30, 12, 2, 360, "30x12x2")
    with pytest.raises(FeatureError, match="entities"):
        Size.parse("30x12")


def test_synthetic_rows_are_seeded_and_have_nulls() -> None:
    size = Size(20, 12, 3)
    rows = synthetic_rows(size)
    assert rows == synthetic_rows(size)
    assert len(rows) == size.rows
    assert set(rows[0]) == {"entity", "closing", *value_columns(size)}
    assert any(r["v0"] is None for r in rows)
    assert rows != synthetic_rows(Size(20, 12, 3, seed=8))


def test_family_specs_cover_the_catalog() -> None:
    names = {o.aggregate for family in FAMILIES for o in benchmark_spec(["v0"], family, (3,)).outputs}
    assert names >= set(CATALOG)
    assert {o.aggregate for o in benchmark_spec(["v0"], ALL, (3,)).outputs} == names
    with pytest.raises(FeatureError, match="family"):
        benchmark_spec(["v0"], "shapes")


def test_reference_run_and_report() -> None:
    timings = run_benchmark(None, [Size(5, 6, 1), Size(10, 6, 1)], [REFERENCE], ["trend"])
    assert [(t.engine, t.family, t.size) for t in timings] == [
        (REFERENCE, "trend", "5x6x1"),
        (REFERENCE, "trend", "10x6x1"),
    ]
    assert all(t.seconds >= 0 and t.outputs == 15 for t in timings)
    report = render_report([*timings, Timing("spark", "trend", "5x6x1", 1.5, 15)], ["note"])
    lines = report.splitlines()
    assert lines[0] == "- note"
    assert lines[2] == "| Family | Engine | Outputs | 5x6x1 (s) | 10x6x1 (s) |"
    assert lines[-1] == "| trend | spark | 15 | 1.50 | - |"


def test_spark_engines_need_a_session() -> None:
    with pytest.raises(FeatureError, match="session"):
        run_benchmark(None, [Size(2, 2, 1)], ["spark"])
    with pytest.raises(FeatureError, match="unknown"):
        run_benchmark(None, [Size(2, 2, 1)], ["gpu"])
