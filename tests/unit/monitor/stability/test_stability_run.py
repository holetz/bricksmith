"""Stability declared on tasks: plan targets, the post-write check, the gate and the run report."""
# ruff: noqa: PT018 - dense assertions on run outcomes

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

from bricksmith.cli.main import main
from bricksmith.context import ExecutionContext
from bricksmith.graph import load_graph
from bricksmith.plan import Plan, build_plan
from bricksmith.runner import InMemoryTableIO, RunReport, apply_plan
from bricksmith.runner.service import run_summary

if TYPE_CHECKING:
    import pytest

TASKS = """
from bricksmith.tasks import task

MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04"]


def orders_rows(drift):
    rows = []
    for m, month in enumerate(MONTHS):
        for i in range(60):
            late = drift and m == len(MONTHS) - 1
            amount = float(i % 20 + (40 if late else 0))
            rows.append({"month": month, "amount": amount, "channel": "web" if i % 3 else "shop", "id": i})
    return rows


@task(kind="table", outputs="orders", metadata={"stability": STABILITY})
def orders(ctx):
    ctx.write("orders", orders_rows(ctx.execution.parameters.get("drift") == "yes"))


@task(kind="table", outputs="order_summary")
def summary(ctx):
    ctx.write("order_summary", len(ctx.read("orders")))
"""
STABILITY = {
    "period": "month",
    "psi_limits": "fixed",
    "min_rows_per_bin": 0,
    "exclude": ["id"],
    "segments": ["channel"],
    "baseline": "previous",
    "persistence": {"immediate_red": True},
    "gate": {"fail_on": ["red"]},
}


def project(root: Path, stability: object = STABILITY) -> Path:
    path = root / "catalogs/main/sales/orders.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(TASKS).replace("STABILITY", repr(stability)), encoding="utf-8")
    return root


def ctx(drift: str = "no") -> ExecutionContext:
    return ExecutionContext(environment="test", parameters={"drift": drift})


def run(root: Path, io: InMemoryTableIO, drift: str = "no", **kw: object) -> RunReport:
    graph = load_graph(root, context=ctx(drift))
    plan = build_plan(graph)
    return apply_plan(plan, graph, project_root=root, execution=ctx(drift), table_io=io, **kw)  # type: ignore[arg-type]


def test_plan_lists_the_stability_targets(tmp_path: Path) -> None:
    root = project(tmp_path)
    plan = build_plan(load_graph(root, context=ctx()))
    entry = plan.entry("main.sales.orders")
    assert [t.physical for t in entry.targets] == [
        "main.sales.orders",
        "main.sales.stability_actions",
        "main.sales.stability_alerts",
        "main.sales.stability_binnings",
        "main.sales.stability_profiles",
        "main.sales.stability_results",
        "main.sales.stability_runs",
    ]
    results = entry.targets[5]
    assert results.operations == ("merge",) and results.options["keys"] == [
        "dataset", "version", "period", "segment", "column", "metric",
    ]  # fmt: skip
    assert Plan.from_json(plan.to_json()).fingerprint == plan.fingerprint
    plain = build_plan(load_graph(project(tmp_path / "plain", {"period": "month", "scope": "other.audit"})))
    assert plain.entry("main.sales.orders").targets[2].physical == "other.audit.stability_alerts"


def test_invalid_declaration_is_a_plan_error(tmp_path: Path) -> None:
    plan = build_plan(load_graph(project(tmp_path, {"period": "month", "gate": {"fail_on": ["never-ever"]}})))
    assert [i.code for i in plan.errors] == ["invalid_stability"] and "fail_on" in plan.errors[0].message
    plan = build_plan(load_graph(project(tmp_path / "b", {"period": "month", "output": "nothing"})))
    assert "exactly one task output" in plan.errors[0].message


def test_stable_run_warns_nothing_and_is_idempotent(tmp_path: Path) -> None:
    root = project(tmp_path)
    io = InMemoryTableIO()
    report = run(root, io)
    assert report.ok
    result = report.results[0]
    assert result.stability[0]["status"] == "passed" and result.stability[0]["periods_profiled"] == 4
    assert ("main.sales.stability_results", "merge") in result.writes
    stored = io.tables["main.sales.stability_results"]
    assert isinstance(stored, list) and {r["status"] for r in stored if r["metric"] == "psi"} <= {
        "green",
        "no_baseline",
        "insufficient_volume",
    }
    before = len(stored)
    again = run(root, io)
    assert again.ok and again.results[0].stability[0]["periods_profiled"] == 1
    assert len(io.tables["main.sales.stability_results"]) == before  # type: ignore[arg-type]  # merge by key, no duplicates
    assert len(io.tables["main.sales.stability_runs"]) == 2  # type: ignore[arg-type]
    assert "stability passed: main.sales.orders 2026-04" in run_summary(again)


def test_red_gate_fails_the_task_and_blocks_dependents(tmp_path: Path) -> None:
    root = project(tmp_path)
    io = InMemoryTableIO()
    report = run(root, io, drift="yes")
    orders, summary = report.results
    assert orders.state == "failed" and orders.reason == "stability check failed"
    assert orders.error is not None and orders.error["code"] == "stability_gate"
    assert ("main.sales.orders", "write") in orders.writes  # the output is already published
    assert summary.state == "blocked" and not report.ok
    alerts = io.tables["main.sales.stability_alerts"]
    assert any(a["state"] == "open" and a["severity"] == "critical" for a in alerts)  # type: ignore[union-attr]
    restored = RunReport.from_json(report.to_json())
    assert restored == report and restored.results[0].stability[0]["status"] == "failed"


def test_warn_gate_and_error_policy(tmp_path: Path) -> None:
    warn = project(tmp_path / "warn", {**STABILITY, "gate": {}})
    report = run(warn, InMemoryTableIO(), drift="yes")
    assert report.ok and report.results[0].stability[0]["status"] == "warned"
    broken = {**STABILITY, "period": "missing_column", "gate": {"on_error": "warn"}}
    report = run(project(tmp_path / "broken", broken), InMemoryTableIO())
    assert report.ok and report.results[0].stability[0]["status"] == "error"
    assert "missing_column" in report.results[0].stability[0]["message"]
    strict = {**broken, "gate": {"on_error": "fail"}}
    report = run(project(tmp_path / "strict", strict), InMemoryTableIO())
    assert report.results[0].state == "failed" and report.results[0].error["code"] == "monitor_invalid"  # type: ignore[index]


def test_reports_without_stability_serialize_as_before(tmp_path: Path) -> None:
    report = run(project(tmp_path), InMemoryTableIO())
    summary = report.to_dict()["results"][1]  # type: ignore[index]
    assert "stability" not in summary
    old = json.loads(report.to_json())
    for item in old["results"]:
        item.pop("stability", None)
    assert RunReport.from_dict(old).results[0].stability == ()


def test_dry_run_lists_stability_effects(tmp_path: Path) -> None:
    root = project(tmp_path)
    io = InMemoryTableIO()
    report = run(root, io, dry_run=True)
    assert ("main.sales.stability_profiles", "merge") in report.results[0].writes and io.writes == []


def test_views_command_prints_sql(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["stability", "views", "--scope", "main.monitoring"]) == 0
    text = capsys.readouterr().out
    assert "CREATE OR REPLACE VIEW main.monitoring.stability_latest" in text
    assert "main.monitoring.stability_open_alerts" in text
    assert main(["stability", "views", "--scope", "bad scope"]) != 0


def test_example_declares_stability_offline() -> None:
    root = Path(__file__).resolve().parents[4] / "examples" / "shared_schema"
    graph = load_graph(root)
    assert graph.ok
    plan = build_plan(graph, roots=["demo.ml.sales_report"])
    assert not plan.errors
    entry = plan.entry("demo.ml.sales_monthly")
    assert {t.name for t in entry.targets} >= {"demo.ml.sales_monthly", "demo.ml.stability_results"}
