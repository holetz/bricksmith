"""Experiments on the selected workspace: pinned input, isolated revision, untouched baseline, compare, cleanup."""
# ruff: noqa: PT018 - dense assertions in remote tests

from __future__ import annotations

import dataclasses
import textwrap
from typing import TYPE_CHECKING

from bricksmith.experiments import (
    Evidence,
    apply_retirement,
    compare_revision,
    load_revision,
    plan_isolation,
    plan_retirement,
    run_revision,
)
from bricksmith.experiments.spark import SparkExperimentStore
from bricksmith.storage import SparkTableIO, current_version

if TYPE_CHECKING:
    from pathlib import Path

    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

REVISION = """
name = "rev_a"
roots = ["main.sales.report"]
[parameters]
factor = 3
[[pins]]
input = "{events}"
version = 0
[[compare]]
output = "report"
keys = ["id"]
"""
TASKS = {
    "events.py": """
        @task(kind="table")
        def events(ctx):
            ctx.write("events", None)
    """,
    "report.py": """
        @task(kind="table", metadata={"write": "overwrite"})
        def report(ctx):
            factor = ctx.execution.parameters.get("factor", 1)
            ctx.write("report", ctx.read("events").selectExpr("id", f"value * {factor} AS value"))
    """,
}


def test_pinned_isolated_revision_leaves_baseline_untouched_and_cleans_up(
    tmp_path: Path, remote_context: ExecutionContext, remote_clients: Clients, test_schema: str
) -> None:
    catalog = remote_context.catalog or ""
    folder = tmp_path / "catalogs/main/sales"
    folder.mkdir(parents=True)
    for name, body in TASKS.items():
        (folder / name).write_text("from bricksmith.tasks import task\n" + textwrap.dedent(body), encoding="utf-8")
    context = dataclasses.replace(
        remote_context, catalogs={"main": catalog}, schemas={"sales": test_schema}, parameters={"factor": 1}
    )
    spark = remote_clients.spark()
    events, report = f"{catalog}.{test_schema}.events", f"{catalog}.{test_schema}.report"
    ddl = "id BIGINT, value BIGINT"
    spark.createDataFrame([(1, 10), (2, 20)], ddl).write.mode("overwrite").saveAsTable(events)  # version 0
    spark.createDataFrame([(1, 10), (2, 20)], ddl).write.mode("overwrite").saveAsTable(report)
    spark.createDataFrame([(3, 99)], ddl).write.mode("append").saveAsTable(events)  # live drift, version 1
    store = SparkExperimentStore(spark)
    baseline_version = current_version(spark, report)
    assert store.version(events) == 1

    revision = load_revision(REVISION.format(events=events))
    plan = plan_isolation(tmp_path, revision, context=context, store=store)
    assert plan.ok, plan.to_text()
    isolated = f"{catalog}.{test_schema}_x_rev_a.report"
    assert plan.targets == {report: isolated} and plan.pins() == {events: 0}
    evidence = Evidence.freeze(plan, created="2026-01-01T00:00:00Z")
    try:
        outcome = run_revision(
            evidence, project_root=tmp_path, context=context, table_io=SparkTableIO(spark), store=store
        )
        assert outcome.ok, outcome.report.to_json()
        rows = sorted(tuple(r) for r in spark.table(isolated).collect())
        assert rows == [(1, 30), (2, 60)]  # frozen version 0 (not the live row 3) times factor 3
        assert current_version(spark, report) == baseline_version
        assert sorted(tuple(r) for r in spark.table(report).collect()) == [(1, 10), (2, 20)]
        result = compare_revision(evidence, store)
        comparison = result.comparisons[report]
        assert (comparison.missing_in_right, comparison.extra_in_right, comparison.rows_with_differences) == (0, 0, 2)
        assert result.verdict == "fail"
    finally:
        retirement = plan_retirement(evidence, store)
        dropped, left = apply_retirement(retirement, evidence, store)
    assert left == [] and isolated in dropped
    assert not store.exists(isolated) and not store.schema_exists(catalog, f"{test_schema}_x_rev_a")
    assert store.exists(events) and store.exists(report)
