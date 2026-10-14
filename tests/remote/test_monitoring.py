"""Monitoring on the selected workspace: Spark equals the pure reference, tasks persist, reruns are idempotent."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

import dataclasses
import importlib.util
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.graph import load_graph
from bricksmith.monitor import fit_reference, measure
from bricksmith.monitor.persist import table_rows
from bricksmith.monitor.spark import fit_spark, measure_spark
from bricksmith.plan import build_plan
from bricksmith.runner import apply_plan
from bricksmith.storage import SparkTableIO

if TYPE_CHECKING:
    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "shared_schema"
DDL = "id INT, x DOUBLE, k STRING, s DOUBLE, y INT, g STRING, t STRING"
FIT = [  # id, x, k, s, y, g, t  (x has ties and a null; labels include a missing one)
    (1, 1.0, "a", 0.1, 0, "p", "2026-01-03"), (2, 2.0, "a", 0.2, 0, "p", "2026-01-04"),
    (3, 2.0, "b", 0.4, 1, "q", "2026-01-05"), (4, 3.0, "b", 0.4, 0, "q", "2026-01-06"),
    (5, 4.0, "a", 0.7, 1, "p", "2026-01-07"), (6, 5.0, "c", 0.9, 1, "q", "2026-01-08"),
    (7, None, None, 0.6, None, "p", "2026-01-09"), (8, 8.0, "a", 0.8, 1, "q", "2026-01-10"),
]  # fmt: skip
NOW = [  # unseen category, out-of-range x, null, a segment without labels, another month
    (11, 0.0, "a", 0.5, 1, "p", "2026-02-02"), (12, 9.0, "z", 0.5, 0, "p", "2026-02-03"),
    (13, 3.0, None, 0.95, 1, "q", "2026-02-04"), (14, None, "b", 0.95, 0, "q", "2026-02-05"),
    (15, 2.0, "a", 0.3, None, "r", "2026-03-06"), (16, 4.5, "c", None, 1, "r", "2026-03-07"),
]  # fmt: skip
COLUMNS = {"x": {"bins": 3}, "k": {"type": "categorical"}, "s": {"edges": [0, 0.5, 1]}}
META: dict[str, Any] = {
    "name": "tiny", "version": 1, "score": "s", "label": "y", "segment": "g", "time": "t",
    "metric_config": {"stability_period": "month", "calibration_bins": 2},
}  # fmt: skip


def rows_of(data: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    return [dict(zip(("id", "x", "k", "s", "y", "g", "t"), r, strict=True)) for r in data]


def close(a: Any, b: Any) -> bool:  # noqa: ANN401
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(close(a[k], b[k]) for k in a)
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) < 1e-9
    return bool(a == b)


def test_spark_equals_the_pure_reference(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str
) -> None:
    spark = remote_clients.spark()
    table = f"{remote_context.catalog}.{test_schema}.tiny"
    spark.createDataFrame(FIT + NOW, DDL).write.saveAsTable(table)
    frame = spark.table(table)
    window = {"column": "t", "start": "2026-01-01", "end": "2026-02-01"}
    pure_ref = fit_reference(rows_of(FIT + NOW), COLUMNS, fit_window=window, **META)
    spark_ref = fit_spark(frame, COLUMNS, fit_window=window, **META)
    assert spark_ref.to_dict() == pure_ref.to_dict() and spark_ref.fingerprint == pure_ref.fingerprint
    assert pure_ref.fit_rows == 8 and pure_ref.column("x").counts["null"] == 1

    for scope in ({"column": "t", "start": "2026-02-01", "end": "2026-04-01"}, window, None):
        expected = {m.key: m for m in measure(pure_ref, rows_of(FIT + NOW), scope)}
        actual = {m.key: m for m in measure_spark(spark_ref, frame, scope)}
        assert expected.keys() == actual.keys()
        for key, want in expected.items():
            got = actual[key]
            assert (got.status, got.reason, got.count) == (want.status, want.reason, want.count), key
            assert (got.value is None) is (want.value is None), key
            assert got.value is None or abs(got.value - float(want.value or 0)) < 1e-9, key
            assert close(dict(got.detail), dict(want.detail)), key
    wanted = {
        m.key: m for m in measure(pure_ref, rows_of(NOW), {"column": "t", "start": "2026-02-01", "end": "2026-04-01"})
    }
    assert wanted[("auc", "s", "r")].reason == "missing_scores"  # r: one unlabeled row, one labeled row without score
    assert wanted[("csi", "k", "")].detail["bins"]["unseen"]["current"] > 0  # an unseen category
    assert wanted[("stability", "s", "")].value is not None  # two months in the window


def count(spark: Any, name: str) -> int:  # noqa: ANN401
    return int(spark.table(name).count())


def test_monitor_tasks_persist_and_rerun_is_idempotent(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str, tmp_path: Path
) -> None:
    spark = remote_clients.spark()
    catalog = remote_context.catalog or ""
    project = tmp_path / "project"
    shutil.copytree(EXAMPLE, project, ignore=shutil.ignore_patterns("__pycache__"))
    context = dataclasses.replace(remote_context, catalogs={"demo": catalog}, schemas={"ml": test_schema})
    graph = load_graph(project, context=context)
    assert graph.ok
    plan = build_plan(graph, roots=["demo.ml.monitor_alerts"])  # the monitor tasks and everything they read
    assert not plan.errors

    def run() -> None:
        report = apply_plan(plan, graph, project_root=project, execution=context, table_io=SparkTableIO(spark))
        assert report.ok, report.to_json()

    def t(name: str) -> str:
        return f"{catalog}.{test_schema}.{name}"

    run()
    first = {
        n: count(spark, t(n))
        for n in ("monitor_references", "monitor_measurements", "monitor_evaluations", "monitor_alerts")
    }
    assert first["monitor_references"] == 2 and all(v > 0 for v in first.values())
    measurements = table_rows(spark.table(t("monitor_measurements")))
    by_key = {(r["reference"], r["metric"], r["subject"], r["segment"]): r for r in measurements}
    # Same data and definitions as the pure engine on the example's synthetic prediction table.
    spec = importlib.util.spec_from_file_location("example_monitoring", project / "catalogs/demo/ml/monitoring.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = rows_of_example(mod)
    ref = fit_reference(
        data, {"score": {"edges": [0, 0.25, 0.5, 0.75, 1.0]}}, name="churn_scores", version=1, score="score",
        label="churned", segment="plan", time="snapshot_date", fit_window=mod.JAN, alert_policy=mod.SCORE_RULES,
        evaluation_window=mod.MARCH,
    )  # fmt: skip
    for m in measure(ref, data, mod.MARCH):
        row = by_key[("churn_scores", m.metric, m.subject, m.segment)]
        assert (row["status"], row["reason"], row["count"]) == (m.status, m.reason, m.count), m.key
        assert (row["value"] is None) is (m.value is None) and (m.value is None or abs(row["value"] - m.value) < 1e-9)
    assert abs(by_key[("customer_features", "csi", "age", "")]["value"]) < 1e-9  # identical customers in both snapshots
    alerts = table_rows(spark.table(t("monitor_alerts")))
    drift = next(a for a in alerts if a["rule"] == "score_drift")
    assert (drift["severity"], drift["error_class"], drift["kind"]) == (
        "critical",
        "statistical_alert",
        "monitor_threshold",
    )
    assert json.loads(drift["details"])["version"] == 1
    states = {(r["rule"], r["state"]) for r in table_rows(spark.table(t("monitor_evaluations")))}
    assert ("auc_trend", "insufficient_history") in states
    before = sorted(map(str, table_rows(spark.table(t("monitor_measurements")))))

    run()  # a repeat run merges on the same keys: nothing is added or changed
    assert {n: count(spark, t(n)) for n in first} == first
    assert sorted(map(str, table_rows(spark.table(t("monitor_measurements"))))) == before


def rows_of_example(mod: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    return [
        {"snapshot_date": day, "score": s, "churned": y, "plan": mod.PLANS[i]}
        for day, scores, labels in (
            ("2026-01-31", mod.JAN_SCORES, mod.JAN_LABELS),
            ("2026-03-31", mod.MAR_SCORES, mod.MAR_LABELS),
        )
        for i, (s, y) in enumerate(zip(scores, labels, strict=True))
    ]
