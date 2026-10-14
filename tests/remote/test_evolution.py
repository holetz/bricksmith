"""Schema evolution and maintenance on the selected workspace with tiny synthetic tables."""
# ruff: noqa: PT018, S608 - dense assertions and SQL over test-owned tables

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.evolution import (
    EvolutionPolicy,
    MigrationBlockedError,
    TableSchema,
    migrate,
    parse_maintenance,
    plan_maintenance,
    plan_migration,
    read_live,
    run_maintenance,
)
from bricksmith.storage import SparkTableIO

if TYPE_CHECKING:
    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext


class Env:
    """Spark session and naming for one test-owned schema."""

    def __init__(self, spark: Any, schema: str, catalog: str) -> None:  # noqa: ANN401
        self.spark, self.schema, self.catalog = spark, schema, catalog

    def name(self, table: str) -> str:
        return f"{self.catalog}.{self.schema}.{table}"

    def rows(self, table: str, columns: str = "*") -> list[tuple[Any, ...]]:
        return sorted(
            (tuple(r) for r in self.spark.sql(f"SELECT {columns} FROM {self.name(table)}").collect()), key=repr
        )

    def operations(self, table: str) -> list[str]:
        return [r["operation"] for r in self.spark.sql(f"DESCRIBE HISTORY {self.name(table)}").collect()]

    def create(self, table: str, ddl: str, rows: str, *extra: str) -> str:
        name = self.name(table)
        self.spark.sql(f"CREATE TABLE {name} ({ddl}) COMMENT 'synthetic table'")
        for stmt in extra:
            self.spark.sql(stmt.format(t=name))
        self.spark.sql(f"INSERT INTO {name} VALUES {rows}")
        return name


@pytest.fixture
def env(remote_context: ExecutionContext, remote_clients: Clients, test_schema: str) -> Env:
    return Env(remote_clients.spark(), test_schema, remote_context.catalog or "")


def declared(**data: Any) -> TableSchema:  # noqa: ANN401
    return TableSchema.from_dict(data)


def test_compatible_add_widen_and_comment_apply_then_rerun_is_a_noop(env: Env) -> None:
    name = env.create(
        "orders",
        "id INT NOT NULL COMMENT 'key', amount INT, note STRING",
        "(1, 10, 'a'), (2, 20, 'b')",
        "ALTER TABLE {t} SET TBLPROPERTIES ('team.tag' = 'kept')",
    )
    want = declared(
        columns=[
            {"name": "id", "type": "int", "nullable": False, "comment": "key"},
            {"name": "amount", "type": "bigint"},
            {"name": "note", "type": "string", "comment": "free text"},
            {"name": "added", "type": "struct<a: int, b: array<string>>"},
        ],
        comment="synthetic table",
        properties={"team.tag": "kept"},
    )
    first = migrate(env.spark, name, want)
    assert first.applied and first.plan.classification == "compatible"
    live = read_live(env.spark, name)
    assert live.column("amount").type.ddl() == "BIGINT"  # type: ignore[union-attr]
    assert live.column("note").comment == "free text"  # type: ignore[union-attr]
    assert live.column("added") is not None
    assert env.rows("orders", "id, amount, note") == [(1, 10, "a"), (2, 20, "b")]
    assert live.properties["team.tag"] == "kept" and live.comment == "synthetic table"
    again = migrate(env.spark, name, want)
    assert not again.applied and again.plan.empty
    assert plan_migration(name, want, read_live(env.spark, name)).empty


def test_rename_with_dependent_check_is_applied_coherently_with_data_preserved(env: Env) -> None:
    name = env.create(
        "renamed",
        "id INT NOT NULL, amount INT, other INT",
        "(1, 10, 5), (2, 20, 6)",
        "ALTER TABLE {t} ADD CONSTRAINT positive CHECK (amount > 0)",
        "ALTER TABLE {t} ADD CONSTRAINT other_small CHECK (other < 100)",
    )
    want = declared(
        columns=[
            {"name": "id", "type": "int", "nullable": False},
            {"name": "value", "type": "int"},
            {"name": "other", "type": "int"},
        ],
        checks={"positive": "value > 0"},
    )
    policy = EvolutionPolicy(renames={"amount": "value"})
    plan = plan_migration(name, want, read_live(env.spark, name), policy)
    order = [(o.kind, o.path) for o in plan.operations]
    assert (
        order.index(("drop_check", "positive"))
        < order.index(("rename_column", "amount"))
        < order.index(("add_check", "positive"))
    )
    result = migrate(env.spark, name, want, policy)
    assert result.applied
    live = read_live(env.spark, name)
    assert [c.name for c in live.columns] == ["id", "value", "other"]
    assert env.rows("renamed") == [(1, 10, 5), (2, 20, 6)]
    assert "value" in live.checks["positive"] and live.checks["other_small"].replace(" ", "") == "other<100"
    with pytest.raises(Exception, match=r"(?i)constraint|positive"):
        env.spark.sql(f"INSERT INTO {name} VALUES (3, -1, 1)")
    assert not migrate(env.spark, name, want, policy).applied  # re-running is stable


def test_narrowing_is_refused_without_policy_and_checked_with_it(env: Env) -> None:
    name = env.create("narrow", "id INT NOT NULL, amount INT, tag STRING", "(1, 10, '7'), (2, 20, '8')")
    want = declared(
        columns=[
            {"name": "id", "type": "int", "nullable": False},
            {"name": "amount", "type": "smallint"},
            {"name": "tag", "type": "string"},
        ]
    )
    version = env.spark.sql(f"DESCRIBE HISTORY {name} LIMIT 1").collect()[0]["version"]
    with pytest.raises(MigrationBlockedError, match="narrowing"):
        migrate(env.spark, name, want)
    assert env.spark.sql(f"DESCRIBE HISTORY {name} LIMIT 1").collect()[0]["version"] == version
    assert read_live(env.spark, name).column("amount").type.ddl() == "INT"  # type: ignore[union-attr]
    # a lossless narrowing is applied only under the explicit policy, rewriting through a checked column
    result = migrate(env.spark, name, want, EvolutionPolicy(narrowing="checked"))
    assert result.applied
    assert read_live(env.spark, name).column("amount").type.ddl() == "SMALLINT"  # type: ignore[union-attr]
    assert env.rows("narrow", "id, amount, tag") == [(1, 10, "7"), (2, 20, "8")]
    # a lossy value is refused before any column is touched
    env.spark.sql(f"INSERT INTO {name} (id, amount, tag) VALUES (3, 30, '9')")
    big = declared(
        columns=[
            {"name": "id", "type": "int", "nullable": False},
            {"name": "amount", "type": "smallint"},
            {"name": "tag", "type": "int"},
        ]
    )
    env.spark.sql(f"INSERT INTO {name} (id, amount, tag) VALUES (4, 40, 'not a number')")
    with pytest.raises(MigrationBlockedError, match="check failed"):
        migrate(env.spark, name, big, EvolutionPolicy(incompatible="checked"))
    assert read_live(env.spark, name).column("tag").type.ddl() == "STRING"  # type: ignore[union-attr]
    assert not any(c.name.startswith("_bs_tmp") for c in read_live(env.spark, name).columns)


def test_drop_is_destructive_and_needs_the_policy(env: Env) -> None:
    name = env.create("dropped", "id INT NOT NULL, gone STRING", "(1, 'x')")
    want = declared(columns=[{"name": "id", "type": "int", "nullable": False}])
    with pytest.raises(MigrationBlockedError, match="destructive"):
        migrate(env.spark, name, want)
    assert read_live(env.spark, name).column("gone") is not None
    assert migrate(env.spark, name, want, EvolutionPolicy(destructive=True)).applied
    assert env.rows("dropped") == [(1,)]


def test_write_with_an_evolution_policy_admits_a_compatible_column(env: Env) -> None:
    name = env.create("sink", "id INT NOT NULL, v INT", "(1, 10)")
    io = SparkTableIO(env.spark)
    frame = env.spark.createDataFrame([(2, 20, "n")], "id INT, v INT, extra STRING")
    with pytest.raises(Exception, match=r"(?i)evolution"):
        io.write(name, frame, operation="append", options={"write": "append", "keys": ["id"]})
    io.write(name, frame, operation="append", options={"write": "append", "keys": ["id"], "evolution": {}})
    assert env.rows("sink", "id, v, extra") == [(1, 10, None), (2, 20, "n")]
    narrow = env.spark.createDataFrame([(3, 1)], "id INT, v SMALLINT")
    with pytest.raises(MigrationBlockedError):
        io.write(name, narrow, operation="append", options={"write": "append", "keys": ["id"], "evolution": {}})
    assert env.rows("sink", "id") == [(1,), (2,)]


def test_maintenance_runs_only_when_asked(env: Env) -> None:
    name = env.create("maint", "id INT, v INT", "(1, 1)")
    io = SparkTableIO(env.spark)
    options = {
        "write": "append",
        "allow_duplicates": True,
        "maintenance": {"optimize": {}, "vacuum": {"retain_hours": 200}},
    }
    for i in range(2, 4):
        io.write(name, env.spark.createDataFrame([(i, i)], "id INT, v INT"), operation="append", options=options)
    assert not any(op in {"OPTIMIZE", "VACUUM START", "VACUUM END"} for op in env.operations("maint"))
    spec = parse_maintenance(options["maintenance"])  # type: ignore[arg-type]
    plan = plan_maintenance(name, spec)
    assert plan.operations == ("optimize", "vacuum")
    assert not any(op in {"OPTIMIZE", "VACUUM START"} for op in env.operations("maint"))  # planning changes nothing
    run_maintenance(env.spark, plan)
    ops = env.operations("maint")
    assert "OPTIMIZE" in ops
    assert env.rows("maint") == [(1, 1), (2, 2), (3, 3)]
