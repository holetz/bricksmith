"""Operations on the selected workspace: runner events, a diagnosable failure and Delta readiness."""
# ruff: noqa: PT018 - dense assertions in remote tests

from __future__ import annotations

import dataclasses
import textwrap
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bricksmith.graph import load_graph
from bricksmith.ops import InputSpec, ListSink, build_ops_report, diagnose, evaluate_readiness, render_ops_report
from bricksmith.ops.inspect_spark import SparkDeltaInspector
from bricksmith.plan import build_plan
from bricksmith.runner import apply_plan
from bricksmith.storage import SparkTableIO

if TYPE_CHECKING:
    from pathlib import Path

    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

FILES = {
    "bricksmith.toml": "",
    "catalogs/lg/ops/seed.py": """
        from bricksmith.tasks import task

        @task(kind="table", outputs="seed", metadata={"write": "overwrite"})
        def seed(ctx):
            ctx.write("seed", ctx.io.spark.createDataFrame([(1, "a"), (2, None)], "id INT, tag STRING"))
    """,
    "catalogs/lg/ops/broken.py": """
        from bricksmith.tasks import task

        @task(kind="table", outputs="broken", metadata={"write": "overwrite"})
        def broken(ctx):
            ctx.read("seed")
            raise RuntimeError("injected failure password=hunter2")
    """,
    "catalogs/lg/ops/after.py": """
        from bricksmith.tasks import task

        @task(kind="table", outputs="after", metadata={"write": "overwrite"})
        def after(ctx):
            ctx.read("broken")
    """,
}


def test_failure_events_report_and_delta_readiness(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str, tmp_path: Path
) -> None:
    catalog = remote_context.catalog or ""
    spark = remote_clients.spark()
    for rel, body in FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    context = dataclasses.replace(remote_context, catalogs={"lg": catalog}, schemas={"ops": test_schema})
    graph = load_graph(tmp_path, context=context)
    plan = build_plan(graph)
    assert not plan.errors
    sink = ListSink()
    report = apply_plan(
        plan, graph, project_root=tmp_path, execution=context, table_io=SparkTableIO(spark), events=sink
    )
    assert report.ids("succeeded") == ("lg.ops.seed",)
    assert report.ids("failed") == ("lg.ops.broken",) and report.ids("blocked") == ("lg.ops.after",)
    kinds = [e.kind for e in sink.events]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished" and len({e.run_id for e in sink.events}) == 1
    assert "hunter2" not in "\n".join(e.to_json_line() for e in sink.events)
    blocked = diagnose(report, "lg.ops.after", plan)
    assert blocked.root_causes == ("lg.ops.broken",) and [u.task_id for u in blocked.upstream] == ["lg.ops.broken"]
    data = build_ops_report(report, plan)
    text = render_ops_report(data)
    assert "root cause: lg.ops.broken" in text and "hunter2" not in text and "cost unavailable" in text

    seed = f"{catalog}.{test_schema}.seed"
    ready = evaluate_readiness(
        [InputSpec(seed, 600, 2, ("id", "tag"), {"tag": 0.6}), InputSpec(f"{catalog}.{test_schema}.absent", 600)],
        SparkDeltaInspector(spark),
        datetime.now(UTC),
    )
    status = {(f.table.rsplit(".", 1)[-1], f.check): f.status for f in ready.findings}
    assert status[("seed", "freshness")] == "ready" and status[("seed", "min_rows")] == "ready"
    assert status[("seed", "null_fraction[tag]")] == "ready" and status[("absent", "freshness")] == "missing"
    strict = evaluate_readiness(
        [InputSpec(seed, 600, 3, (), {"tag": 0.1})], SparkDeltaInspector(spark), datetime.now(UTC)
    )
    assert {f.check: f.status for f in strict.findings}["min_rows"] == "incomplete" and not strict.ready
