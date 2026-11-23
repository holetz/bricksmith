"""Data stability on the selected workspace: Spark equals the pure engine, the gate blocks dependents, reruns merge."""
# ruff: noqa: PT018 - dense assertions on run outcomes

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.graph import load_graph
from bricksmith.monitor.stability import MemoryStore, check, parse_config, view_statements
from bricksmith.monitor.stability.profile import fit_rows_binning, profile_rows
from bricksmith.monitor.stability.spark import fit_spark_binning, profile_spark
from bricksmith.plan import build_plan
from bricksmith.runner import apply_plan
from bricksmith.storage import SparkTableIO

if TYPE_CHECKING:
    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "shared_schema"
DDL = "id INT, month STRING, amount DOUBLE, kind STRING, flag BOOLEAN, region STRING, w DOUBLE"


def synthetic() -> list[tuple[Any, ...]]:
    rows = []
    for m, month in enumerate(("2026-01", "2026-02", "2026-03")):
        for i in range(90):
            amount = None if i % 17 == 0 else 0.0 if i % 6 == 0 else float((i * 13) % 50 + m * 9)
            kind = None if i % 23 == 0 else "abcz"[(i + m) % 4] if m == 2 else "abc"[i % 3]
            rows.append((m * 100 + i, month, amount, kind, i % 4 == 0, "ns"[i % 2], 1.0 + i % 3))
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
        assert all(same(row[c], other[c]) for c in row if c != "computed_at"), (key, row, other)


def test_spark_engine_equals_the_pure_engine(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str
) -> None:
    spark = remote_clients.spark()
    table = f"{remote_context.catalog}.{test_schema}.stability_input"
    spark.createDataFrame(synthetic(), DDL).write.saveAsTable(table)
    frame = spark.table(table)
    rows = as_dicts(synthetic())
    config = parse_config(
        {"period": "month", "exclude": ["id"], "segments": ["region", ["region", "flag"]], "weight": "w",
         "baseline": "previous", "min_rows": 10, "inference": {"numeric_bins": 4}}
    )  # fmt: skip
    pure_bins, pure_skipped = fit_rows_binning(rows, config, ["2026-01"], dataset="d", version=1)
    spark_bins, spark_skipped = fit_spark_binning(frame, config, ["2026-01"], dataset="d", version=1)
    assert spark_bins.to_dict() == pure_bins.to_dict() and spark_skipped == pure_skipped
    assert pure_bins.column("amount").zero and pure_bins.column("kind").kind == "categorical"
    periods = ["2026-01", "2026-02", "2026-03"]
    pure_profile, _ = profile_rows(pure_bins, rows, config, periods, dataset="d")
    spark_profile, _ = profile_spark(spark_bins, frame, config, periods, dataset="d")
    assert_rows_equal(pure_profile, spark_profile, ("period", "segment", "column", "bin"))
    assert {r["segment"] for r in spark_profile} >= {"", "region=n", "region=n,flag=true", "region=s,flag=false"}

    pure = check(rows, config, MemoryStore(), dataset="d", run_id="r", now="t")
    remote = check(frame, config, MemoryStore(), dataset="d", run_id="r", now="t")
    keys = ("period", "segment", "column", "metric")
    assert_rows_equal(pure.tables["results"], remote.tables["results"], keys)
    assert pure.outcome.to_dict() == remote.outcome.to_dict()
    unseen = next(
        r for r in remote.tables["results"]
        if (r["period"], r["segment"], r["column"], r["metric"]) == ("2026-03", "", "kind", "unseen_rate")
    )  # fmt: skip
    assert unseen["value"] > 0.2  # the new category "z" falls outside the fitted bins


def count(spark: Any, name: str) -> int:  # noqa: ANN401
    return int(spark.table(name).count())


def test_declared_stability_gates_the_run_and_feeds_the_views(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str, tmp_path: Path
) -> None:
    spark = remote_clients.spark()
    catalog = remote_context.catalog or ""
    project = tmp_path / "project"
    shutil.copytree(EXAMPLE, project, ignore=shutil.ignore_patterns("__pycache__"))

    def t(name: str) -> str:
        return f"{catalog}.{test_schema}.{name}"

    def run(drift: str) -> Any:  # noqa: ANN401
        context = dataclasses.replace(
            remote_context,
            catalogs={"demo": catalog},
            schemas={"ml": test_schema},
            parameters={**remote_context.parameters, "stability_drift": drift},
        )
        graph = load_graph(project, context=context)
        plan = build_plan(graph, roots=["demo.ml.sales_report"])
        assert not plan.errors
        assert t("stability_results") in {x.physical for e in plan.entries for x in e.targets}
        return apply_plan(plan, graph, project_root=project, execution=context, table_io=SparkTableIO(spark))

    first = run("no")
    assert first.ok, first.to_json()
    outcome = first.results[0].stability[0]
    assert outcome["status"] == "passed" and outcome["periods_profiled"] == 6 and outcome["version"] == 1
    tables = ("stability_binnings", "stability_profiles", "stability_results", "stability_alerts", "stability_runs")
    counts = {n: count(spark, t(n)) for n in tables}
    assert counts["stability_binnings"] == 1 and counts["stability_results"] > 0 and counts["stability_alerts"] == 0
    psi = spark.table(t("stability_results")).where("metric = 'psi'").collect()
    assert psi and all(r["status"] in ("green", "insufficient_volume") for r in psi)
    assert all(abs(r["value"]) < 1e-12 for r in psi if r["value"] is not None)  # identical months

    again = run("no")  # same data: one refreshed month, merged on the same keys
    assert again.ok and again.results[0].stability[0]["periods_profiled"] == 1
    after = {n: count(spark, t(n)) for n in tables}
    assert after == {**counts, "stability_runs": counts["stability_runs"] + 1}

    drifted = run("yes")
    sales, report = drifted.results[-2], drifted.results[-1]
    assert sales.state == "failed" and sales.error and sales.error["code"] == "stability_gate", drifted.to_json()
    assert report.state == "blocked" and report.blocked_by == (sales.task_id,)
    latest = spark.table(t("stability_results")).where("period = '2026-06' AND segment = '' AND metric = 'psi'")
    assert {r["column"]: r["status"] for r in latest.collect()} == {"amount": "red", "promoted": "green"}
    app = spark.table(t("stability_results")).where(
        "period = '2026-06' AND segment = 'channel=app' AND metric = 'volume_change'"
    )
    assert [(r["value"], r["status"]) for r in app.collect()] == [(1.0, "red")]  # 40 -> 80 rows
    open_alerts = spark.table(t("stability_alerts")).where("state = 'open'").collect()
    assert any(a["severity"] == "critical" and a["column"] == "amount" for a in open_alerts)

    for statement in view_statements(f"{catalog}.{test_schema}"):
        spark.sql(statement)
    status = spark.table(t("stability_dataset_status")).where("period = '2026-06'").collect()
    assert [r["status"] for r in status] == ["red"]
    assert count(spark, t("stability_open_alerts")) == len(open_alerts)
    assert {r["period"] for r in spark.table(t("stability_latest")).collect()} == {"2026-06"}
