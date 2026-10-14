"""Storage strategies on the selected workspace with tiny synthetic tables in a test-owned schema."""
# ruff: noqa: ARG005, E501, PT017, PT018, S608 - compact fakes and dense assertions in synthetic tests

from __future__ import annotations

import sys
import textwrap
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.errors import AuthorizationError
from bricksmith.graph import load_graph
from bricksmith.plan import Authorization, build_plan
from bricksmith.registry.model import parse_registry
from bricksmith.runner import apply_plan
from bricksmith.storage import (
    EmptyInputError,
    SparkTableIO,
    UnsupportedError,
    WriteCheckError,
    current_version,
    parse_write_spec,
    plan_restore,
    plan_retention,
    run_plan,
    scd2_violations,
    table_history,
)

if TYPE_CHECKING:
    from pathlib import Path

    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext


class Env:
    """Spark session, table I/O and naming for one test-owned schema."""

    def __init__(self, spark: Any, schema: str, catalog: str) -> None:  # noqa: ANN401
        self.spark, self.schema, self.catalog = spark, schema, catalog
        self.io = SparkTableIO(spark)

    def name(self, table: str) -> str:
        return f"{self.catalog}.{self.schema}.{table}"

    def frame(self, rows: list[tuple[Any, ...]], ddl: str) -> Any:  # noqa: ANN401
        return self.spark.createDataFrame(rows, ddl)

    def write(self, table: str, rows: list[tuple[Any, ...]], ddl: str, strategy: str, **options: Any) -> None:  # noqa: ANN401
        opts = {"write": strategy, **options}
        self.io.write(self.name(table), self.frame(rows, ddl), operation=strategy, options=opts)

    def rows(self, table: str, columns: str = "*") -> list[tuple[Any, ...]]:
        return sorted(
            (tuple(r) for r in self.spark.sql(f"SELECT {columns} FROM {self.name(table)}").collect()), key=repr
        )

    def version(self, table: str) -> int | None:
        return current_version(self.spark, self.name(table))


@pytest.fixture
def env(remote_context: ExecutionContext, remote_clients: Clients, test_schema: str) -> Env:
    return Env(remote_clients.spark(), test_schema, remote_context.catalog or "")


def test_full_and_static_refresh_with_empty_input_and_protection_policies(env: Env) -> None:
    ddl = "id INT, v STRING"
    for _ in range(2):  # repeated runs keep the documented result
        env.write("full", [(1, "a"), (2, "b")], ddl, "overwrite")
        assert env.rows("full") == [(1, "a"), (2, "b")]
    env.write("full", [(3, "c")], ddl, "overwrite")
    assert env.rows("full") == [(3, "c")]
    before = env.version("full")
    with pytest.raises(EmptyInputError):
        env.write("full", [], ddl, "overwrite")
    env.write("full", [], ddl, "overwrite", on_empty="skip")
    assert env.version("full") == before and env.rows("full") == [(3, "c")]
    env.write("full", [], ddl, "overwrite", on_empty="allow_empty")
    assert env.rows("full") == []

    env.write("static", [(1, "a")], ddl, "static_refresh")
    env.write("static", [(9, "z")], ddl, "static_refresh")
    assert env.rows("static") == [(1, "a")] and env.io.results[-1].state == "skipped"
    env.write("static", [(9, "z")], ddl, "static_refresh", force=True)
    assert env.rows("static") == [(9, "z")]

    # Protection is evaluated before the table is read or touched.
    version = env.version("static")
    with pytest.raises(AuthorizationError):
        env.io.write(
            env.name("static"),
            env.frame([(5, "q")], ddl),
            operation="overwrite",
            options={"write": "overwrite", "protected": True, "granted": False},
        )
    assert env.version("static") == version and env.rows("static") == [(9, "z")]
    env.io.write(
        env.name("static"),
        env.frame([(5, "q")], ddl),
        operation="overwrite",
        options={"write": "overwrite", "protected": True, "granted": True},
    )
    assert env.rows("static") == [(5, "q")]
    assert env.io.plans[-1].protected and env.io.plans[-1].granted


def test_partition_replacement_leaves_neighbours_untouched(env: Env) -> None:
    ddl = "d STRING, v INT"
    options = {"partition_by": ["d"]}
    env.write("parts", [("d1", 1), ("d2", 2), ("d2", 3), ("d3", 4)], ddl, "replace_partitions", **options)
    assert env.rows("parts") == [("d1", 1), ("d2", 2), ("d2", 3), ("d3", 4)]
    for _ in range(2):  # reprocessing d2 twice gives the same result
        env.write("parts", [("d2", 99)], ddl, "replace_partitions", **options)
        assert env.rows("parts") == [("d1", 1), ("d2", 99), ("d3", 4)]
    env.write("parts", [], ddl, "replace_partitions", partitions=[{"d": "d3"}], on_empty="allow_empty", **options)
    assert env.rows("parts") == [("d1", 1), ("d2", 99)]

    window = {"column": "d", "start": "d2", "end": "d3"}
    with pytest.raises(WriteCheckError):  # late input before the window
        env.write("parts", [("d1", 7), ("d2", 8)], ddl, "replace_partitions", window=window)
    with pytest.raises(WriteCheckError):  # input at the window end
        env.write("parts", [("d3", 7)], ddl, "replace_partitions", window=window)
    env.write("parts", [("d1", 7), ("d2", 8)], ddl, "replace_partitions", window=window, late_input="drop")
    assert env.rows("parts") == [("d1", 1), ("d2", 8)]


def test_incremental_merge_and_append_are_idempotent(env: Env) -> None:
    ddl = "id INT, v STRING"
    for _ in range(2):
        env.write("m", [(1, "a"), (2, "b")], ddl, "merge", keys=["id"])
    for _ in range(2):
        env.write("m", [(2, "B"), (3, "c")], ddl, "merge", keys=["id"])
        assert env.rows("m") == [(1, "a"), (2, "B"), (3, "c")]
    for _ in range(2):  # keyed append only inserts absent keys
        env.write("m", [(1, "ignored"), (4, "d")], ddl, "append", keys=["id"])
        assert env.rows("m") == [(1, "a"), (2, "B"), (3, "c"), (4, "d")]
    before = env.version("m")
    with pytest.raises(WriteCheckError):
        env.write("m", [(5, "x"), (5, "y")], ddl, "merge", keys=["id"])
    with pytest.raises(WriteCheckError):
        env.write("m", [(None, "x")], ddl, "merge", keys=["id"])
    assert env.version("m") == before

    window = {"column": "d", "start": "2024-01-02", "end": "2024-01-04"}
    wddl = "id INT, d STRING"
    env.write("w", [(1, "2024-01-02")], wddl, "merge", keys=["id"], window=window)
    with pytest.raises(WriteCheckError):
        env.write("w", [(2, "2024-01-01")], wddl, "merge", keys=["id"], window=window)
    env.write("w", [(2, "2024-01-01"), (3, "2024-01-03")], wddl, "merge", keys=["id"], window=window, late_input="drop")
    env.write("w", [(4, "2024-01-01")], wddl, "merge", keys=["id"], window=window, late_input="accept")
    assert env.rows("w", "id") == [(1,), (3,), (4,)]
    env.write("loose", [(1, "a")], ddl, "append", allow_duplicates=True)
    env.write("loose", [(1, "a")], ddl, "append", allow_duplicates=True)
    assert env.rows("loose") == [(1, "a"), (1, "a")]  # documented: not idempotent


def test_scd2_invariants_and_idempotent_reruns(env: Env) -> None:
    ddl = "id INT, name STRING, at INT"
    options = {"keys": ["id"], "tracked": ["name"], "effective": "at"}
    spec = parse_write_spec({"write": "scd2", **options}, "scd2")
    cols = "id, name, valid_from, valid_to, is_current"
    first, second = [(1, "a", 10), (2, "b", 10)], [(1, "a2", 20), (2, "b", 20), (3, "c", 20)]
    for _ in range(2):
        env.write("h", first, ddl, "scd2", **options)
    assert env.rows("h", cols) == [(1, "a", 10, None, True), (2, "b", 10, None, True)]
    for _ in range(2):
        env.write("h", second, ddl, "scd2", **options)
        assert env.rows("h", cols) == [
            (1, "a", 10, 20, False),
            (1, "a2", 20, None, True),
            (2, "b", 10, None, True),
            (3, "c", 20, None, True),
        ]
    with pytest.raises(WriteCheckError, match="backdated"):  # replaying an older batch is rejected
        env.write("h", first, ddl, "scd2", **options)
    env.write("h", first, ddl, "scd2", late_input="drop", **options)  # or explicitly dropped: no row changes
    version = env.version("h")
    with pytest.raises(WriteCheckError, match="conflicting"):
        env.write("h", [(1, "zzz", 20)], ddl, "scd2", **options)
    assert env.version("h") == version  # a rejected batch commits nothing
    rows = [
        dict(zip(["id", "name", "valid_from", "valid_to", "is_current"], r, strict=True)) for r in env.rows("h", cols)
    ]
    assert scd2_violations(rows, spec) == []


def test_views_functions_and_materialized_views(env: Env) -> None:
    env.write("base", [(1, "a")], "id INT, v STRING", "overwrite")
    for _ in range(2):
        env.io.write(env.name("vw"), f"SELECT id FROM {env.name('base')}", operation="view", options={"write": "view"})
    assert env.rows("vw") == [(1,)]
    options = {"write": "function", "params": "x INT", "returns": "INT"}
    env.io.write(env.name("plus_one"), "x + 1", operation="function", options=options)
    assert env.spark.sql(f"SELECT {env.name('plus_one')}(41)").collect()[0][0] == 42
    # Materialized views need a pipeline-capable compute: either created or reported as unsupported.
    try:
        env.io.write(
            env.name("mv"), "SELECT 1 AS a", operation="materialized_view", options={"write": "materialized_view"}
        )
    except UnsupportedError as error:
        assert "not supported" in str(error)
        sys.stderr.write(f"MATERIALIZED_VIEW: unsupported: {str(error)[:160]}\n")
    else:
        assert env.rows("mv") == [(1,)]
        sys.stderr.write("MATERIALIZED_VIEW: created\n")
    # A read never writes: the plan lists no mutation and reading leaves the version alone.
    version = env.version("base")
    env.spark.table(env.name("base")).collect()
    assert env.version("base") == version


def test_history_restore_retention_and_hooks(env: Env) -> None:
    ddl = "id INT, v STRING"
    seen: list[str] = []
    env.io.pre_hooks = (lambda e: seen.append("pre"),)
    env.io.post_hooks = (lambda e: seen.append("post"),)
    env.write("r", [(1, "a")], ddl, "overwrite")
    first = env.version("r")
    assert first is not None
    env.write("r", [(2, "b")], ddl, "overwrite", target_checks=["id > 0"], input_checks=["id > 0"])
    assert seen == ["pre", "post", "pre", "post"]
    assert env.io.results[-1].version_before == first
    with pytest.raises(WriteCheckError, match="already committed"):  # post check fails after the commit
        env.write("r", [(3, "c")], ddl, "overwrite", target_checks=["id > 100"])
    assert env.rows("r") == [(3, "c")]
    plan = plan_restore(env.name("r"), version=first)
    assert plan.mutating_steps[0].sql.startswith("RESTORE TABLE")
    run_plan(env.spark, plan)  # the documented recovery: restore to the recorded version_before
    assert env.rows("r") == [(1, "a")]
    assert table_history(env.spark, env.name("r"), 3)[0]["operation"] == "RESTORE"
    run_plan(env.spark, plan_retention(env.name("r"), log_days=30, deleted_file_days=7))
    props = {row["key"]: row["value"] for row in env.spark.sql(f"SHOW TBLPROPERTIES {env.name('r')}").collect()}
    assert props["delta.logRetentionDuration"] == "interval 30 days"


def test_registry_metadata_is_applied_on_write_and_keeps_unrelated_metadata(env: Env) -> None:
    logical = "main.sales.orders"
    registry = parse_registry(
        {
            "version": 1,
            "columns": [{"name": "id", "type": "int", "description": "Order identifier", "sensitivity": "public"}],
            "tables": {logical: {"description": "Synthetic orders", "grain": ["id"], "columns": {"id": {}}}},
        }
    )
    env.io.registry, env.io.registry_tables = registry, {env.name("orders"): logical}
    env.write("orders", [(1, "a")], "id INT, note STRING", "overwrite")
    env.spark.sql(f"COMMENT ON COLUMN {env.name('orders')}.note IS 'kept note'")
    env.spark.sql(f"ALTER TABLE {env.name('orders')} SET TBLPROPERTIES ('team.owner' = 'x')")
    for _ in range(2):
        env.write("orders", [(1, "a")], "id INT, note STRING", "overwrite")
    frame = env.spark.table(env.name("orders"))
    comments = {f.name: f.metadata.get("comment") for f in frame.schema.fields}
    assert comments == {"id": "Order identifier", "note": "kept note"}
    props = {row["key"]: row["value"] for row in env.spark.sql(f"SHOW TBLPROPERTIES {env.name('orders')}").collect()}
    assert props["team.owner"] == "x" and props["bricksmith.grain"] == "id"
    assert props["bricksmith.sensitivity.id"] == "public"
    rows = env.spark.sql(f"DESCRIBE TABLE EXTENDED {env.name('orders')}").collect()
    assert next(r["data_type"] for r in rows if r["col_name"] == "Comment") == "Synthetic orders"


FILES = {
    "bricksmith.toml": "",
    "catalogs/lg/stage/seed.py": """
        from bricksmith.tasks import task

        @task(kind="table", outputs="orders", metadata={"write": "overwrite"})
        def seed(ctx):
            frame = ctx.io.spark.createDataFrame([(1, "x", 5), (2, "x", 7), (3, "y", 1)], "id INT, customer STRING, amount INT")
            ctx.write("orders", frame)
    """,
    "catalogs/lg/stage/totals.py": """
        from bricksmith.tasks import task

        @task(kind="table", outputs="totals", metadata={"write": "merge", "keys": ["customer"], "protected": True})
        def totals(ctx):
            orders = ctx.read("orders")
            ctx.write("totals", orders.groupBy("customer").sum("amount").withColumnRenamed("sum(amount)", "total"))
    """,
}


def test_runner_applies_a_two_task_project_to_delta_tables(
    env: Env, remote_context: ExecutionContext, tmp_path: Path
) -> None:
    for rel, body in FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    import dataclasses  # noqa: PLC0415

    context = dataclasses.replace(remote_context, catalogs={"lg": env.catalog}, schemas={"stage": env.schema})
    graph = load_graph(tmp_path, context=context)
    totals = next(o.physical for o in graph.outputs if o.name.endswith(".totals"))
    plan = build_plan(graph, roots=["lg.stage.totals"])
    assert not plan.errors
    with pytest.raises(Exception, match="authoriz"):  # protected output is not applied without its own grant
        apply_plan(plan, graph, project_root=tmp_path, execution=context, table_io=env.io)
    assert env.io.plans == []
    granted = plan.authorize(Authorization(totals, ("merge",)))
    for _ in range(2):  # the second apply is a repeat run: same result
        report = apply_plan(granted, graph, project_root=tmp_path, execution=context, table_io=env.io)
        assert report.ok, report.to_json()
        assert env.rows("orders") == [(1, "x", 5), (2, "x", 7), (3, "y", 1)]
        assert env.rows("totals") == [("x", 12), ("y", 1)]
    assert env.spark.sql(f"DESCRIBE DETAIL {env.name('totals')}").collect()[0]["format"] == "delta"
