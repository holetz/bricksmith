"""Registry metadata applied after a table write, against a recording fake Spark; no Spark needed."""
# ruff: noqa: PT018 - dense assertions in synthetic tests

from __future__ import annotations

from types import SimpleNamespace

from bricksmith.registry.model import parse_registry
from bricksmith.storage import SparkTableIO
from bricksmith.storage.registry_sync import apply_registry_metadata
from tests.unit.storage.test_executor import Fake, Frame, Result, Row, frame, opts

REGISTRY = parse_registry(
    {
        "version": 1,
        "columns": [{"name": "id", "type": "bigint", "description": "Identifier", "sensitivity": "public"}],
        "tables": {"m.s.t": {"description": "Orders", "columns": {"id": {}}}},
    }
)


class MetaFrame(Frame):
    schema = SimpleNamespace(
        fields=[
            SimpleNamespace(name="id", metadata={}),
            SimpleNamespace(name="v", metadata={"comment": "keep"}),
        ]
    )


class MetaFake(Fake):
    def table(self, name: str) -> Frame:
        return MetaFrame(self.tables[name], [{"n": 1}], self.log)

    def sql(self, text: str) -> Result:
        if text.startswith("DESCRIBE TABLE EXTENDED"):
            return Result([Row(col_name="Comment", data_type="")])
        if text.startswith("SHOW TBLPROPERTIES"):
            return Result([Row(key="delta.x", value="1")])
        return super().sql(text)


def statements(spark: MetaFake) -> list[str]:
    return [e for e in spark.log if e.startswith("sql ")]


def test_apply_registry_metadata_runs_only_changed_statements() -> None:
    spark = MetaFake({"t": ["id", "v"]})
    steps = apply_registry_metadata(spark, REGISTRY, "m.s.t", "t")
    text = " | ".join(s.sql for s in steps)
    assert "COMMENT ON TABLE" in text and "ALTER COLUMN `id` COMMENT 'Identifier'" in text
    assert "`v`" not in text and "delta.x" not in text
    assert apply_registry_metadata(spark, REGISTRY, "missing", "t") == []


def test_write_applies_registry_metadata_after_the_mutation() -> None:
    spark = MetaFake({"t": ["id", "v"]})
    io = SparkTableIO(spark, registry=REGISTRY, registry_tables={"t": "m.s.t"})
    io.write("t", frame(spark), operation="merge", options=opts())
    events = statements(spark)
    merge = max(i for i, e in enumerate(events) if e.startswith("sql MERGE"))
    assert events.index("sql COMMENT") > merge
    assert any(e.startswith("sql ALTER") for e in events[merge:])


def test_write_skips_metadata_without_a_registry_mapping_or_for_functions() -> None:
    spark = MetaFake({"t": ["id", "v"]})
    SparkTableIO(spark, registry=REGISTRY).write("t", frame(spark), operation="merge", options=opts())
    SparkTableIO(spark, registry_tables={"t": "m.s.t"}).write("t", frame(spark), operation="merge", options=opts())
    assert "sql COMMENT" not in statements(spark)
