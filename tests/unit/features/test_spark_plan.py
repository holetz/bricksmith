"""Native Spark plan, offline: every aggregate has an expression, stages respect dependencies, names are quoted."""

from __future__ import annotations

import re

import pytest

from bricksmith.features import (
    CATALOG,
    CategoricalCount,
    Difference,
    FeatureSpec,
    Ratio,
    UnsupportedAggregateError,
    WindowFeature,
)
from bricksmith.features import spark as engine
from bricksmith.features.spark import AGGREGATES, array_column, literal, percentile_sql, quote, sql_plan

SPEC = FeatureSpec(
    keys=("entity",),
    closing="closing",
    features=(WindowFeature(("x",), (1, 2, 6), (*CATALOG, "p5", "p50")),),
    ratios=(Ratio("ratio", "x_mean_6p", "x"),),
    differences=(Difference("diff", "ratio", "x_sum_2p"),),
    categorical=(CategoricalCount("status", ("it's",), (3,)),),
)


def test_every_catalog_aggregate_has_an_expression() -> None:
    assert set(AGGREGATES) == set(CATALOG)


def test_plan_covers_every_output_in_spec_order() -> None:
    plan = sql_plan(SPEC)
    names = [n for n, _ in (*plan.outputs, *plan.combined)]
    assert sorted(names) == sorted(o.name for o in SPEC.outputs)
    assert [n for n, _ in plan.combined] == ["ratio", "diff"]
    assert plan.sources == ("x",)
    assert all(sql.startswith("CAST(") for _, sql in plan.outputs)


def test_stages_only_reference_earlier_columns() -> None:
    plan = sql_plan(SPEC)
    defined = {"__f_by_lag"}
    for stage in plan.stages:
        for _, sql in stage:
            refs = set(re.findall(r"`(__f_[a-z0-9_]+)`", sql))
            assert refs <= defined, refs - defined
        defined |= {alias for alias, _ in stage}
    for _, sql in plan.outputs:
        assert set(re.findall(r"`(__f_[a-z0-9_]+)`", sql)) <= defined
    assert array_column("x") in {a for a, _ in plan.stages[0]}


def test_short_windows_compile_to_nulls_and_empty_steps() -> None:
    outputs = dict(sql_plan(SPEC).outputs)
    assert outputs["x_tercile_diff_2p"] == "CAST(NULL AS DOUBLE)"
    assert outputs["x_tercile_diff_6p"] != "CAST(NULL AS DOUBLE)"
    stages = {alias: sql for stage in sql_plan(SPEC).stages for alias, sql in stage}
    assert stages["__f_steps_x_1"] == "CAST(array() AS ARRAY<DOUBLE>)"
    assert "sequence(1, 6 - 1)" in stages["__f_steps_x_6"]
    assert outputs["x_skew_6p"].startswith("CAST(IF(`__f_n_x_6` < 3, NULL,")
    assert outputs["x_count_6p"].endswith("AS BIGINT)")


def test_restricted_plans_leave_combined_outputs_out() -> None:
    plan = sql_plan(SPEC, ["x_mean_6p", "status_count_it_s_3p"])
    assert [n for n, _ in plan.outputs] == ["x_mean_6p", "status_count_it_s_3p"]
    assert plan.combined == ()
    assert plan.sources == ()
    assert "'it\\'s'" in dict(plan.outputs)["status_count_it_s_3p"]


def test_quoting_and_percentiles() -> None:
    assert quote("a`b") == "`a``b`"
    assert literal("a\\b'c") == "'a\\\\b\\'c'"
    sql = percentile_sql(90)
    assert "0.9D" in sql
    assert "least(" in sql


def test_an_aggregate_without_an_expression_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(engine.AGGREGATES, "rms")
    spec = FeatureSpec(("e",), "c", (WindowFeature(("x",), (3,), ("rms",)),))
    with pytest.raises(UnsupportedAggregateError, match="arrow"):
        sql_plan(spec)


def test_period_expressions() -> None:
    assert set(engine.PERIOD_SQL) == {"month", "quarter", "week", "day"}
    assert "1970-01-05" in engine.PERIOD_SQL["week"]
