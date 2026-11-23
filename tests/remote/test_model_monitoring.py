"""Model monitoring on the selected workspace: Spark equals the pure engine, a declared task feeds the views."""
# ruff: noqa: PT018 - dense assertions on run outcomes

from __future__ import annotations

import dataclasses
import textwrap
from typing import TYPE_CHECKING, Any

from bricksmith.graph import load_graph
from bricksmith.monitor.model import parse_config
from bricksmith.monitor.model.engine import MemoryStore, check
from bricksmith.monitor.model.tables import view_statements
from bricksmith.plan import build_plan
from bricksmith.runner import apply_plan
from bricksmith.storage import SparkTableIO

if TYPE_CHECKING:
    from pathlib import Path

    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

DDL = "month STRING, sample STRING, score DOUBLE, label INT, grade STRING, region STRING, age DOUBLE, income DOUBLE"
MONTHS = (("dev", "2025-01"), ("dev", "2025-02"), ("oot", "2025-03"), ("prod", "2025-04"), ("prod", "2025-05"))
DECLARATION: dict[str, Any] = {
    "model": "churn",
    "period": "month",
    "score": "score",
    "label": "label",
    "grade": "grade",
    "segment": "region",
    "sample": "sample",
    "references": {"development": {"sample": "dev"}, "out_of_time": {"sample": "oot"}},
    "features": ["age"],
    "critical_fields": ["income"],
    "model_version": "1",
    "min_rows": 10,
    "gate": {"fail_on": ["red"]},
}
TASKS = """
from bricksmith.tasks import task

DDL = {ddl!r}
MONTHS = {months!r}


def scored(drift):
    rows = []
    for sample, month in MONTHS:
        for i in range(120):
            score = (i * 37 % 120) / 120
            if drift and month == MONTHS[-1][1]:
                score = min(score + 0.4, 0.99)
            label = 1 if (i * 61 % 100) / 100 < score else 0
            grade = "A" if score < 0.3 else "B" if score < 0.7 else "C"
            income = None if i % 10 == 0 else float(i)
            rows.append((month, sample, score, label, grade, "ns"[i % 2], float(i % 40), income))
    return rows


@task(kind="table", outputs="scores", metadata={{"write": "overwrite", "model_monitoring": {declaration!r}}})
def scores(ctx):
    drift = ctx.execution.parameters.get("model_drift") == "yes"
    ctx.write("scores", ctx.io.spark.createDataFrame(scored(drift), DDL))


@task(kind="table", outputs="score_report", metadata={{"write": "overwrite"}})
def report(ctx):
    ctx.write("score_report", ctx.read("scores").groupBy("month").count())
"""


def synthetic() -> list[tuple[Any, ...]]:
    rows = []
    for sample, month in MONTHS:
        for i in range(120):
            score = (i * 37 % 120) / 120
            label = 1 if (i * 61 % 100) / 100 < score else 0
            grade = "A" if score < 0.3 else "B" if score < 0.7 else "C"
            income = None if i % 10 == 0 else float(i)
            rows.append((month, sample, score, label, grade, "ns"[i % 2], float(i % 40), income))
    return rows


def as_dicts(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    names = [c.split()[0] for c in DDL.split(", ")]
    return [dict(zip(names, r, strict=True)) for r in rows]


def same(a: object, b: object) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) < 1e-9 * max(1.0, abs(a))
    return a == b


def assert_rows_equal(left: list[dict[str, Any]], right: list[dict[str, Any]], keys: tuple[str, ...]) -> None:
    def index(rows: list[dict[str, Any]]) -> dict[tuple[Any, ...], dict[str, Any]]:
        return {tuple(r[k] for k in keys): r for r in rows}

    a, b = index(left), index(right)
    assert a.keys() == b.keys()
    for key, row in a.items():
        other = b[key]
        assert all(same(row[c], other[c]) for c in row if c not in ("computed_at", "finished_at")), (key, row, other)


def test_spark_engine_equals_the_pure_engine(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str
) -> None:
    spark = remote_clients.spark()
    table = f"{remote_context.catalog}.{test_schema}.model_input"
    spark.createDataFrame(synthetic(), DDL).write.saveAsTable(table)
    config = parse_config(DECLARATION)
    pure = check(as_dicts(synthetic()), config, MemoryStore(), run_id="r", now="t")
    remote = check(spark.table(table), config, MemoryStore(), run_id="r", now="t")
    assert remote.binning == pure.binning
    keys = ("monitoring_period", "segment", "column", "metric")
    assert_rows_equal(pure.tables["results"], remote.tables["results"], keys)
    assert_rows_equal(pure.tables["details"], remote.tables["details"], (*keys, "bin"))
    assert pure.outcome.to_dict() == remote.outcome.to_dict()
    assert {r["segment"] for r in remote.tables["results"]} == {"", "n", "s"}


def test_declared_task_writes_the_tables_and_the_views_read_them(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str, tmp_path: Path
) -> None:
    spark = remote_clients.spark()
    catalog = remote_context.catalog or ""
    project = tmp_path / "project"
    source = project / "catalogs/demo/ml/scores.py"
    source.parent.mkdir(parents=True)
    text = TASKS.format(ddl=DDL, months=MONTHS, declaration=DECLARATION)
    source.write_text(textwrap.dedent(text), encoding="utf-8")

    def t(name: str) -> str:
        return f"{catalog}.{test_schema}.{name}"

    def run(drift: str) -> Any:  # noqa: ANN401
        context = dataclasses.replace(
            remote_context,
            catalogs={"demo": catalog},
            schemas={"ml": test_schema},
            parameters={**remote_context.parameters, "model_drift": drift},
        )
        graph = load_graph(project, context=context)
        plan = build_plan(graph)
        assert not plan.errors
        assert t("model_monitoring_runs") in {x.physical for e in plan.entries for x in e.targets}
        return apply_plan(plan, graph, project_root=project, execution=context, table_io=SparkTableIO(spark))

    first = run("no")
    assert first.ok, first.to_json()
    outcome = next(r for r in first.results if r.model_monitoring).model_monitoring[0]
    assert outcome["status"] == "passed" and outcome["run_status"] == "success" and outcome["periods"] == 2
    assert int(spark.table(t("model_monitoring_binnings")).count()) == 1
    assert int(spark.table(t("model_monitoring_runs")).count()) == 1

    drifted = run("yes")
    scores = next(r for r in drifted.results if r.model_monitoring)
    assert scores.state == "failed" and scores.error and scores.error["code"] == "model_monitoring_gate"
    assert any(r.state == "blocked" for r in drifted.results)
    assert int(spark.table(t("model_monitoring_runs")).count()) == 2

    for statement in view_statements(f"{catalog}.{test_schema}"):
        spark.sql(statement)
    latest = spark.table(t("model_monitoring_latest")).where("monitoring_period = '2025-05' AND metric = 'psi'")
    rows = latest.where("segment = ''").collect()
    assert [r["light"] for r in rows] == ["red"] and rows[0]["development_value"] is not None
    status = spark.table(t("model_monitoring_model_status")).where("monitoring_period = '2025-05'").collect()
    assert [r["light"] for r in status] == ["red"]
    assert int(spark.table(t("model_monitoring_open_alerts")).count()) > 0
