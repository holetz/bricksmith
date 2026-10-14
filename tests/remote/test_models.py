"""Model lifecycle on the selected workspace: train, register on Unity Catalog, alias, predict from the pin, retire."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.graph import load_graph
from bricksmith.mlops.adapter import get_adapter
from bricksmith.mlops.errors import ModelAmbiguousError, ModelInUseError, ModelNotFoundError
from bricksmith.mlops.lifecycle import (
    apply_alias_plan,
    apply_retirement,
    plan_promotion,
    plan_retirement,
    plan_rollback,
)
from bricksmith.mlops.store import ModelStore
from bricksmith.mlops.training import predict_in_subprocess, predict_rows, register_run, relog_model
from bricksmith.mlops.validate import rows_from
from bricksmith.plan import build_plan
from bricksmith.runner import apply_plan
from bricksmith.storage import SparkTableIO

if TYPE_CHECKING:
    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "shared_schema"


def run_tasks(project: Path, context: Any, spark: Any, roots: list[str], *, upstream: bool) -> None:  # noqa: ANN401
    graph = load_graph(project, context=context)
    plan = build_plan(graph, roots=roots, upstream=upstream, partial=not upstream)
    assert not plan.errors
    report = apply_plan(plan, graph, project_root=project, execution=context, table_io=SparkTableIO(spark))
    assert report.ok, report.to_json()


def test_train_register_alias_predict_evaluate_retire(  # noqa: PLR0915 - one end-to-end scenario
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str, tmp_path: Path
) -> None:
    spark = remote_clients.spark()
    catalog = remote_context.catalog or ""
    me = remote_clients.workspace().current_user.me().user_name  # type: ignore[attr-defined]
    experiment = f"/Users/{me}/{test_schema}"
    name = f"{catalog}.{test_schema}.churn_v1"
    project = tmp_path / "project"
    shutil.copytree(EXAMPLE, project, ignore=shutil.ignore_patterns("__pycache__"))
    context = dataclasses.replace(
        remote_context,
        catalogs={"demo": catalog},
        schemas={"ml": test_schema},
        parameters={"mlflow_experiment": experiment},
    )
    store = ModelStore.from_clients(remote_clients)
    adapter = get_adapter("sklearn")

    def table(short: str) -> str:
        return f"{catalog}.{test_schema}.{short}"

    try:
        # Prediction before any model exists fails without fitting anything.
        with pytest.raises(ModelNotFoundError):
            store.resolve(name, "churn_v1", alias="champion")
        with pytest.raises(ModelAmbiguousError):
            store.resolve(name, "churn_v1")
        assert store.registered(name) is None and store.client.get_experiment_by_name(experiment) is None

        # Datasets and the train task run through the shared runner.
        run_tasks(project, context, spark, ["demo.ml.churn_train"], upstream=True)
        (record,) = spark.table(table("churn_training_run")).collect()
        assert record["model"] == "churn_v1" and record["registered_name"] == name
        assert len(record["training_fingerprint"]) == 64
        metrics = json.loads(record["metrics"])
        assert metrics["train"]["rows"] == 5.0 and metrics["evaluation"]["rows"] == 5.0
        run = store.client.get_run(record["run_id"])
        assert run.data.tags["bricksmith_model"] == "churn_v1" and "evaluation_accuracy" in run.data.metrics
        datasets = json.loads(run.data.tags["bricksmith_datasets"])
        assert len(datasets["training"]["inputs"]) == 3 and all(
            isinstance(v, int) for v in datasets["training"]["inputs"].values()
        )

        # Registration on Unity Catalog; explicit alias; a task bound to a missing alias fails without fitting.
        version = register_run(store, name, "churn_v1", record["run_id"])
        assert version == 1
        with pytest.raises(ModelNotFoundError):
            store.resolve(name, "churn_v1", alias="champion")
        graph = load_graph(project, context=context)
        promote = plan_promotion(
            store, name, "churn_v1", "champion", 1, reviewer="ci", reason="first candidate", graph=graph
        )
        assert promote.impact.alias_bound == ("demo.ml.churn_evaluate", "demo.ml.churn_predict")
        apply_alias_plan(store, promote)
        assert store.aliases(name) == {"champion": 1}

        # Predict and evaluate tasks follow the alias; the persisted table carries the exact version.
        run_tasks(project, context, spark, ["demo.ml.churn_predict", "demo.ml.churn_evaluate"], upstream=False)
        scores = [r.asDict() for r in spark.table(table("churn_scores")).orderBy("customer_id").collect()]
        assert [r["customer_id"] for r in scores] == [1, 4, 5, 7, 8]
        assert all(r["model"] == "churn_v1" and r["model_version"] == 1 and 0 <= r["score"] <= 1 for r in scores)
        measured = {r["metric"]: r["value"] for r in spark.table(table("churn_evaluation")).collect()}
        assert (
            measured["rows"] == 5.0
            and measured["positive_rate"] == 0.6
            and measured["accuracy"] == pytest.approx(metrics["evaluation"]["accuracy"])
        )

        # A fresh interpreter loads the pinned version and reproduces the scores.
        features = rows_from(spark.table(table("churn_prediction")).orderBy("customer_id").collect())
        resolved = store.resolve(name, "churn_v1", version=1)
        fresh = predict_in_subprocess(store, resolved, features)
        assert [round(o["score"], 9) for o in fresh] == [round(r["score"], 9) for r in scores]
        assert [o["prediction"] for o in fresh] == [r["prediction"] for r in scores]

        # Deliberate re-log keeps identity and predictions, adds version 2 and leaves the alias alone.
        result = relog_model(store, adapter, resolved, features, experiment=experiment)
        assert (result.from_version, result.to_version) == (1, 2) and result.max_abs_diff == 0.0
        assert store.aliases(name) == {"champion": 1}
        from bricksmith.mlops.spec import load_spec_file  # noqa: PLC0415

        spec = load_spec_file(project / "models/churn_v1.toml")
        again = predict_rows(store, adapter, spec, features, name=name, version=2)
        assert [round(o["score"], 9) for o in again] == [round(r["score"], 9) for r in scores]

        # Promote the re-logged version, roll back, and retire with a consumer check.
        apply_alias_plan(
            store,
            plan_promotion(store, name, "churn_v1", "champion", 2, reviewer="ci", reason="re-logged", graph=graph),
        )
        assert store.aliases(name) == {"champion": 2}
        apply_alias_plan(
            store, plan_rollback(store, name, "churn_v1", "champion", reviewer="ci", reason="check rollback")
        )
        assert store.aliases(name) == {"champion": 1}
        blocked = plan_retirement(store, name, "churn_v1", graph=graph, versions=[1])
        assert blocked.blocked and len(blocked.consumers) == 2
        with pytest.raises(ModelInUseError):
            apply_retirement(store, blocked)
        apply_alias_plan(
            store, plan_promotion(store, name, "churn_v1", "champion", 2, reviewer="ci", reason="move consumers")
        )
        first = plan_retirement(store, name, "churn_v1", graph=graph, versions=[1])
        assert not first.blocked
        apply_retirement(store, first)
        with pytest.raises(ModelNotFoundError):
            store.resolve(name, "churn_v1", version=1)
        shutil.rmtree(project / "catalogs/demo/ml/__pycache__", ignore_errors=True)
        (project / "catalogs/demo/ml/churn_model.py").unlink()  # no task consumes the model any more
        idle = plan_retirement(store, name, "churn_v1", graph=load_graph(project), versions=[2])
        assert not idle.blocked and idle.remove_aliases == {"champion": 2}
        apply_retirement(store, idle)
        assert store.aliases(name) == {} and run.data.tags["bricksmith_model"] == "churn_v1"
        assert store.client.get_run(record["run_id"]).info.status == "FINISHED"  # training artifacts stay protected
    finally:
        store.delete_model(name)
        store.delete_experiment(experiment)
    assert store.registered(name) is None
