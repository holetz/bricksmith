"""Rules kept as data: ``rules: {table: ...}``, the audit-trail rows, and the run that reads them."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

import json
import textwrap
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.context import ExecutionContext
from bricksmith.graph import load_graph
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import parse_config
from bricksmith.monitor.stability.rulestable import load_rules, rule_row, rules_from_rows
from bricksmith.plan import build_plan
from bricksmith.runner import InMemoryTableIO, apply_plan

if TYPE_CHECKING:
    from pathlib import Path

TABLE = "main.sales.stability_rules"
BODY = {"id": "amount-psi", "metric": "psi", "column": "amount", "limits": {"green": 0.3, "red": 0.5}}


def test_the_declaration_accepts_a_table_and_serializes_it() -> None:
    config = parse_config({"period": "month", "rules": {"table": TABLE}})
    assert config.rules == () and config.rules_table == TABLE
    assert config.to_dict()["rules"] == {"table": TABLE}
    assert parse_config(config.to_dict()).rules_table == TABLE


@pytest.mark.parametrize("bad", [{"table": "no_catalog"}, {"table": 3}, {"path": TABLE}, {"table": TABLE, "x": 1}])
def test_a_malformed_rules_table_is_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(MonitorError, match="rules given as a mapping"):
        parse_config({"period": "month", "rules": bad})


def test_rule_row_validates_with_the_declaration_rules_and_keeps_the_body() -> None:
    row = rule_row(BODY, author="ana", created_at="2026-01-01T00:00:00+00:00")
    assert row["rule_id"] == "amount-psi" and row["version"] == 1 and json.loads(row["body"]) == BODY
    with pytest.raises(MonitorError):
        rule_row({**BODY, "metric": "nope"}, author="ana", created_at="2026-01-01T00:00:00+00:00")
    with pytest.raises(MonitorError, match="author"):
        rule_row(BODY, author=" ", created_at="2026-01-01T00:00:00+00:00")


def test_the_latest_row_of_a_rule_version_wins_and_older_rows_stay_in_the_table() -> None:
    old = rule_row(BODY, author="ana", created_at="2026-01-01T00:00:00+00:00")
    wider = {**BODY, "limits": {"green": 0.4, "red": 0.8}}
    new = rule_row(wider, author="bo", created_at="2026-02-01T00:00:00+00:00")
    other = rule_row({**BODY, "id": "b", "version": 2}, author="bo", created_at="2026-02-02T00:00:00+00:00")
    rules = rules_from_rows([new, old, other])
    assert [(r.id, r.version) for r in rules] == [("amount-psi", 1), ("b", 2)]
    assert rules[0].limits is not None and rules[0].limits.red == 0.8


def test_a_row_that_is_not_json_is_refused_naming_it() -> None:
    with pytest.raises(MonitorError, match="'x' version 1 has a body that is not JSON"):
        rules_from_rows([{"rule_id": "x", "version": 1, "body": "{", "created_at": "2026-01-01"}])


def test_load_rules_reads_rows_or_frames_and_names_a_missing_table() -> None:
    row = rule_row(BODY, author="ana", created_at="2026-01-01T00:00:00+00:00")
    assert [r.id for r in load_rules(InMemoryTableIO({TABLE: [row]}), TABLE)] == ["amount-psi"]

    class _Frame:
        def collect(self) -> list[Any]:
            return [type("Row", (), {"asDict": lambda _self: row})()]

    assert [r.id for r in load_rules(InMemoryTableIO({TABLE: _Frame()}), TABLE)] == ["amount-psi"]
    with pytest.raises(MonitorError, match=r"rules table main\.sales\.stability_rules cannot be read"):
        load_rules(InMemoryTableIO(), TABLE)


def test_a_run_judges_with_the_rules_read_from_the_table(tmp_path: Path) -> None:
    path = tmp_path / "catalogs/main/sales/orders.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        textwrap.dedent(
            f"""
            from bricksmith.tasks import task

            @task(kind="table", outputs="orders", metadata={{"stability": {{
                "period": "month", "min_rows": 10, "psi_limits": "fixed", "min_rows_per_bin": 0,
                "baseline": "previous", "rules": {{"table": "{TABLE}"}}}}}})
            def orders(ctx):
                rows = []
                for m in range(1, 4):
                    rows += [{{"month": f"2026-{{m:02d}}", "amount": float(i % 20) + i * 0.013}} for i in range(80)]
                ctx.write("orders", rows)
            """
        ),
        encoding="utf-8",
    )
    row = rule_row(BODY, author="ana", created_at="2026-01-01T00:00:00+00:00")
    io = InMemoryTableIO({TABLE: [row]})
    execution = ExecutionContext(environment="test")
    graph = load_graph(tmp_path, context=execution)
    report = apply_plan(build_plan(graph), graph, project_root=tmp_path, execution=execution, table_io=io)
    assert report.ok
    results = io.tables["main.sales.stability_results"]
    assert isinstance(results, list) and "amount-psi" in {r["rule_id"] for r in results}
