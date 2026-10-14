"""Migration planning, ordering, policy, idempotence, maintenance, the CLI and the storage bridge."""
# ruff: noqa: PT018 - dense assertions in synthetic tests

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.cli.main import main
from bricksmith.evolution import (
    EvolutionPolicy,
    MigrationBlockedError,
    MigrationPlan,
    SchemaSpecError,
    StaleMigrationError,
    TableSchema,
    apply_plan,
    parse_maintenance,
    parse_policy,
    plan_maintenance,
    plan_migration,
)
from bricksmith.evolution.planner import fingerprint

if TYPE_CHECKING:
    from pathlib import Path

T = "c.s.orders"


def schema(*cols: tuple[str, str] | tuple[str, str, bool], **meta: object) -> TableSchema:
    columns = [{"name": c[0], "type": c[1], "nullable": c[2] if len(c) == 3 else True} for c in cols]
    return TableSchema.from_dict({"columns": columns, **meta})


LIVE = schema(
    ("id", "int", False), ("amount", "int"), checks={"positive": "amount > 0"}, properties={"unrelated": "keep"}
)


def kinds(plan: MigrationPlan) -> list[tuple[str, str]]:
    return [(o.kind, o.path) for o in plan.operations]


def test_rename_with_dependent_check_is_ordered_drop_rename_recreate() -> None:
    declared = schema(("id", "int", False), ("value", "int"), checks={"positive": "value > 0"})
    plan = plan_migration(T, declared, LIVE, EvolutionPolicy(renames={"amount": "value"}))
    assert kinds(plan) == [
        ("set_property", "delta.columnMapping.mode,delta.minReaderVersion,delta.minWriterVersion"),
        ("drop_check", "positive"),
        ("rename_column", "amount"),
        ("add_check", "positive"),
    ]
    assert plan.ok and plan.classification == "compatible"
    sql = [s.sql for s in plan.steps if s.kind == "mutate"]
    assert "DROP CONSTRAINT `positive`" in sql[1] and "RENAME COLUMN `amount` TO `value`" in sql[2]
    assert "CHECK (value > 0)" in sql[3]
    assert plan.projected_columns == ("id", "value")


def test_undeclared_dependent_check_is_rewritten_with_the_new_name() -> None:
    declared = schema(("id", "int", False), ("value", "int"))
    plan = plan_migration(T, declared, LIVE, parse_policy({"renames": {"amount": "value"}}))
    assert "CHECK (value > 0)" in plan.steps[-1].sql


def test_idempotent_plan_after_the_change_is_empty() -> None:
    declared = schema(("id", "int", False), ("value", "int"), checks={"positive": "value > 0"})
    policy = EvolutionPolicy(renames={"amount": "value"})
    after = schema(
        ("id", "int", False),
        ("value", "int"),
        checks={"positive": "value > 0"},
        properties={"delta.columnMapping.mode": "name", "unrelated": "keep"},
    )
    assert plan_migration(T, declared, after, policy).empty
    again = plan_migration(T, declared, LIVE, policy)
    assert again.to_json() == plan_migration(T, declared, LIVE, policy).to_json()
    assert MigrationPlan.from_dict(json.loads(again.to_json())).to_json() == again.to_json()


def test_compatible_add_widen_and_relax() -> None:
    declared = schema(("id", "bigint"), ("amount", "int"), ("note", "string"), checks={"positive": "amount > 0"})
    plan = plan_migration(T, declared, LIVE)
    assert plan.ok and plan.classification == "compatible"
    assert [k for k, _ in kinds(plan)] == ["set_property", "add_column", "change_type", "set_nullable"]
    assert "delta.enableTypeWidening" in plan.steps[0].sql


def test_classification_of_blocked_operations() -> None:
    narrow = plan_migration(T, schema(("id", "int", False), ("amount", "smallint")), LIVE)
    assert (
        not narrow.ok and narrow.classification == "incompatible" and "narrowing=checked" in narrow.blocked[0].blocked
    )
    family = plan_migration(T, schema(("id", "int", False), ("amount", "string")), LIVE)
    assert not family.ok and "incompatible=checked" in family.blocked[0].blocked
    tighten = plan_migration(T, schema(("id", "int", False), ("amount", "int", False)), LIVE)
    assert not tighten.ok and tighten.blocked[0].kind == "set_nullable"
    not_null_add = plan_migration(T, schema(("id", "int", False), ("amount", "int"), ("x", "int", False)), LIVE)
    assert not not_null_add.ok


def test_destructive_requires_explicit_policy() -> None:
    declared = schema(("id", "int", False))
    plan = plan_migration(T, declared, LIVE)
    assert plan.classification == "destructive" and not plan.ok
    assert "destructive=true" in plan.blocked[0].blocked
    allowed = plan_migration(T, declared, LIVE, EvolutionPolicy(destructive=True))
    assert allowed.ok and [k for k, _ in kinds(allowed)][:2] == ["set_property", "drop_check"]
    assert "positive" in allowed.operations[1].path and not any(o.kind == "add_check" for o in allowed.operations)


def test_checked_narrowing_rewrites_through_a_verified_temporary_column() -> None:
    declared = schema(("id", "int", False), ("amount", "smallint"), checks={"positive": "amount > 0"})
    plan = plan_migration(T, declared, LIVE, EvolutionPolicy(narrowing="checked"))
    assert plan.ok
    op = next(o for o in plan.operations if o.kind == "change_type")
    assert [s.kind for s in op.steps][:2] == ["check", "mutate"] and "try_cast" in op.steps[0].sql
    assert kinds(plan)[1] == ("drop_check", "positive") and kinds(plan)[-1] == ("add_check", "positive")
    nested = schema(("s", "struct<a: int>"))
    nested_decl = schema(("s", "struct<a: smallint>"))
    blocked = plan_migration(T, nested_decl, nested, EvolutionPolicy(narrowing="checked"))
    assert not blocked.ok and "nested" in blocked.blocked[0].blocked


def test_policy_and_maintenance_validation() -> None:
    for bad in ({"narrowing": "yes"}, {"nope": 1}, {"renames": {"a": "b.c"}}):
        with pytest.raises(SchemaSpecError):
            parse_policy(bad)
    with pytest.raises(SchemaSpecError):
        parse_maintenance({"vacuum": {"retain_hours": 1}})
    with pytest.raises(SchemaSpecError):
        parse_maintenance({"optimize": {"zorder_by": ["a"]}, "cluster_by": ["a"]})


def test_maintenance_plan_is_separate_selective_and_ordered() -> None:
    spec = parse_maintenance(
        {
            "cluster_by": ["id"],
            "optimize": {"where": "id > 1"},
            "vacuum": {"retain_hours": 200},
            "schedule": "0 3 * * 0",
        }
    )
    plan = plan_maintenance(T, spec)
    assert plan.operations == ("cluster", "optimize", "vacuum") and plan.schedule == "0 3 * * 0"
    assert [s.sql for s in plan.steps][1] == "OPTIMIZE `c`.`s`.`orders` WHERE id > 1"
    assert plan_maintenance(T, spec, ("vacuum",)).steps[0].sql.endswith("RETAIN 200 HOURS")
    assert plan_maintenance(T, parse_maintenance(None)).steps == ()


class Rows:
    def __init__(self, rows: list[dict[Any, Any]]) -> None:
        self.rows = rows

    def collect(self) -> list[dict[str, Any]]:
        return self.rows


class FakeSpark:
    def __init__(self, counts: int = 0) -> None:
        self.log: list[str] = []
        self.counts = counts

    def sql(self, text: str) -> Rows:
        self.log.append(text)
        return Rows([{0: self.counts}]) if text.startswith("SELECT") else Rows([])


def test_apply_refuses_blocked_stale_and_failed_checks_without_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    spark = FakeSpark()
    blocked = plan_migration(T, schema(("id", "int", False)), LIVE)
    with pytest.raises(MigrationBlockedError):
        apply_plan(spark, blocked)
    assert spark.log == []
    ok = plan_migration(
        T, schema(("id", "int", False), ("amount", "int"), ("n", "string"), checks={"positive": "amount > 0"}), LIVE
    )
    monkeypatch.setattr("bricksmith.evolution.apply.read_live", lambda *_: schema(("id", "int")))
    with pytest.raises(StaleMigrationError):
        apply_plan(spark, ok)
    monkeypatch.setattr("bricksmith.evolution.apply.read_live", lambda *_: LIVE)
    assert apply_plan(spark, ok) is True
    assert spark.log == ["ALTER TABLE `c`.`s`.`orders` ADD COLUMN `n` STRING"]
    assert apply_plan(spark, plan_migration(T, LIVE, LIVE)) is False
    failing = FakeSpark(counts=3)
    check = plan_migration(
        T,
        schema(("id", "int", False), ("amount", "int"), checks={"positive": "amount > 0", "small": "amount < 5"}),
        LIVE,
    )
    with pytest.raises(MigrationBlockedError):
        apply_plan(failing, check)
    assert not any(s.startswith("ALTER") for s in failing.log)
    assert fingerprint(LIVE) == plan_migration(T, LIVE, LIVE).fingerprint


def test_cli_diff_plan_and_maintain_offline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    live = tmp_path / "live.json"
    live.write_text(json.dumps(LIVE.to_dict()))
    declared = tmp_path / "declared.toml"
    declared.write_text(
        '[[columns]]\nname = "id"\ntype = "int"\nnullable = false\n[[columns]]\nname = "value"\ntype = "int"\n'
        '[evolution]\nrenames = { amount = "value" }\n[maintenance]\nvacuum = { retain_hours = 200 }\n'
    )
    base = ["schema", "diff", T, "--declared", str(declared), "--live", str(live), "--json"]
    assert main(base) == 0
    assert json.loads(capsys.readouterr().out)["changes"][0]["kind"] == "rename_column"
    assert main([*base, "--check"]) == 4
    capsys.readouterr()
    assert main(["schema", "plan", T, "--declared", str(declared), "--live", str(live), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["classification"] == "compatible"
    declared.write_text('[[columns]]\nname = "id"\ntype = "int"\nnullable = false\n')
    assert main(["schema", "plan", T, "--declared", str(declared), "--live", str(live)]) == 4
    assert "BLOCKED" in capsys.readouterr().out
    declared.write_text('[[columns]]\nname = "id"\ntype = "int"\n[maintenance]\nvacuum = { retain_hours = 200 }\n')
    assert main(["schema", "maintain", T, "--declared", str(declared)]) == 0
    assert "plan only" in capsys.readouterr().out
