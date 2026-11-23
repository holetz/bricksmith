"""Model monitoring on the selected workspace: Spark equals the pure engine, a declared task feeds the views."""
# ruff: noqa: PT018 - dense assertions on run outcomes

from __future__ import annotations

import dataclasses
import textwrap
from typing import TYPE_CHECKING, Any

from bricksmith.graph import load_graph
from bricksmith.monitor.model import parse_config
from bricksmith.monitor.model.actions import action_row, insert_statement
from bricksmith.monitor.model.engine import MemoryStore, check
from bricksmith.monitor.model.tables import ALERTS, RESULTS, RUNS, SCHEMAS, view_statements
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
#: The declaration with every limit kind, a short trend window and the default persistence policy.
JUDGED: dict[str, Any] = {
    **DECLARATION,
    "limits": {
        "ks": {"kind": "relative", "green": -0.15, "red": -0.30, "direction": "lower"},
        "auc": {"kind": "relative", "green": -0.05, "red": -0.10, "direction": "lower", "reference": "out_of_time"},
        "gini": {"kind": "control", "green": 2, "red": 3, "direction": "both"},
    },
    "trend": {"window": 3, "min_points": 3},
    "trend_metrics": ["psi", "ks", "gini"],
}
#: Columns added after the first model monitoring release; tables created before them gain them on write.
ADDED = {
    RESULTS: ("limit_kind", "limit_reference", "compared_value", "baseline_mean", "baseline_sd", "baseline_points",
              "trend_status", "trend_expected", "trend_deviation", "lag", "lag_unit"),
    ALERTS: ("kind", "state", "opened_period", "escalated_period", "closed_period", "last_period", "flagged_periods",
             "recurrence", "action", "action_reason", "action_author", "action_at", "valid_until"),
    RUNS: ("open_alerts", "critical_alerts"),
}  # fmt: skip
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

    judged = parse_config(JUDGED)
    pure = check(as_dicts(synthetic()), judged, MemoryStore(), run_id="r", now="t")
    remote = check(spark.table(table), judged, MemoryStore(), run_id="r", now="t")
    assert_rows_equal(pure.tables["results"], remote.tables["results"], keys)
    assert_rows_equal(pure.tables["alerts"], remote.tables["alerts"], ("alert_id",))
    assert pure.outcome.to_dict() == remote.outcome.to_dict()
    gini = [r for r in remote.tables["results"] if r["metric"] == "gini" and r["segment"] == ""]
    assert all(r["limit_kind"] == "control" and r["baseline_points"] == 3 for r in gini)
    ks = [r for r in remote.tables["results"] if r["metric"] == "ks" and r["segment"] == ""]
    assert all(r["limit_reference"] == "development" and r["compared_value"] is not None for r in ks)


def test_lags_read_the_same_data_period_in_spark_and_in_the_pure_engine(
    remote_clients: Clients, remote_context: ExecutionContext, test_schema: str
) -> None:
    spark = remote_clients.spark()
    table = f"{remote_context.catalog}.{test_schema}.model_lag_input"
    spark.createDataFrame(synthetic(), DDL).write.saveAsTable(table)
    lagged = parse_config({**DECLARATION, "lags": {"auc": {"months": 1}, "gini": 1, "calibration": {"months": 9}}})
    pure = check(as_dicts(synthetic()), lagged, MemoryStore(), run_id="r", now="t")
    remote = check(spark.table(table), lagged, MemoryStore(), run_id="r", now="t")
    keys = ("monitoring_period", "segment", "column", "metric")
    assert_rows_equal(pure.tables["results"], remote.tables["results"], keys)
    rows = [r for r in remote.tables["results"] if r["segment"] == "" and r["monitoring_period"] == "2025-05"]
    by_metric = {r["metric"]: r for r in rows}
    assert (by_metric["auc"]["data_period"], by_metric["auc"]["lag_unit"]) == ("2025-04", "months")
    assert (by_metric["gini"]["data_period"], by_metric["gini"]["lag_unit"]) == ("2025-04", "periods")
    assert by_metric["ks"]["data_period"] == "2025-05" and by_metric["ks"]["lag"] == 0
    assert by_metric["brier"]["reason"] == "data_period_unavailable" and by_metric["brier"]["lag"] == 9


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

    for kind, added in ADDED.items():  # tables as the first model monitoring release created them
        columns = ", ".join(f"`{c}` {k}" for c, k in SCHEMAS[kind].items() if c not in added)
        spark.sql(f"CREATE TABLE {t('model_monitoring_' + kind)} ({columns})")

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
    for kind, added in ADDED.items():
        assert set(added) <= set(spark.table(t("model_monitoring_" + kind)).columns)
    kinds = {r["kind"] for r in spark.table(t("model_monitoring_alerts")).select("kind").distinct().collect()}
    assert "light" in kinds
    runs = spark.table(t("model_monitoring_runs")).orderBy("finished_at").collect()
    assert runs[-1]["open_alerts"] is not None and runs[-1]["critical_alerts"] is not None


def test_an_inserted_acceptance_suppresses_the_gate_and_the_governance_views_answer(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str, tmp_path: Path
) -> None:
    spark = remote_clients.spark()
    catalog = remote_context.catalog or ""
    project = tmp_path / "project"
    source = project / "catalogs/demo/ml/scores.py"
    source.parent.mkdir(parents=True)
    source.write_text(textwrap.dedent(TASKS.format(ddl=DDL, months=MONTHS, declaration=DECLARATION)), encoding="utf-8")

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
        return apply_plan(
            build_plan(graph), graph, project_root=project, execution=context, table_io=SparkTableIO(spark)
        )

    failed = run("yes")
    scores = next(r for r in failed.results if r.model_monitoring)
    assert scores.state == "failed" and scores.error and scores.error["code"] == "model_monitoring_gate"
    assert int(spark.table(t("model_monitoring_actions")).count()) == 0  # created empty by the run
    reds = spark.table(t("model_monitoring_alerts")).where("kind = 'light' AND light = 'red'").collect()
    assert reds
    table = t("model_monitoring_actions")
    for alert in reds:  # a person accepts every red alert through plain SQL
        row = action_row(model="churn", alert_id=alert["alert_id"], action="risk_accepted", reason="known shift",
                         author="ana", created_at="2026-01-01T00:00:00", valid_until="2999-12-31")  # fmt: skip
        spark.sql(insert_statement(table, row))
    assert int(spark.table(table).count()) == len({r["alert_id"] for r in reds})

    accepted = run("yes")
    outcome = next(r for r in accepted.results if r.model_monitoring).model_monitoring[0]
    assert outcome["status"] != "failed" and outcome["red"] == 0 and "accepted" in outcome["message"]
    states = {r["state"] for r in spark.table(t("model_monitoring_alerts")).where("run_id != ''").collect()}
    assert "accepted" in states

    first = reds[0]
    close = action_row(model="churn", alert_id=first["alert_id"], action="closed", reason="fixed upstream",
                       author="bo", created_at="2026-01-05T00:00:00")  # fmt: skip
    spark.sql(insert_statement(table, close))
    for statement in view_statements(f"{catalog}.{test_schema}"):
        spark.sql(statement)
    latest = {r["alert_id"]: r["action"] for r in spark.table(t("model_monitoring_actions_latest")).collect()}
    assert latest[first["alert_id"]] == "closed" and set(latest.values()) == {"closed", "risk_accepted"}
    superseded = spark.table(t("model_monitoring_acceptances")).where(f"alert_id = '{first['alert_id']}'").collect()
    assert [r["status"] for r in superseded] == ["superseded"]
    active = spark.table(t("model_monitoring_acceptances")).where("status = 'active'").count()
    assert int(active) == len(reds) - 1
    coverage = spark.table(t("model_monitoring_coverage")).collect()
    assert [(r["model"], r["periods"]) for r in coverage] == [("churn", 2)] and coverage[0]["days_since_run"] >= 0
    response = spark.table(t("model_monitoring_alert_response")).where(f"alert_id = '{first['alert_id']}'").collect()
    assert response[0]["closed_at"] is not None and response[0]["days_to_resolve"] is not None
    summary = spark.table(t("model_monitoring_response_summary")).collect()
    assert summary[0]["acted"] >= 1 and summary[0]["resolved"] == 1
