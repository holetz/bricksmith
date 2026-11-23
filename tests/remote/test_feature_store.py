"""Closing-based feature store on the selected workspace: Spark equals the row engine; loads replace by closing."""
# ruff: noqa: PT018 - dense assertions on run outcomes

from __future__ import annotations

import dataclasses
import textwrap
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.graph import load_graph
from bricksmith.mlops.featurestore import FeatureStoreError, Lookup, is_closing, parse_feature_table, shift
from bricksmith.mlops.featurestore import rows as engine
from bricksmith.mlops.featurestore import spark as spark_engine
from bricksmith.plan import build_plan
from bricksmith.runner import apply_plan
from bricksmith.storage import SparkTableIO

if TYPE_CHECKING:
    from pathlib import Path

    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

DECLARATION: dict[str, Any] = {
    "entity_keys": ["account_id"],
    "availability_lag": 1,
    "features": {
        "balance": {"description": "Closing balance", "type": "double", "owner": "team-a", "default": 0.0},
        "segment": {"description": "Account segment", "type": "string", "owner": "team-a", "default": "unknown"},
        "history": {
            "description": "Balance and its previous value",
            "type": "array<double>",
            "owner": "team-a",
            "order": "index 0 is the closing balance",
        },
    },
}
SOURCE_DDL = "account_id BIGINT, described DATE, balance DOUBLE, segment STRING, history ARRAY<DOUBLE>"
SPINE_DDL = "account_id BIGINT, closing_date DATE, label INT"
CLOSINGS = (date(2023, 12, 31), date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 31))


def source_rows() -> list[dict[str, Any]]:
    rows = []
    for account in (1, 2, 3):
        for m, closing in enumerate(CLOSINGS):
            if account == 3 and m < 2:  # account 3 appears late
                continue
            value = None if (account, m) == (2, 1) else float(account * 10 + m)
            history = None if value is None else [value, value - 0.5]
            rows.append({"account_id": account, "described": closing, "balance": value,
                         "segment": "abc"[account - 1], "history": history})  # fmt: skip
    return rows


def spine_rows() -> list[dict[str, Any]]:
    return [
        {"account_id": account, "closing_date": closing, "label": (account + i) % 2}
        for account in (1, 2, 3, 4)
        for i, closing in enumerate(CLOSINGS[1:])
    ]


def frame(spark: Any, rows: list[dict[str, Any]], ddl: str) -> Any:  # noqa: ANN401
    names = [c.split()[0] for c in ddl.split(", ")]
    return spark.createDataFrame([tuple(r[n] for n in names) for r in rows], ddl)


def plain(rows: list[Any]) -> dict[tuple[Any, ...], dict[str, Any]]:
    found = {}
    for row in rows:
        data = row.asDict(recursive=True) if hasattr(row, "asDict") else dict(row)
        data["history"] = list(data["history"]) if data.get("history") is not None else None
        found[data["account_id"], data["closing_date"]] = data
    return found


def test_spark_engine_equals_the_row_engine(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str
) -> None:
    spark = remote_clients.spark()
    definition = parse_feature_table(DECLARATION)
    table = f"{remote_context.catalog}.{test_schema}.balances"

    calendar = spark.createDataFrame([(d,) for d in CLOSINGS], "d DATE")
    for frequency, periods in (("monthly", -13), ("monthly", 1), ("quarterly", 0), ("annual", 2), ("weekly", -3)):
        days = [d for d in CLOSINGS if is_closing(d, frequency)]
        got = calendar.where(spark_engine.is_closing_column(calendar["d"], frequency)).select(
            spark_engine.shift_column(calendar["d"], periods, frequency).alias("s")
        )
        assert sorted(r["s"] for r in got.collect()) == sorted(shift(d, periods, frequency) for d in days), frequency

    stamped_rows = engine.stamp_availability(source_rows(), definition, described="described")
    stamped = spark_engine.stamp_availability(
        frame(spark, source_rows(), SOURCE_DDL), definition, described="described"
    )
    assert plain(stamped.collect()) == plain(stamped_rows)
    stamped.write.saveAsTable(table)

    lookups = [Lookup(table), Lookup(table, features=("balance",), offset=1, prefix="prev_")]
    definitions = {table: definition}
    expected = engine.build_training_set(spine_rows(), lookups, definitions, {table: stamped_rows})
    built = spark_engine.build_training_set(frame(spark, spine_rows(), SPINE_DDL), lookups, definitions, spark=spark)
    result = built.data.collect()
    assert len(result) == len(spine_rows())
    assert list(built.data.columns) == list(expected.data[0])
    assert plain(result) == plain(expected.data)
    assert built.reads == {table: 0}
    missing = plain(result)[4, date(2024, 3, 31)]
    assert missing["balance"] == 0.0 and missing["segment"] == "unknown" and missing["history"] is None
    assert plain(result)[2, date(2024, 2, 29)]["balance"] is None  # a stored null stays null

    with pytest.raises(FeatureStoreError, match="appear more than once"):
        spark_engine.check_frame(spark.table(table).unionByName(spark.table(table).limit(1)), definition)
    with pytest.raises(FeatureStoreError, match="observation closing 2024-03-15"):
        spark_engine.build_training_set(
            frame(spark, [{"account_id": 1, "closing_date": date(2024, 3, 15), "label": 0}], SPINE_DDL),
            lookups, definitions, spark=spark,
        )  # fmt: skip


TASK = """
import datetime

from bricksmith.mlops.featurestore import parse_feature_table
from bricksmith.mlops.featurestore.spark import check_frame, stamp_availability
from bricksmith.tasks import task

DECLARATION = DECLARATION_VALUE


@task(kind="features", outputs="balances", metadata={"feature_table": DECLARATION_VALUE})
def balances(ctx):
    rows = ROWS_VALUE
    duplicate = ctx.execution.parameters.get("duplicate") == "yes"
    if duplicate:
        rows = rows + rows[:1]
    ddl = "account_id BIGINT, described DATE, balance DOUBLE, segment STRING, history ARRAY<DOUBLE>"
    frame = ctx.io.spark.createDataFrame(rows, ddl)
    stamped = stamp_availability(frame, parse_feature_table(DECLARATION), described="described")
    ctx.write("balances", stamped)
"""


def test_feature_table_task_replaces_by_closing_and_refuses_duplicate_keys(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str, tmp_path: Path
) -> None:
    spark = remote_clients.spark()
    catalog = remote_context.catalog or ""
    table = f"{catalog}.{test_schema}.balances"

    def run(rows: list[tuple[Any, ...]], duplicate: str = "no") -> Any:  # noqa: ANN401
        project = tmp_path / f"p{len(list(tmp_path.iterdir()))}"
        path = project / "catalogs/demo/fs/balances.py"
        path.parent.mkdir(parents=True)
        text = textwrap.dedent(TASK).replace("DECLARATION_VALUE", repr(DECLARATION)).replace("ROWS_VALUE", repr(rows))
        path.write_text(text, encoding="utf-8")
        context = dataclasses.replace(
            remote_context,
            catalogs={"demo": catalog},
            schemas={"fs": test_schema},
            parameters={**remote_context.parameters, "duplicate": duplicate},
        )
        graph = load_graph(project, context=context)
        plan = build_plan(graph)
        assert not plan.errors, plan.errors
        options = plan.entries[0].targets[0].options
        assert options["write"] == "replace_partitions" and options["keys"] == ["account_id", "closing_date"]
        return apply_plan(plan, graph, project_root=project, execution=context, table_io=SparkTableIO(spark))

    def stored() -> dict[tuple[int, date], float | None]:
        return {(r["account_id"], r["closing_date"]): r["balance"] for r in spark.table(table).collect()}

    first = [(a, d, float(a), "a", [1.0]) for a in (1, 2) for d in CLOSINGS[:2]]
    assert run(first).ok
    assert stored() == {(a, shift(d, 1, "monthly")): float(a) for a in (1, 2) for d in CLOSINGS[:2]}

    rerun = [(1, CLOSINGS[1], 99.0, "a", [1.0])]  # re-run the January closing (stored at February)
    assert run(rerun).ok
    assert stored() == {(1, date(2024, 1, 31)): 1.0, (2, date(2024, 1, 31)): 2.0, (1, date(2024, 2, 29)): 99.0}

    failed = run(rerun, duplicate="yes")
    assert not failed.ok and "unique" in str(failed.results[0].error), failed.to_json()
    assert stored()[1, date(2024, 2, 29)] == 99.0  # nothing was written
