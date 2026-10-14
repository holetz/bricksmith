"""Pure planning: metadata validation, write plans, SQL descriptions, SCD2 invariants and helpers."""
# ruff: noqa: E501, FBT001, FBT003, PT018, RUF015 - compact fakes and dense assertions in synthetic tests

from __future__ import annotations

import datetime as dt

import pytest

from bricksmith.errors import AuthorizationError
from bricksmith.registry.model import parse_registry
from bricksmith.storage import (
    WritePlan,
    WriteSpecError,
    parse_write_spec,
    plan_registry_metadata,
    plan_restore,
    plan_retention,
    plan_write,
    scd2_violations,
)
from bricksmith.storage.sql import literal, partition_predicate, quote

T = "main.s.t"


def spec(**options: object):  # noqa: ANN201
    return parse_write_spec(options, str(options.get("write", "write")))


def sqls(plan: WritePlan, kind: str) -> list[str]:
    return [s.sql for s in plan.steps if s.kind == kind]


def test_strategy_comes_from_the_operation_or_the_generic_write_metadata() -> None:
    assert parse_write_spec({"write": "merge", "keys": ["id"]}, "write").strategy == "merge"
    assert parse_write_spec({"write": "merge", "keys": ["id"]}, "overwrite").strategy == "overwrite"
    with pytest.raises(WriteSpecError, match="no write strategy"):
        parse_write_spec({}, "write")


@pytest.mark.parametrize(
    "options",
    [
        {"write": "merge"},
        {"write": "append"},
        {"write": "merge", "keys": ["id; drop"]},
        {"write": "merge", "keys": ["id", "id"]},
        {"write": "replace_partitions"},
        {"write": "replace_partitions", "partition_by": ["d"], "late_input": "accept"},
        {"write": "replace_partitions", "partition_by": ["d"], "partitions": [{"x": 1}]},
        {
            "write": "replace_partitions",
            "partition_by": ["d"],
            "window": {"column": "d", "start": "2024-01-02", "end": "2024-01-01"},
        },
        {"write": "scd2", "keys": ["id"], "effective": "at"},
        {"write": "scd2", "keys": ["id"], "tracked": ["id"], "effective": "at"},
        {"write": "scd2", "keys": ["id"], "tracked": ["v"], "effective": "at", "late_input": "accept"},
        {"write": "overwrite", "on_empty": "ignore"},
        {"write": "function"},
    ],
)
def test_invalid_metadata_is_rejected(options: dict[str, object]) -> None:
    with pytest.raises(WriteSpecError):
        spec(**options)


def test_overwrite_creates_then_replaces_and_reports_before_mutation() -> None:
    first = plan_write(T, spec(write="overwrite"), operation="overwrite", input_columns=["a", "b"])
    assert sqls(first, "mutate") == ["CREATE TABLE `main`.`s`.`t` AS SELECT `a`, `b` FROM {input}"]
    again = plan_write(
        T, spec(write="overwrite"), operation="overwrite", input_columns=["a", "b"], target_columns=["b", "a"]
    )
    assert sqls(again, "mutate")[0].startswith("INSERT OVERWRITE `main`.`s`.`t` SELECT `b`, `a`")
    assert WritePlan.from_dict(again.to_dict()) == again
    assert again.to_json() == WritePlan.from_dict(again.to_dict()).to_json()


def test_schema_mismatch_is_rejected_before_any_step() -> None:
    with pytest.raises(WriteSpecError, match="schema evolution"):
        plan_write(T, spec(write="overwrite"), operation="overwrite", input_columns=["a"], target_columns=["a", "x"])


def test_static_refresh_has_a_guard_unless_forced() -> None:
    guarded = plan_write(T, spec(write="static_refresh"), operation="static_refresh", input_columns=["a"])
    forced = plan_write(T, spec(write="static_refresh", force=True), operation="static_refresh", input_columns=["a"])
    assert [s.kind for s in guarded.steps][0] == "guard"
    assert all(s.kind != "guard" for s in forced.steps)


def test_protection_is_checked_before_anything_is_planned() -> None:
    with pytest.raises(AuthorizationError, match="nothing was written"):
        plan_write(T, spec(write="overwrite"), operation="overwrite", input_columns=["a"], protected=True)
    plan = plan_write(
        T, spec(write="overwrite"), operation="overwrite", input_columns=["a"], protected=True, granted=True
    )
    assert plan.protected
    with pytest.raises(AuthorizationError):
        plan_restore(T, version=1, protected=True)


def test_replace_partitions_names_only_the_replaced_rows() -> None:
    s = spec(write="replace_partitions", partition_by=["d"])
    plan = plan_write(
        T,
        s,
        operation="replace_partitions",
        input_columns=["d", "v"],
        target_columns=["d", "v"],
        partition_values=[{"d": "2024-01-02"}, {"d": "2024-01-01"}],
    )
    assert sqls(plan, "mutate") == [
        "INSERT INTO `main`.`s`.`t` REPLACE WHERE (`d` = '2024-01-01') OR (`d` = '2024-01-02') SELECT `d`, `v` FROM {input}"
    ]
    named = spec(write="replace_partitions", partition_by=["d"], partitions=[{"d": "x"}])
    guarded = plan_write(T, named, operation="replace_partitions", input_columns=["d", "v"], target_columns=["d", "v"])
    assert any("every input row lies in the named partitions" in s.description for s in guarded.steps)


def test_window_checks_and_late_input_policy() -> None:
    window = {"column": "d", "start": "2024-01-02", "end": "2024-01-04"}
    fail = plan_write(T, spec(write="merge", keys=["id"], window=window), operation="merge", input_columns=["id", "d"])
    drop = plan_write(
        T,
        spec(write="merge", keys=["id"], window=window, late_input="drop"),
        operation="merge",
        input_columns=["id", "d"],
    )
    accept = plan_write(
        T,
        spec(write="merge", keys=["id"], window=window, late_input="accept"),
        operation="merge",
        input_columns=["id", "d"],
    )
    late = "no late input before window start 2024-01-02"
    assert any(s.description == late for s in fail.steps) and not fail.input_filter
    assert drop.input_filter == "`d` >= '2024-01-02'" and not any(s.description == late for s in drop.steps)
    assert not accept.input_filter and not any(s.description == late for s in accept.steps)
    assert any("window end" in s.description for s in accept.steps)


def test_merge_and_append_are_keyed_and_unknown_columns_are_rejected() -> None:
    merge = plan_write(
        T, spec(write="merge", keys=["id"]), operation="merge", input_columns=["id", "v"], target_columns=["id", "v"]
    )
    (sql,) = sqls(merge, "mutate")
    assert "ON t.`id` = s.`id` WHEN MATCHED THEN UPDATE SET t.`v` = s.`v` WHEN NOT MATCHED THEN INSERT" in sql
    append = plan_write(
        T, spec(write="append", keys=["id"]), operation="append", input_columns=["id", "v"], target_columns=["id", "v"]
    )
    assert "WHEN MATCHED" not in sqls(append, "mutate")[0]
    loose = plan_write(
        T, spec(write="append", allow_duplicates=True), operation="append", input_columns=["id"], target_columns=["id"]
    )
    assert "not idempotent" in loose.steps[-1].description
    with pytest.raises(WriteSpecError, match="keys not in the input"):
        plan_write(T, spec(write="merge", keys=["nope"]), operation="merge", input_columns=["id"])


def test_scd2_plan_has_checks_merge_and_invariants() -> None:
    s = spec(write="scd2", keys=["id"], tracked=["name"], effective="at")
    cols = ["id", "name", "at"]
    first = plan_write(T, s, operation="scd2", input_columns=cols)
    assert sqls(first, "create")[0].startswith(
        "CREATE TABLE `main`.`s`.`t` AS SELECT `id`, `name`, `at`, `at` AS `valid_from`"
    )
    assert not any("redelivery" in x.description for x in first.steps)
    again = plan_write(
        T, s, operation="scd2", input_columns=cols, target_columns=[*cols, "valid_from", "valid_to", "is_current"]
    )
    kinds = [x.kind for x in again.steps]
    assert kinds.index("check") < kinds.index("mutate") < kinds.index("post_check")
    assert any("redelivery" in x.description for x in again.steps)
    assert any("backdated" in x.description for x in again.steps)
    assert "WHEN NOT MATCHED AND s._bs_new THEN INSERT" in sqls(again, "mutate")[0]
    dropped = plan_write(
        T,
        spec(write="scd2", keys=["id"], tracked=["name"], effective="at", late_input="drop"),
        operation="scd2",
        input_columns=cols,
        target_columns=[*cols, "valid_from", "valid_to", "is_current"],
    )
    assert not any("backdated" in x.description for x in dropped.steps)
    with pytest.raises(WriteSpecError, match="generated scd2 columns"):
        plan_write(T, s, operation="scd2", input_columns=[*cols, "valid_from"])


def test_views_and_functions_share_the_lifecycle() -> None:
    view = plan_write("m.s.v", spec(write="view"), operation="view", statement="SELECT 1 AS a;")
    assert sqls(view, "mutate") == ["CREATE OR REPLACE VIEW `m`.`s`.`v` AS SELECT 1 AS a"]
    mv = plan_write("m.s.v", spec(write="materialized_view"), operation="materialized_view", statement="SELECT 1")
    assert "MATERIALIZED VIEW" in sqls(mv, "mutate")[0]
    fn = plan_write(
        "m.s.f", spec(write="function", params="x INT", returns="INT"), operation="function", statement="x + 1"
    )
    assert sqls(fn, "mutate") == ["CREATE OR REPLACE FUNCTION `m`.`s`.`f`(x INT) RETURNS INT RETURN x + 1"]
    with pytest.raises(WriteSpecError, match="defining SQL"):
        plan_write("m.s.v", spec(write="view"), operation="view", statement=3)


def test_hooks_as_sql_checks_run_before_and_after() -> None:
    s = spec(write="overwrite", input_checks=["a > 0"], target_checks=["a > 0"])
    plan = plan_write(T, s, operation="overwrite", input_columns=["a"])
    assert [x.kind for x in plan.steps] == ["check", "mutate", "post_check"]


def test_scd2_violations_detect_each_invariant() -> None:
    s = spec(write="scd2", keys=["id"], tracked=["v"], effective="at")

    def row(vf: int, vt: int | None, cur: bool, key: int = 1) -> dict[str, object]:
        return {"id": key, "valid_from": vf, "valid_to": vt, "is_current": cur}

    assert scd2_violations([row(1, 5, False), row(5, None, True), row(1, None, True, key=2)], s) == []
    assert scd2_violations([row(1, 5, False), row(5, 9, False)], s) == ["(1,): 0 current rows"]
    assert any("2 current" in f for f in scd2_violations([row(1, None, True), row(2, None, True)], s))
    assert any("overlap" in f for f in scd2_violations([row(1, 7, False), row(5, None, True)], s))
    assert any("empty or inverted" in f for f in scd2_violations([row(3, 3, False), row(3, None, True)], s))
    assert any("disagree" in f for f in scd2_violations([row(1, None, False), row(5, None, True)], s))


def test_restore_and_retention_plans() -> None:
    assert plan_restore(T, version=3).steps[0].sql == "RESTORE TABLE `main`.`s`.`t` TO VERSION AS OF 3"
    assert "TIMESTAMP AS OF '2024-01-01'" in plan_restore(T, timestamp="2024-01-01").steps[0].sql
    with pytest.raises(WriteSpecError):
        plan_restore(T)
    sql = plan_retention(T, log_days=30, deleted_file_days=7).steps[0].sql
    assert "'delta.logRetentionDuration' = 'interval 30 days'" in sql
    with pytest.raises(WriteSpecError):
        plan_retention(T, log_days=0, deleted_file_days=7)


def test_literals_and_predicates() -> None:
    assert literal("o'x") == "'o\\'x'"
    assert literal(True) == "true"
    assert literal(dt.date(2024, 1, 2)) == "'2024-01-02'"
    assert quote("a.b`c") == "`a`.`b``c`"
    assert partition_predicate([{"a": 1, "b": None}]) == "(`a` = 1 AND `b` IS NULL)"


def test_registry_metadata_plan_changes_only_what_differs() -> None:
    registry = parse_registry(
        {
            "version": 1,
            "columns": [{"name": "id", "type": "bigint", "description": "Identifier", "sensitivity": "public"}],
            "tables": {"m.s.t": {"description": "Orders", "columns": {"id": {}}}},
        }
    )
    steps = plan_registry_metadata(
        registry, "m.s.t", "m.s.t", ["id", "other"], "", {"other": "keep"}, {"delta.x": "1", "bricksmith.stale": "1"}
    )
    text = " | ".join(s.sql for s in steps)
    assert "COMMENT ON TABLE" in text and "ALTER COLUMN `id` COMMENT 'Identifier'" in text
    assert "`other`" not in text and "delta.x" not in text
    assert "UNSET TBLPROPERTIES IF EXISTS ('bricksmith.stale')" in text
    assert plan_registry_metadata(registry, "missing", "m.s.t", ["id"], "", {}, {}) == []
