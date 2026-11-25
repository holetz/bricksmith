"""The end-to-end model flow example on the selected workspace, every stage through the shared runner.

The test installs the JVM feature kernel in a test-owned volume, runs the example from the synthetic source to the
trained run, attempts registration, scores with an explicit pin, evaluates and publishes the technical document,
then checks every stage's tables. A capability the workspace refuses (kernel registration, model registration) is
recorded as Unsupported with the platform's reason; any other error fails. Each stage's outcome goes to stderr for
the evidence page. The experiment and the registered model (a refused registration still creates it) are deleted
in ``finally``; the schema fixture verifies that the schema, the volume and the kernel function are gone.
"""
# ruff: noqa: PT018 - dense assertions on run outcomes

from __future__ import annotations

import dataclasses
import json
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.features.kernel import install_uc_function
from bricksmith.graph import load_graph
from bricksmith.io import files_api_client
from bricksmith.mlops.documentation import DOCUMENT_MARKDOWN
from bricksmith.mlops.lifecycle import apply_alias_plan, plan_promotion
from bricksmith.mlops.stages.config import PREDICTION_COLUMNS
from bricksmith.mlops.store import ModelStore
from bricksmith.mlops.training import register_run
from bricksmith.monitor.model.tables import view_statements
from bricksmith.plan import build_plan
from bricksmith.runner import apply_plan
from bricksmith.storage import SparkTableIO

if TYPE_CHECKING:
    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext
    from bricksmith.runner.report import RunReport

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "model_flow"
DEVELOPMENT_ROOTS = [
    "demo.flow.attrition_train",
    "demo.flow.attrition_selection_ledger",
    "demo.flow.attrition_selection_variables",
]
SCORING_ROOTS = ["demo.flow.attrition_scores", "demo.flow.attrition_evaluation"]


def stage(name: str, state: str, detail: str = "") -> None:
    sys.stderr.write(f"STAGE {name}: {state}{f' ({detail})' if detail else ''}\n")


def first_line(error: BaseException) -> str:
    lines = str(error).strip().splitlines()
    return f"{type(error).__name__}: {lines[0][:300] if lines else ''}"


def run_tasks(project: Path, context: Any, spark: Any, roots: list[str], *, upstream: bool) -> RunReport:  # noqa: ANN401
    graph = load_graph(project, context=context)
    plan = build_plan(graph, roots=roots, upstream=upstream, partial=not upstream)
    assert not plan.errors, plan.errors
    report = apply_plan(plan, graph, project_root=project, execution=context, table_io=SparkTableIO(spark))
    for result in report.results:
        stage(result.task_id, result.state, result.reason or (result.error or {}).get("message", "")[:300])
        for choice in result.feature_engines:
            sys.stderr.write(f"ENGINE {result.task_id}: {json.dumps(dict(choice), sort_keys=True)}\n")
    assert report.ok, report.to_json()
    return report


def test_the_model_flow_example_runs_end_to_end(  # noqa: PLR0915 - one end-to-end scenario
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str, jar_volume: str, tmp_path: Path
) -> None:
    spark = remote_clients.spark()
    catalog = remote_context.catalog or ""
    me = remote_clients.workspace().current_user.me().user_name  # type: ignore[attr-defined]
    experiment = f"/Users/{me}/{test_schema}"
    name = f"{catalog}.{test_schema}.attrition"
    project = tmp_path / "project"
    shutil.copytree(EXAMPLE, project, ignore=shutil.ignore_patterns("__pycache__"))
    store = ModelStore.from_clients(remote_clients)

    def table(short: str) -> list[dict[str, Any]]:
        return [r.asDict() for r in spark.table(f"{catalog}.{test_schema}.{short}").collect()]

    function = f"{catalog}.{test_schema}.bricksmith_window_kernel"
    try:
        install_uc_function(spark, files_api_client(remote_clients), jar_volume, function)
    except Exception as error:  # noqa: BLE001 - an installation refusal is the outcome being recorded
        stage("kernel install", "Unsupported", first_line(error))
        function = ""
    else:
        stage("kernel install", "passed", function.rsplit(".", 1)[1])
    context = dataclasses.replace(
        remote_context,
        catalogs={"demo": catalog},
        schemas={"flow": test_schema},
        parameters={"mlflow_experiment": experiment, "kernel_function": function},
    )
    registered, version = False, 0
    try:
        # Source, feature tables with stability, development stages, selection and training.
        report = run_tasks(project, context, spark, DEVELOPMENT_ROOTS, upstream=True)
        engines = [c for r in report.results for c in r.feature_engines]
        assert engines and all(c["requested"] == "auto" for c in engines)
        if function:
            assert all(c["engine"] == "jvm" for c in engines), engines
        else:
            assert all(c["engine"] == "spark" and c["reason"] for c in engines), engines

        source = table("accounts_monthly")
        accounts = table("account_features")
        assert len(accounts) == len(source) and len(table("customer_features")) == len(
            {(r["customer_id"], r["closing_date"]) for r in source}
        )
        assert {r["dataset"] for r in table("stability_results")} >= {
            f"{catalog}.{test_schema}.account_features",
            f"{catalog}.{test_schema}.customer_features",
        }
        population = table("attrition_population")
        assert len(population) == len(source) and any(r["excluded"] for r in population)
        assert {r["attrited"] for r in table("attrition_labels")} == {0, 1}
        training = table("attrition_training")
        assert {r["sample"] for r in training} == {"dev", "oot"} and not any(r["excluded"] for r in training)
        assert set(json.loads(training[0]["feature_reads"])) == {
            f"{catalog}.{test_schema}.account_features",
            f"{catalog}.{test_schema}.customer_features",
        }
        variables = table("attrition_selection_variables")
        selected = {r["variable"] for r in variables if r["selected"]}
        assert selected and table("attrition_selection_ledger")
        (record,) = table("attrition_training_run")
        run_id = record["run_id"]
        assert set(json.loads(record["features"])) <= selected
        assert store.client.get_run(run_id).info.status == "FINISHED"
        stage("training run", "passed", f"{len(json.loads(record['features']))} features")

        # Registration and the alias are a deliberate step; the free tier refuses to register a version (spec 88).
        try:
            version = register_run(store, name, "attrition", run_id)
        except Exception as error:  # noqa: BLE001 - a registration refusal is the outcome being recorded
            stage("registration", "Unsupported", first_line(error))
            scoring = {"model_run": run_id}
        else:
            registered = True
            apply_alias_plan(
                store,
                plan_promotion(store, name, "attrition", "champion", version, reviewer="ci", reason="model flow"),
            )
            stage("registration", "passed", f"version {version}, alias champion")
            scoring = {}

        # Scoring pinned explicitly, model monitoring on the prediction table, evaluation and the document.
        pinned = dataclasses.replace(context, parameters={**context.parameters, **scoring})
        report = run_tasks(project, pinned, spark, SCORING_ROOTS, upstream=False)
        scores = table("attrition_scores")
        assert {r["sample"] for r in scores} == {"dev", "oot", "live"}
        assert set(PREDICTION_COLUMNS) | {"attrited"} <= set(scores[0])
        expected = str(version) if registered else f"run:{run_id}"
        assert {r["model_version"] for r in scores} == {expected}
        assert all(0.0 <= r["score"] <= 1.0 and r["grade"] in {"A", "B", "C", "D"} for r in scores)
        (monitoring,) = next(r for r in report.results if r.task_id.endswith("attrition_scores")).model_monitoring
        stage("model monitoring", "passed", json.dumps(dict(monitoring), sort_keys=True, default=str)[:300])
        assert table("model_monitoring_binnings")
        measured = [r for r in table("model_monitoring_results") if r["value"] is not None]
        by_metric: dict[str, list[dict[str, Any]]] = {}
        for r in measured:
            by_metric.setdefault(r["metric"], []).append(r)
        missing = {"psi", "csi", "ks", "auc", "gini", "grade_oe", "grade_jeffreys_p", "grade_binomial_p"} - set(
            by_metric
        )
        assert not missing, f"no measured value for {sorted(missing)}"
        assert {r["column"] for r in by_metric["grade_oe"]} >= {f"grade={g}" for g in "ABCD"}
        assert all(0.0 <= r["value"] <= 1.0 for r in by_metric["auc"])
        assert all(r["lag"] == 3 for r in by_metric["ks"]), "label-based metrics respect the declared label lag"
        runs = table("model_monitoring_runs")
        assert runs and all(r["status"] in {"success", "partial"} for r in runs), runs
        assert all(r["model"] == "attrition" for r in runs)
        table("model_monitoring_alerts")  # readable, possibly empty when no light turned red
        for statement in view_statements(f"{catalog}.{test_schema}"):
            spark.sql(statement)
        for view in ("latest", "model_status", "open_alerts", "coverage", "response_summary", "acceptances"):
            spark.table(f"{catalog}.{test_schema}.model_monitoring_{view}").collect()
        (coverage,) = table("model_monitoring_coverage")
        assert coverage["model"] == "attrition" and coverage["periods"] >= 1
        assert table("model_monitoring_model_status")
        stage("model monitoring metrics", "passed", f"{len(measured)} measured results, {len(runs)} run(s)")
        results = table("attrition_evaluation_results")
        assert {r["sample"] for r in results} >= {"dev", "oot"} and table("attrition_evaluation_grades")
        assert {r["run_id"] for r in results} == {run_id}

        landing = tmp_path / "document"
        landing.mkdir()
        document = Path(store.client.download_artifacts(run_id, DOCUMENT_MARKDOWN, str(landing))).read_text()
        assert "## Feature sources" in document and "### Coefficients" in document
        stage("technical document", "passed", f"{len(document)} characters")
        stage("feature engineering adapter", "not run", "the remote group does not install the features extra")
    finally:
        try:
            store.delete_model(name)  # a refused registration still created the registered model; absent is a no-op
        finally:  # independent cleanup: a failing model deletion still removes the experiment
            store.delete_experiment(experiment)
    assert store.registered(name) is None
