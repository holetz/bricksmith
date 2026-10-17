"""Quality on the selected workspace: Spark comparison, checks, and check tasks / write conditions in the runner."""
# ruff: noqa: E501, PT018 - dense assertions and synthetic SQL in remote tests

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import textwrap
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.graph import load_graph
from bricksmith.plan import build_plan
from bricksmith.quality import (
    Check,
    GatePolicy,
    Tolerance,
    build_regression_report,
    compare,
    compare_rows,
    condition_sql,
    plan_for_frames,
    run_check,
)
from bricksmith.runner import apply_plan
from bricksmith.storage import SparkTableIO, WriteCheckError, current_version
from bricksmith.storage.sql import quote

if TYPE_CHECKING:
    from pathlib import Path

    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

DDL = "id INT, name STRING, score DOUBLE"
LEFT = [(1, "a", 1.0), (2, "b", 2.0), (3, "c", None), (4, None, 4.0)]
RIGHT = [(1, "a", 1.0), (2, "B", 2.5), (3, "c", None), (5, "e", 5.0)]


class Env:
    """Spark session and naming for one test-owned schema."""

    def __init__(self, spark: Any, schema: str, catalog: str) -> None:  # noqa: ANN401
        self.spark, self.schema, self.catalog = spark, schema, catalog

    def name(self, table: str) -> str:
        return f"{self.catalog}.{self.schema}.{table}"

    def table(self, table: str, rows: list[tuple[Any, ...]], ddl: str = DDL) -> Any:  # noqa: ANN401
        self.spark.createDataFrame(rows, ddl).write.mode("overwrite").saveAsTable(self.name(table))
        return self.spark.table(self.name(table))


@pytest.fixture
def env(remote_context: ExecutionContext, remote_clients: Clients, test_schema: str) -> Env:
    return Env(remote_clients.spark(), test_schema, remote_context.catalog or "")


def counts(report: Any) -> tuple[Any, ...]:  # noqa: ANN401
    return (
        report.status,
        report.missing_in_right,
        report.extra_in_right,
        report.matched_keys,
        report.rows_with_differences,
        {c.name: c.mismatches for c in report.columns},
    )


def test_comparison_counts_tolerances_and_samples_match_hand_values(env: Env) -> None:
    left, right = env.table("l", LEFT), env.table("r", RIGHT)
    plan = plan_for_frames(left, right, ["id"], left_name="l", right_name="r")
    assert plan.executable and [c.name for c in plan.columns] == ["name", "score"]
    report = compare(left, right, plan)
    assert counts(report) == (
        "different",
        1,
        1,
        3,
        1,
        {"name": 1, "score": 1},
    )  # hand-derived: id 4 missing, 5 extra, id 2 changed
    assert report.samples == {} and (report.left.rows, report.right.rows) == (4, 4)  # type: ignore[union-attr]
    # The pure reference and Spark agree on the same plan (both are complete reports).
    names = ["id", "name", "score"]
    rows_l: list[dict[str, object]] = [dict(zip(names, r, strict=True)) for r in LEFT]
    rows_r: list[dict[str, object]] = [dict(zip(names, r, strict=True)) for r in RIGHT]
    assert compare_rows(rows_l, rows_r, plan).to_dict() == report.to_dict()
    # Tolerance exactly at the boundary matches (2.0 vs 2.5, absolute 0.5); a smaller one does not.
    at = plan_for_frames(left, right, ["id"], tolerances={"score": Tolerance(absolute=0.5)})
    assert {c.name: c.mismatches for c in compare(left, right, at).columns}["score"] == 0
    beyond = plan_for_frames(left, right, ["id"], tolerances={"score": Tolerance(absolute=0.25)})
    assert {c.name: c.mismatches for c in compare(left, right, beyond).columns}["score"] == 1
    ints = env.table("i1", [(1, 3)], "id INT, v INT"), env.table("i2", [(1, 4)], "id INT, v INT")
    rel = plan_for_frames(ints[0], ints[1], ["id"], tolerances={"v": Tolerance(relative=0.25)})  # allowed 0.25 * 4 = 1
    assert compare(*ints, rel).rows_with_differences == 0
    over = plan_for_frames(ints[0], ints[1], ["id"], tolerances={"v": Tolerance(relative=0.2)})  # allowed 0.8 < 1
    assert compare(*ints, over).rows_with_differences == 1
    # Samples are bounded and only present when requested.
    many_l = env.table("ml", [(i, "x", 0.0) for i in range(1, 6)])
    many_r = env.table("mr", [(i, "y", 0.0) for i in range(1, 6)] + [(8, "z", 0.0), (9, "z", 0.0)])
    plan2 = plan_for_frames(many_l, many_r, ["id"])
    full = compare(many_l, many_r, plan2, sample_limit=2)
    assert full.columns[0].mismatches == 5 and full.extra_in_right == 2
    assert len(full.samples["mismatch:name"]) == 2 and len(full.samples["extra_in_right"]) == 2
    assert full.samples["mismatch:name"][0] == {"key": {"id": 1}, "left": "x", "right": "y"}
    assert compare(many_l, many_r, plan2, sample_limit=1).samples["extra_in_right"] == [{"id": 8}]
    assert compare(many_l, many_r, plan2).samples == {}
    # Identical and empty datasets.
    assert compare(left, left, plan).status == "identical"
    empty = env.table("e1", [], DDL), env.table("e2", [], DDL)
    assert counts(compare(*empty, plan)) == ("identical", 0, 0, 0, 0, {"name": 0, "score": 0})
    assert compare(left, empty[0], plan).missing_in_right == 4


def test_duplicate_null_keys_and_unsupported_types(env: Env) -> None:
    left = env.table("l", [(1, "a", 1.0), (1, "a", 1.0), (2, "b", 2.0)])
    right = env.table("r", [(1, "a", 1.0), (2, "b", 2.0), (None, "n", 0.0)])
    plan = plan_for_frames(left, right, ["id"])
    report = compare(left, right, plan)
    assert report.status == "invalid" and report.matched_keys == 0 and report.columns == ()
    assert (report.left.duplicate_key_groups, report.left.duplicate_key_rows, report.left.null_key_rows) == (1, 2, 0)  # type: ignore[union-attr]
    assert report.right.null_key_rows == 1 and report.right.duplicate_key_groups == 0  # type: ignore[union-attr]
    assert (
        build_regression_report("dup", comparisons={"t": report}, policy=GatePolicy(max_differing_rows=99)).verdict
        == "fail"
    )
    typed_l = env.spark.sql("SELECT 1 AS id, map('a', 1) AS m, X'0A' AS b, 'x' AS s, array(1, 2) AS arr")
    typed_r = env.spark.sql("SELECT 1 AS id, map('a', 1) AS m, X'0A' AS b, 'y' AS s, array(1, 2) AS arr")
    auto = plan_for_frames(typed_l, typed_r, ["id"])
    assert [c.name for c in auto.columns] == ["arr", "s"]
    assert {(d.code, d.column, d.severity) for d in auto.diagnostics} == {
        ("unsupported_type", "m", "warning"),
        ("unsupported_type", "b", "warning"),
    }
    done = compare(typed_l, typed_r, auto)
    assert {c.name: c.mismatches for c in done.columns} == {"arr": 0, "s": 1}
    blocked = plan_for_frames(typed_l, typed_r, ["id"], columns=["s", "m"])
    assert not blocked.executable and compare(typed_l, typed_r, blocked).status == "invalid"


def test_every_check_kind_on_spark(env: Env) -> None:
    rows = [
        (1, "a", dt.datetime(2026, 1, 1, tzinfo=dt.UTC), 1),
        (2, "b", dt.datetime(2025, 12, 31, tzinfo=dt.UTC), None),
        (3, "z", None, None),
    ]
    data = env.table("d", rows, "id INT, kind STRING, seen TIMESTAMP, opt INT")
    now = dt.datetime(2026, 1, 2, tzinfo=dt.UTC)
    assert run_check(Check.grain("id"), data).status == "passed"
    dup = env.table("dup", [(1, "a"), (1, "b"), (None, "c")], "id INT, kind STRING")
    result = run_check(Check.grain("id"), dup, sample_limit=3)
    assert result.status == "failed" and result.observed == {
        "duplicate_groups": 1,
        "duplicate_rows": 2,
        "null_key_rows": 1,
        "rows": 3,
    }
    assert result.samples == ({"id": None}, {"id": 1}) and run_check(Check.grain("id"), dup).samples == ()
    other = env.table("o", [(1,), (2,), (2,)], "id INT")
    assert run_check(Check.cardinality("id", expect="one_to_many"), data, other).status == "passed"
    assert run_check(Check.cardinality("id"), data, other).status == "failed"
    miss = run_check(Check.cardinality("id", expect="one_to_many", require_match=True), data, other, sample_limit=2)
    assert miss.status == "failed" and miss.observed["unmatched_rows"] == 1 and miss.samples == ({"id": 3},)
    failed = run_check(Check.not_null("opt", "id"), data)
    assert failed.status == "failed" and failed.observed["nulls"] == {"opt": 2, "id": 0}
    accepted = run_check(Check.accepted_values("kind", ["a", "b"]), data, sample_limit=3)
    assert accepted.status == "failed" and accepted.observed["invalid_rows"] == 1 and accepted.samples == ("z",)
    assert run_check(Check.accepted_values("opt", [1]), data).observed["invalid_rows"] == 2
    assert run_check(Check.accepted_values("opt", [1], allow_null=True), data).status == "passed"
    assert run_check(Check.completeness("opt"), data).status == "failed"
    assert run_check(Check.completeness("opt", min_ratio=1 / 3), data).status == "passed"
    day = 86400.0
    assert run_check(Check.freshness("seen", max_age_seconds=day), data, now=now).status == "passed"
    assert run_check(Check.freshness("seen", max_age_seconds=day - 1), data, now=now).status == "failed"
    assert run_check(Check.freshness("kind", max_age_seconds=1), data, now=now).status == "unavailable"
    empty = env.table("e", [], "id INT, seen TIMESTAMP")
    assert run_check(Check.completeness("id"), empty).status == "unavailable"
    assert run_check(Check.freshness("seen", max_age_seconds=1), empty, now=now).status == "unavailable"
    assert run_check(Check.grain("nope"), data).status == "unavailable"


def test_spark_matches_row_engine_on_null_semantics_precision_and_samples(env: Env) -> None:
    ddl = "v INT, seen TIMESTAMP, d DATE"
    ts = dt.datetime(2026, 1, 1, 12, 0, 0, 900000, tzinfo=dt.UTC)
    day = dt.date(2026, 1, 2)
    raw = [(1, ts, day), (None, ts, day), (2, ts, day)]
    frame = env.table("par", raw, ddl)
    rows = [{"v": v, "seen": s, "d": d} for v, s, d in raw]
    now = dt.datetime(2026, 1, 1, 12, 0, 1, tzinfo=dt.UTC)
    day_now = dt.datetime(2026, 1, 2, 12, 0, tzinfo=dt.UTC)
    cases: list[tuple[Check, dt.datetime | None]] = [
        (Check.accepted_values("v", [1, None], allow_null=True), None),
        (Check.accepted_values("v", [1, 2], allow_null=True), None),
        (Check.accepted_values("v", [1, 2]), None),
        (Check.freshness("seen", max_age_seconds=0.2), now),
        (Check.freshness("seen", max_age_seconds=0.1), now),
        (Check.freshness("seen", max_age_seconds=0.09), now),
        (Check.freshness("d", max_age_seconds=12 * 3600), day_now),
        (Check.freshness("d", max_age_seconds=12 * 3600 - 1), day_now),
    ]
    for check, at in cases:
        spark = run_check(check, frame, sample_limit=5, now=at)
        local = run_check(check, rows, sample_limit=5, now=at)
        assert (spark.status, spark.observed.get("invalid_rows"), spark.samples) == (
            local.status,
            local.observed.get("invalid_rows"),
            local.samples,
        ), check.name
    empty = env.table("par_e", [], ddl)
    allnull = env.table("par_n", [(1, None, None)], ddl)
    for target in (empty, allnull):
        assert run_check(Check.freshness("seen", max_age_seconds=1), target, now=now).status == "unavailable"
    keys = [(1, "x"), (1, "x"), (None, "y"), (2, None), (2, None), (None, None)]
    kframe = env.table("par_k", keys, "a INT, b STRING")
    krows = [{"a": a, "b": b} for a, b in keys]
    for limit in (None, 2, 10):
        spark = run_check(Check.grain("a", "b"), kframe, sample_limit=limit)
        local = run_check(Check.grain("a", "b"), krows, sample_limit=limit)
        assert (spark.status, dict(spark.observed), spark.samples) == (
            local.status,
            dict(local.observed),
            local.samples,
        )
    nullonly = env.table("par_o", [(1,), (None,), (None,)], "a INT")
    assert run_check(Check.grain("a"), nullonly, sample_limit=3).samples == ({"a": None},)


def _project(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


def test_check_task_and_write_conditions_run_through_the_runner(
    env: Env, remote_context: ExecutionContext, tmp_path: Path
) -> None:
    target = quote(env.name("orders"))
    input_checks = [
        condition_sql(Check.not_null("id")),
        condition_sql(Check.accepted_values("kind", ["a", "b"])),
        condition_sql(Check.grain("id")),
    ]
    target_checks = [condition_sql(Check.grain("id"), relation=target)]
    files = {
        "bricksmith.toml": "",
        "catalogs/lg/stage/seed.py": f"""
            from bricksmith.tasks import task

            @task(kind="table", outputs="orders", metadata={{"write": "overwrite", "input_checks": {json.dumps(input_checks)}, "target_checks": {json.dumps(target_checks)}}})
            def seed(ctx):
                ctx.write("orders", ctx.io.spark.createDataFrame([(1, "a"), (2, "b")], "id INT, kind STRING"))
        """,
        "catalogs/lg/stage/check_orders.py": """
            from bricksmith.quality import Check, require_check
            from bricksmith.tasks import task

            @task(kind="check")
            def check_orders(ctx):
                require_check(Check.grain("id"), ctx.read("orders"))
                require_check(Check.accepted_values("kind", ["a", "b"]), ctx.read("orders"))
        """,
    }
    _project(tmp_path, files)
    context = dataclasses.replace(remote_context, catalogs={"lg": env.catalog}, schemas={"stage": env.schema})
    io = SparkTableIO(env.spark)
    graph = load_graph(tmp_path, context=context)
    plan = build_plan(graph)
    assert not plan.errors
    report = apply_plan(plan, graph, project_root=tmp_path, execution=context, table_io=io)
    assert report.ok, report.to_json()
    assert sorted(report.ids("succeeded")) == ["lg.stage.check_orders", "lg.stage.seed"]
    version = current_version(env.spark, env.name("orders"))
    # A violating batch is refused by the write conditions before anything is committed.
    bad = [(1, "a"), (1, "b")]
    for rows, message in ((bad, "grain"), ([(1, "zzz")], "kind"), ([(None, "a")], "IS NOT NULL")):
        frame = env.spark.createDataFrame(rows, "id INT, kind STRING")
        with pytest.raises(WriteCheckError, match="check failed"):
            io.write(
                env.name("orders"),
                frame,
                operation="overwrite",
                options={"write": "overwrite", "input_checks": input_checks, "target_checks": target_checks},
            )
        assert current_version(env.spark, env.name("orders")) == version, message
    # A failing check task fails in the runner with the quality error code and gates the run.
    env.spark.createDataFrame([(1, "a"), (1, "q")], "id INT, kind STRING").write.mode("overwrite").saveAsTable(
        env.name("orders")
    )
    failing = apply_plan(
        build_plan(graph, roots=["lg.stage.check_orders"], upstream=False, partial=True),
        graph,
        project_root=tmp_path,
        execution=context,
        table_io=io,
    )
    assert not failing.ok
    result = next(r for r in failing.results if r.task_id == "lg.stage.check_orders")
    assert result.state == "failed" and result.error is not None and "check" in json.dumps(dict(result.error))
