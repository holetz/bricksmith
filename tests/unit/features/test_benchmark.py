"""Benchmark harness, offline: seeded data, family specs, reference timings and the markdown report."""

from __future__ import annotations

import pytest

from bricksmith.features import CATALOG, FeatureError
from bricksmith.features.benchmark import (
    ALL,
    BENCH_ENGINES,
    REFERENCE,
    Budget,
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


class Ticker:
    """A fake clock that advances one second per reading."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


def test_last_closing_is_the_final_month_of_the_table() -> None:
    assert Size(1, 1, 1).last_closing.isoformat() == "2000-01-01"
    assert Size(1, 13, 1).last_closing.isoformat() == "2001-01-01"
    assert Size(1, 24, 1).last_closing.isoformat() == "2001-12-01"


def test_progress_reports_each_measurement() -> None:
    lines: list[str] = []
    run_benchmark(None, [Size(4, 3, 1)], [REFERENCE], ["basic", "trend"], progress=lines.append)
    assert lines[0].startswith("[4x3x1] preparing data (12 rows")
    assert [line for line in lines if line.endswith("...")] == [
        "[1/2] 4x3x1 basic reference ...",
        "[2/2] 4x3x1 trend reference ...",
    ]
    assert sum(line.endswith("s") for line in lines) == 2


def test_single_closing_times_one_closing() -> None:
    lines: list[str] = []
    run_benchmark(None, [Size(4, 14, 1)], [REFERENCE], ["basic"], single_closing=True, progress=lines.append)
    assert "as_of 2001-02-01" in lines[0]


def test_budget_stops_between_measurements_and_labels_the_rest() -> None:
    budget = Budget(2.5, clock=Ticker())  # the clock reads 1 at creation, then 2, 3, ...: expired from the 3rd read
    lines: list[str] = []
    timings = run_benchmark(
        None, [Size(3, 3, 1), Size(3, 3, 1)], [REFERENCE], ["basic", "trend"], budget=budget, progress=lines.append
    )
    assert [(t.family, t.size) for t in timings] == [("basic", "3x3x1")]
    assert budget.skipped == [
        "reference/trend/3x3x1",
        "reference/basic/3x3x1",
        "reference/trend/3x3x1",
    ]
    assert budget.note() == "partial: the 2.5 s time budget ran out; 3 measurement(s) not run"
    assert any("not started" in line for line in lines)
    assert lines[-1].startswith("[budget] partial")


def test_a_budget_that_holds_leaves_no_note() -> None:
    budget = Budget(1000.0, clock=Ticker())
    assert len(run_benchmark(None, [Size(3, 3, 1)], [REFERENCE], ["basic"], budget=budget)) == 1
    assert budget.note() is None
    with pytest.raises(FeatureError, match="positive"):
        Budget(0)


def test_engine_names_come_from_the_known_engines() -> None:
    assert BENCH_ENGINES == ("reference", "jvm", "spark", "arrow")
    with pytest.raises(FeatureError, match=r"reference, spark, arrow, jvm; .*\['gpu'\]"):
        run_benchmark(None, [Size(2, 2, 1)], ["gpu"])
    with pytest.raises(FeatureError, match="need a session"):
        run_benchmark(None, [Size(2, 2, 1)], ["jvm"])
