"""A write's declared evolution policy admits compatible changes through the planner and refuses the rest."""
# ruff: noqa: ARG002, PT018, PIE810 - compact fakes

from __future__ import annotations

from typing import Any

import pytest

from bricksmith.evolution import MigrationBlockedError, TableSchema
from bricksmith.storage import SparkTableIO, WriteSpecError

LIVE = TableSchema.from_dict({"columns": [{"name": "id", "type": "int"}, {"name": "v", "type": "int"}]})


class Row(dict):  # type: ignore[type-arg]
    def __getitem__(self, key: Any) -> Any:  # noqa: ANN401
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Schema:
    def __init__(self, fields: list[dict[str, Any]]) -> None:
        self.fields = fields

    def jsonValue(self) -> dict[str, Any]:  # noqa: N802
        return {"type": "struct", "fields": self.fields}


class Frame:
    def __init__(self, fields: list[dict[str, Any]]) -> None:
        self.columns = [f["name"] for f in fields]
        self.schema = Schema(fields)

    def limit(self, n: int) -> Frame:
        return self

    def collect(self) -> list[Row]:
        return [Row(n=1)]

    def createOrReplaceTempView(self, name: str) -> None:  # noqa: N802
        return None


class Catalog:
    def tableExists(self, name: str) -> bool:  # noqa: N802
        return True

    def dropTempView(self, name: str) -> None:  # noqa: N802
        return None


class Result:
    def __init__(self, rows: list[Row]) -> None:
        self.rows = rows

    def collect(self) -> list[Row]:
        return self.rows


class Spark:
    catalog = Catalog()

    def __init__(self) -> None:
        self.log: list[str] = []

    def table(self, name: str) -> Frame:
        return Frame([{"name": "id", "type": "integer"}, {"name": "v", "type": "integer"}])

    def sql(self, text: str) -> Result:
        self.log.append(text)
        return Result([Row(version=3)] if text.startswith("DESCRIBE HISTORY") else [Row(c=0)])


def fields(*pairs: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"name": n, "type": t, "nullable": True, "metadata": {}} for n, t in pairs]


@pytest.fixture(autouse=True)
def _live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bricksmith.evolution.bridge.read_live", lambda *_: LIVE)
    monkeypatch.setattr("bricksmith.evolution.apply.read_live", lambda *_: LIVE)


def opts(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"write": "append", "keys": ["id"], **extra}


def alters(spark: Spark) -> list[str]:
    return [s for s in spark.log if s.startswith("ALTER")]


def test_without_a_policy_a_differing_schema_is_still_rejected() -> None:
    spark = Spark()
    with pytest.raises(WriteSpecError, match="evolution"):
        SparkTableIO(spark).write(
            "t", Frame(fields(("id", "integer"), ("v", "integer"), ("n", "string"))), operation="append", options=opts()
        )
    assert alters(spark) == []


def test_policy_admits_a_compatible_add_before_the_write() -> None:
    spark = Spark()
    io = SparkTableIO(spark)
    io.write(
        "t",
        Frame(fields(("id", "integer"), ("v", "integer"), ("n", "string"))),
        operation="append",
        options=opts(evolution={}),
    )
    first_alter = next(i for i, s in enumerate(spark.log) if s.startswith("ALTER"))
    first_insert = next(i for i, s in enumerate(spark.log) if s.startswith("INSERT") or s.startswith("MERGE"))
    assert alters(spark) == ["ALTER TABLE `t` ADD COLUMN `n` STRING"] and first_alter < first_insert
    assert io.migrations[0].projected_columns == ("id", "v", "n")


def test_policy_still_refuses_narrowing_and_drops_without_changes() -> None:
    spark = Spark()
    with pytest.raises(MigrationBlockedError, match="narrowing"):
        SparkTableIO(spark).write(
            "t", Frame(fields(("id", "integer"), ("v", "short"))), operation="append", options=opts(evolution={})
        )
    with pytest.raises(MigrationBlockedError, match="destructive"):
        SparkTableIO(spark).write("t", Frame(fields(("id", "integer"))), operation="append", options=opts(evolution={}))
    assert alters(spark) == [] and not any(s.startswith(("INSERT", "MERGE")) for s in spark.log)


def test_explain_never_migrates() -> None:
    spark = Spark()
    SparkTableIO(spark).explain(
        "t",
        Frame(fields(("id", "integer"), ("v", "integer"), ("n", "string"))),
        operation="append",
        options=opts(evolution={}),
    )
    assert alters(spark) == []
