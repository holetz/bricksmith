"""Each task result carries the feature engine choices its task recorded, through a JSON round trip."""

from __future__ import annotations

import json
import textwrap
from typing import TYPE_CHECKING

from bricksmith.context import ExecutionContext
from bricksmith.graph import load_graph
from bricksmith.plan import Authorization, build_plan
from bricksmith.runner import InMemoryTableIO, RunReport, apply_plan

if TYPE_CHECKING:
    from pathlib import Path

TASKS = """
from bricksmith.features.record import EngineChoice, record
from bricksmith.tasks import task


@task(kind="table", outputs="features")
def features(ctx):
    record(EngineChoice("auto", "spark", reason="session: RuntimeError: refused", outputs=3))
    ctx.write("features", [1])


@task(kind="table", outputs="broken")
def broken(ctx):
    record(EngineChoice("jvm", "jvm", path="session", outputs=1))
    raise RuntimeError("boom")


@task(kind="table", outputs="plain")
def plain(ctx):
    ctx.write("plain", [1])
"""


def test_task_results_carry_the_recorded_engine_choices(tmp_path: Path) -> None:
    module = tmp_path / "catalogs" / "main" / "feat" / "jobs.py"
    module.parent.mkdir(parents=True)
    module.write_text(textwrap.dedent(TASKS), encoding="utf-8")
    graph = load_graph(tmp_path)
    plan = build_plan(graph)
    plan = plan.authorize(*(Authorization(o.physical, ("write",)) for o in graph.outputs))
    report = apply_plan(
        plan,
        graph,
        project_root=tmp_path,
        execution=ExecutionContext(environment="test", parameters={}),
        table_io=InMemoryTableIO({}),
    )
    results = {r.task_id.rsplit(".", 1)[1]: r for r in report.results}
    assert results["features"].feature_engines == (
        {"requested": "auto", "engine": "spark", "path": "", "reason": "session: RuntimeError: refused", "outputs": 3},
    )
    assert results["broken"].state == "failed"
    assert results["broken"].feature_engines[0]["engine"] == "jvm"
    assert results["plain"].feature_engines == ()
    data = json.loads(report.to_json())
    by_id = {r["task_id"].rsplit(".", 1)[1]: r for r in data["results"]}
    assert "feature_engines" not in by_id["plain"]
    assert by_id["features"]["feature_engines"][0]["engine"] == "spark"
    assert RunReport.from_dict(data) == report
