"""The Spark executor against a recording fake: ordering, policies and protection; no Spark needed."""
# ruff: noqa: ARG002, ARG005, EM101, FBT003, PT018, TRY003 - compact fakes and dense assertions in synthetic tests

from __future__ import annotations

from typing import Any

import pytest

from bricksmith.errors import AuthorizationError
from bricksmith.plan import Target
from bricksmith.storage import (
    EmptyInputError,
    SparkTableIO,
    UnsupportedError,
    WriteCheckError,
    WriteSpecError,
)


class Row(dict):  # type: ignore[type-arg]
    def __getitem__(self, key: Any) -> Any:  # noqa: ANN401
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Frame:
    def __init__(self, columns: list[str], rows: list[dict[str, Any]], log: list[str]) -> None:
        self.columns, self.rows, self.log = columns, rows, log

    def limit(self, n: int) -> Frame:
        return Frame(self.columns, self.rows[:n], self.log)

    def collect(self) -> list[Row]:
        return [Row(r) for r in self.rows]

    def select(self, *cols: str) -> Frame:
        return Frame(list(cols), [{c: r[c] for c in cols} for r in self.rows], self.log)

    def distinct(self) -> Frame:
        seen = {tuple(r.items()) for r in self.rows}
        return Frame(self.columns, [dict(t) for t in sorted(seen)], self.log)

    def filter(self, expr: str) -> Frame:
        self.log.append(f"filter {expr}")
        return self

    def createOrReplaceTempView(self, name: str) -> None:  # noqa: N802
        self.log.append("view")


class Result:
    def __init__(self, rows: list[Row]) -> None:
        self.rows = rows

    def collect(self) -> list[Row]:
        return self.rows


class Catalog:
    def __init__(self, spark: Fake) -> None:
        self.spark = spark

    def tableExists(self, name: str) -> bool:  # noqa: N802
        return name in self.spark.tables

    def dropTempView(self, name: str) -> None:  # noqa: N802
        self.spark.log.append("drop")


class Fake:
    def __init__(
        self, tables: dict[str, list[str]] | None = None, counts: dict[str, int] | None = None, fail_on: str = ""
    ) -> None:
        self.tables, self.counts, self.fail_on = tables or {}, counts or {}, fail_on
        self.log: list[str] = []
        self.catalog = Catalog(self)

    def table(self, name: str) -> Frame:
        return Frame(self.tables[name], [{"n": 1}], self.log)

    def sql(self, text: str) -> Result:
        self.log.append("sql " + text.split(" ", 1)[0])
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("[UNSUPPORTED_FEATURE] not supported for this compute\nmore")
        for needle, count in self.counts.items():
            if needle in text:
                return Result([Row(c=count)])
        return Result([Row(version=3)] if text.startswith("DESCRIBE HISTORY") else [Row(c=0)])


def frame(spark: Fake, rows: list[dict[str, Any]] | None = None, columns: list[str] | None = None) -> Frame:
    return Frame(columns or ["id", "v"], [{"id": 1, "v": 2}] if rows is None else rows, spark.log)


def opts(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"write": "merge", "keys": ["id"], "protected": False, "granted": False, **extra}


def mutations(spark: Fake) -> list[str]:
    return [
        e
        for e in spark.log
        if e.split()[0] == "sql" and e.split()[1] in {"MERGE", "INSERT", "CREATE", "RESTORE", "ALTER"}
    ]


def test_protected_target_is_refused_before_any_spark_call() -> None:
    spark = Fake({"t": ["id", "v"]})
    io = SparkTableIO(spark)
    with pytest.raises(AuthorizationError):
        io.write("t", frame(spark), operation="merge", options=opts(protected=True))
    assert spark.log == []
    io.write("t", frame(spark), operation="merge", options=opts(protected=True, granted=True))
    assert mutations(spark)


def test_plan_is_reported_before_the_first_mutation() -> None:
    spark = Fake({"t": ["id", "v"]})
    seen: list[int] = []
    io = SparkTableIO(spark, reporter=lambda plan: seen.append(len(mutations(spark))))
    io.write("t", frame(spark), operation="merge", options=opts())
    assert seen == [0]
    assert io.plans[0].strategy == "merge" and io.results[0].state == "applied"
    assert io.results[0].version_before == 3
    assert "drop" in spark.log


def test_explain_reads_but_never_mutates() -> None:
    spark = Fake({"t": ["id", "v"]})
    plan = SparkTableIO(spark).explain("t", frame(spark), operation="merge", options=opts())
    assert plan.strategy == "merge" and mutations(spark) == []


def test_empty_input_policies() -> None:
    spark = Fake({"t": ["id", "v"]})
    io = SparkTableIO(spark)
    with pytest.raises(EmptyInputError):
        io.write("t", frame(spark, []), operation="merge", options=opts())
    assert mutations(spark) == []
    io.write("t", frame(spark, []), operation="merge", options=opts(on_empty="skip"))
    assert io.results[-1].state == "skipped" and mutations(spark) == []
    io.write("t", frame(spark, []), operation="overwrite", options={"write": "overwrite", "on_empty": "allow_empty"})
    assert io.results[-1].state == "applied" and mutations(spark)


def test_partition_replacement_from_empty_input_needs_an_explicit_scope() -> None:
    spark = Fake({"t": ["d", "v"]})
    io = SparkTableIO(spark)
    options = {"write": "replace_partitions", "partition_by": ["d"], "on_empty": "allow_empty"}
    with pytest.raises(WriteSpecError, match="explicit partitions"):
        io.write("t", frame(spark, [], ["d", "v"]), operation="replace_partitions", options=options)
    named = {**options, "partitions": [{"d": "x"}]}
    io.write("t", frame(spark, [], ["d", "v"]), operation="replace_partitions", options=named)
    assert any("REPLACE" not in e and e.startswith("sql INSERT") for e in spark.log)


def test_partitions_come_from_the_input_rows() -> None:
    spark = Fake({"t": ["d", "v"]})
    io = SparkTableIO(spark)
    rows = [{"d": "b", "v": 1}, {"d": "a", "v": 2}, {"d": "a", "v": 3}]
    io.write(
        "t",
        frame(spark, rows, ["d", "v"]),
        operation="replace_partitions",
        options={"write": "replace_partitions", "partition_by": ["d"]},
    )
    sql = next(s.sql for s in io.plans[0].steps if s.kind == "mutate")
    assert "WHERE (`d` = 'a') OR (`d` = 'b')" in sql


def test_failed_pre_check_blocks_every_mutation_and_post_check_names_the_recovery() -> None:
    spark = Fake({"t": ["id", "v"]}, counts={"GROUP BY": 2})
    with pytest.raises(WriteCheckError, match="nothing was written"):
        SparkTableIO(spark).write("t", frame(spark), operation="merge", options=opts())
    assert mutations(spark) == [] and "drop" in spark.log
    spark = Fake({"t": ["id", "v"]}, counts={"FROM `t` WHERE NOT": 1})
    with pytest.raises(WriteCheckError, match="already committed"):
        SparkTableIO(spark).write("t", frame(spark), operation="merge", options=opts(target_checks=["v > 0"]))


def test_late_input_drop_filters_the_input() -> None:
    spark = Fake({"t": ["id", "d"]})
    window = {"column": "d", "start": "2024-01-02", "end": "2024-01-05"}
    SparkTableIO(spark).write(
        "t", frame(spark, columns=["id", "d"]), operation="merge", options=opts(window=window, late_input="drop")
    )
    assert "filter `d` >= '2024-01-02'" in spark.log


def test_pre_and_post_hooks_wrap_the_mutation() -> None:
    spark = Fake({"t": ["id", "v"]})
    order: list[str] = []
    io = SparkTableIO(
        spark,
        pre_hooks=[lambda e: order.append(f"pre{len(mutations(spark))}")],
        post_hooks=[lambda e: order.append(f"post{len(mutations(spark))}")],
    )
    io.write("t", frame(spark), operation="merge", options=opts())
    assert order == ["pre0", "post1"]


def test_materialized_view_refusal_is_reported_as_unsupported() -> None:
    spark = Fake(fail_on="MATERIALIZED")
    with pytest.raises(UnsupportedError, match="materialized views are not supported"):
        SparkTableIO(spark).write(
            "v", "SELECT 1", operation="materialized_view", options={"write": "materialized_view"}
        )


def test_reading_never_writes_and_a_factory_connects_lazily() -> None:
    spark = Fake({"t": ["id"]})
    calls: list[int] = []

    def factory() -> Fake:
        calls.append(1)
        return spark

    io = SparkTableIO(factory)
    assert calls == []
    io.read("t")
    assert calls == [1] and mutations(spark) == []


def test_target_options_travel_in_the_plan_and_round_trip() -> None:
    target = Target("a.b.c", "x.y.c", ("merge",), False, {"write": "merge", "keys": ["id"]})
    assert Target.from_dict(target.to_dict()) == target
    assert "options" not in Target("a", "b", ("write",)).to_dict()


def test_task_context_hands_metadata_and_protection_state_to_the_io() -> None:
    from bricksmith.context import ExecutionContext  # noqa: PLC0415
    from bricksmith.plan import PlanEntry  # noqa: PLC0415
    from bricksmith.runner import InMemoryTableIO, TaskContext  # noqa: PLC0415

    seen: list[Any] = []

    class Spy(InMemoryTableIO):
        def write(self, physical: str, data: object, *, operation: str, options: Any = None) -> None:  # noqa: ANN401
            seen.append((operation, options))
            super().write(physical, data, operation=operation)

    target = Target("a.b.c", "x.y.c", ("merge",), True, {"write": "merge", "keys": ["id"]})
    entry = PlanEntry("a.b.c", "run", "", "a.b.c", "table", "p.py", "f", {}, (), (), (target,))
    context = TaskContext(entry, ExecutionContext.__new__(ExecutionContext), Spy(), frozenset({("x.y.c", "merge")}))
    context.write("c", object())
    operation, options = seen[0]
    assert operation == "merge" and options["keys"] == ["id"]
    assert options["protected"] is True and options["granted"] is True and options["target"] == "a.b.c"
