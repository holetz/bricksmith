"""Model monitoring declared on tasks: plan targets, the post-write check, the gate and the run report."""
# ruff: noqa: PT018 - dense assertions on run outcomes

from __future__ import annotations

import json
import textwrap
from typing import TYPE_CHECKING, Any

from bricksmith.context import ExecutionContext
from bricksmith.graph import load_graph
from bricksmith.plan import Plan, build_plan
from bricksmith.runner import InMemoryTableIO, RunReport, apply_plan
from bricksmith.runner.service import run_summary

if TYPE_CHECKING:
    from pathlib import Path

TASKS = """
from bricksmith.tasks import task

MONTHS = [("dev", "2025-01"), ("dev", "2025-02"), ("oot", "2025-03"), ("prod", "2025-04"), ("prod", "2025-05")]


def scored(drift):
    rows = []
    for sample, month in MONTHS:
        for i in range(100):
            score = (i * 37 % 100) / 100
            if drift and month == MONTHS[-1][1]:
                score = min(score + 0.4, 0.99)
            label = 1 if (i * 61 % 100) / 100 < score else 0
            rows.append({"month": month, "sample": sample, "score": score, "label": label, "age": float(i % 40)})
    return rows


@task(kind="table", outputs="scores", metadata={"model_monitoring": MONITORING, "stability": STABILITY})
def scores(ctx):
    ctx.write("scores", scored(ctx.execution.parameters.get("drift") == "yes"))


@task(kind="table", outputs="score_report")
def report(ctx):
    ctx.write("score_report", len(ctx.read("scores")))
"""
MONITORING: dict[str, Any] = {
    "model": "churn",
    "period": "month",
    "score": "score",
    "label": "label",
    "sample": "sample",
    "references": {"development": {"sample": "dev"}, "out_of_time": {"sample": "oot"}},
    "features": ["age"],
    "min_rows": 10,
    "gate": {"fail_on": ["red"]},
}


def project(root: Path, monitoring: object = MONITORING, stability: object = None) -> Path:
    path = root / "catalogs/main/risk/scores.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = textwrap.dedent(TASKS).replace("MONITORING", repr(monitoring)).replace("STABILITY", repr(stability))
    if stability is None:
        text = text.replace(', "stability": None', "")
    path.write_text(text, encoding="utf-8")
    return root


def ctx(drift: str = "no") -> ExecutionContext:
    return ExecutionContext(environment="test", parameters={"drift": drift})


def run(root: Path, io: InMemoryTableIO, drift: str = "no", **kw: object) -> RunReport:
    graph = load_graph(root, context=ctx(drift))
    plan = build_plan(graph)
    return apply_plan(plan, graph, project_root=root, execution=ctx(drift), table_io=io, **kw)  # type: ignore[arg-type]


def test_plan_lists_the_model_monitoring_targets(tmp_path: Path) -> None:
    plan = build_plan(load_graph(project(tmp_path), context=ctx()))
    entry = plan.entry("main.risk.scores")
    assert [t.physical for t in entry.targets] == [
        "main.risk.scores",
        "main.risk.model_monitoring_alerts",
        "main.risk.model_monitoring_binnings",
        "main.risk.model_monitoring_details",
        "main.risk.model_monitoring_results",
        "main.risk.model_monitoring_runs",
    ]
    runs = entry.targets[5]
    assert runs.operations == ("merge",) and runs.options["keys"] == ["run_id", "model"]
    assert Plan.from_json(plan.to_json()).fingerprint == plan.fingerprint
    scoped = build_plan(load_graph(project(tmp_path / "s", {**MONITORING, "scope": "other.audit"}), context=ctx()))
    assert scoped.entry("main.risk.scores").targets[1].physical == "other.audit.model_monitoring_alerts"


def test_invalid_declaration_is_a_plan_error(tmp_path: Path) -> None:
    plan = build_plan(load_graph(project(tmp_path, {**MONITORING, "gate": {"fail_on": ["never"]}}), context=ctx()))
    assert [i.code for i in plan.errors] == ["invalid_model_monitoring"]
    plan = build_plan(load_graph(project(tmp_path / "b", {**MONITORING, "output": "nothing"}), context=ctx()))
    assert "exactly one task output" in plan.errors[0].message


def test_stable_run_writes_every_table_with_the_run_record_last(tmp_path: Path) -> None:
    io = InMemoryTableIO()
    report = run(project(tmp_path), io)
    assert report.ok
    outcome = report.results[0].model_monitoring[0]
    assert outcome["status"] == "passed" and outcome["run_status"] == "success" and outcome["periods"] == 2
    writes = [w for w in report.results[0].writes if "model_monitoring" in w[0]]
    assert writes[-1] == ("main.risk.model_monitoring_runs", "merge") and len(writes) == 5
    results = io.tables["main.risk.model_monitoring_results"]
    assert isinstance(results, list) and {r["monitoring_period"] for r in results} == {"2025-04", "2025-05"}
    assert "model monitoring passed (success): churn 2025-05" in run_summary(report)
    before = len(results)
    again = run(project(tmp_path), io)
    assert again.ok and len(io.tables["main.risk.model_monitoring_runs"]) == 2  # type: ignore[arg-type]
    assert len(io.tables["main.risk.model_monitoring_results"]) == 2 * before  # type: ignore[arg-type]  # run_id keys
    assert len(io.tables["main.risk.model_monitoring_binnings"]) == 1  # type: ignore[arg-type]  # frozen binning


def test_red_gate_fails_the_task_and_blocks_dependents(tmp_path: Path) -> None:
    io = InMemoryTableIO()
    report = run(project(tmp_path), io, drift="yes")
    scores, summary = report.results
    assert scores.state == "failed" and scores.reason == "model monitoring check failed"
    assert scores.error is not None and scores.error["code"] == "model_monitoring_gate"
    assert ("main.risk.scores", "write") in scores.writes and summary.state == "blocked"
    alerts = io.tables["main.risk.model_monitoring_alerts"]
    assert any(a["severity"] == "critical" for a in alerts)  # type: ignore[union-attr]
    restored = RunReport.from_json(report.to_json())
    assert restored == report and restored.results[0].model_monitoring[0]["status"] == "failed"


def test_warn_gate_and_error_policy(tmp_path: Path) -> None:
    report = run(project(tmp_path / "warn", {**MONITORING, "gate": {}}), InMemoryTableIO(), drift="yes")
    assert report.ok and report.results[0].model_monitoring[0]["status"] == "warned"
    broken = {**MONITORING, "references": {"development": {"sample": "nowhere"}}, "gate": {"on_error": "warn"}}
    io = InMemoryTableIO()
    report = run(project(tmp_path / "broken", broken), io)
    assert report.ok and report.results[0].model_monitoring[0]["status"] == "error"
    assert io.tables["main.risk.model_monitoring_runs"][0]["status"] == "failed"  # type: ignore[index]
    strict = {**broken, "gate": {"on_error": "fail"}}
    report = run(project(tmp_path / "strict", strict), InMemoryTableIO())
    assert report.results[0].state == "failed" and report.results[0].error["code"] == "monitor_invalid"  # type: ignore[index]


def test_stability_and_model_monitoring_on_one_task(tmp_path: Path) -> None:
    stability = {"period": "month", "exclude": ["label", "sample"]}
    root = project(tmp_path, MONITORING, stability)
    plan = build_plan(load_graph(root, context=ctx()))
    assert not plan.errors
    report = run(root, InMemoryTableIO())
    result = report.results[0]
    assert report.ok and result.stability[0]["status"] == "passed"
    assert result.model_monitoring[0]["status"] == "passed"


def test_reports_without_model_monitoring_serialize_as_before(tmp_path: Path) -> None:
    report = run(project(tmp_path), InMemoryTableIO())
    assert "model_monitoring" not in report.to_dict()["results"][1]  # type: ignore[index]
    old = json.loads(report.to_json())
    for item in old["results"]:
        item.pop("model_monitoring", None)
    assert RunReport.from_dict(old).results[0].model_monitoring == ()


def test_dry_run_lists_model_monitoring_effects(tmp_path: Path) -> None:
    io = InMemoryTableIO()
    report = run(project(tmp_path), io, dry_run=True)
    assert ("main.risk.model_monitoring_runs", "merge") in report.results[0].writes and io.writes == []
