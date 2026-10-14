"""Monitoring tasks beside the feature and model tasks of this schema (same graph, same runner).

The prediction table is a declared synthetic stand-in: monitoring reads score, label, segment and time columns,
never a model API, so any task that writes such a table can replace ``monitor_predictions``.
"""

from bricksmith.monitor import load_reference, measurement_rows, reference_row, to_frame
from bricksmith.monitor.persist import evaluate_stored, table_rows
from bricksmith.monitor.spark import fit_spark, measure_spark
from bricksmith.tasks import task

JAN = {"column": "snapshot_date", "start": "2026-01-01", "end": "2026-02-01"}
MARCH = {"column": "snapshot_date", "start": "2026-03-01", "end": "2026-04-01"}
PLANS = ("basic", "pro", "pro", "basic", "pro", "basic", "basic", "pro")
JAN_SCORES = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
JAN_LABELS = (0, 0, 0, 1, 0, 1, 1, 1)
MAR_SCORES = (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.99)
MAR_LABELS = (0, 1, 0, 1, None, None, None, None)  # the last four labels have not matured yet
SCORE_RULES = [
    {"name": "score_drift", "metric": "psi", "op": ">", "threshold": 0.25, "severity": "critical", "min_count": 4},
    {"name": "weak_ranking", "metric": "auc", "op": "<", "threshold": 0.6, "severity": "warning", "min_count": 4},
    {"name": "auc_trend", "metric": "auc", "op": "<", "threshold": 0.5, "severity": "info", "min_history": 2},
]
FEATURE_RULES = [{"name": "feature_drift", "metric": "csi", "op": ">", "threshold": 0.25, "severity": "warning"}]


@task(kind="table", metadata={"write": "overwrite"})
def monitor_predictions(ctx):
    """Synthetic prediction table: one score per customer and snapshot, labels only once they have matured."""
    rows = []
    for day, scores, labels in (("2026-01-31", JAN_SCORES, JAN_LABELS), ("2026-03-31", MAR_SCORES, MAR_LABELS)):
        rows += [(i + 1, day, s, y, PLANS[i]) for i, (s, y) in enumerate(zip(scores, labels, strict=True))]
    ddl = "customer_id INT, snapshot_date STRING, score DOUBLE, churned INT, plan STRING"
    ctx.write("monitor_predictions", ctx.io.spark.createDataFrame(rows, ddl))


@task(kind="monitor", metadata={"write": "merge", "keys": ["reference", "version"]})
def monitor_references(ctx):
    """Fit and version the references: feature distributions and the model score with its labels."""
    features = fit_spark(
        ctx.read("customer_features"),
        {"age": {"bins": 4}, "spend": {"bins": 4}, "plan": {"type": "categorical"}},
        name="customer_features", version=1, time="feature_time", fit_source="customer_features",
        fit_window={**JAN, "column": "feature_time"}, alert_policy=FEATURE_RULES,
        evaluation_window={**MARCH, "column": "feature_time"},
    )  # fmt: skip
    scores = fit_spark(
        ctx.read("monitor_predictions"),
        {"score": {"edges": [0, 0.25, 0.5, 0.75, 1.0]}},
        name="churn_scores", version=1, score="score", label="churned", segment="plan", time="snapshot_date",
        fit_source="monitor_predictions", fit_window=JAN, alert_policy=SCORE_RULES, evaluation_window=MARCH,
    )  # fmt: skip
    ctx.write(
        "monitor_references", to_frame(ctx.io.spark, "references", [reference_row(features), reference_row(scores)])
    )


@task(
    kind="monitor",
    metadata={
        "write": "merge",
        "keys": ["reference", "version", "window_start", "window_end", "metric", "subject", "segment"],
    },
)
def monitor_measurements(ctx):
    """Compare the evaluation window of each reference with its fitted bins; bins are never refit here."""
    stored = table_rows(ctx.read("monitor_references"))
    rows = []
    for name, source in (
        ("customer_features", ctx.read("customer_features")),
        ("churn_scores", ctx.read("monitor_predictions")),
    ):
        reference = load_reference(stored, name)
        window = {
            "column": reference.evaluation_window["column"],
            **{k: reference.evaluation_window[k] for k in ("start", "end")},
        }
        rows += measurement_rows(reference, window, measure_spark(reference, source, window))
    ctx.write("monitor_measurements", to_frame(ctx.io.spark, "measurements", rows))


@task(
    kind="monitor",
    outputs=["monitor_evaluations", "monitor_alerts"],
    metadata={
        "write": "merge",
        "keys": ["reference", "version", "window_start", "window_end", "rule", "metric", "subject", "segment"],
        "on_empty": "allow_empty",
    },
)
def monitor_alerts(ctx):
    """Evaluate each reference's alert policy on the stored measurements; every rule state is persisted."""
    stored = table_rows(ctx.read("monitor_references"))
    measurements = table_rows(ctx.read("monitor_measurements"))
    evaluations, alerts = [], []
    for name in ("customer_features", "churn_scores"):
        found, fired = evaluate_stored(load_reference(stored, name), measurements)
        evaluations += found
        alerts += fired
    ctx.write("monitor_evaluations", to_frame(ctx.io.spark, "evaluations", evaluations))
    ctx.write("monitor_alerts", to_frame(ctx.io.spark, "alerts", alerts))
