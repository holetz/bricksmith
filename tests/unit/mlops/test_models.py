"""Offline tests for model training, registration, explicit resolution, artifacts and lifecycle (fake tracking client)."""
# ruff: noqa: ANN401, ARG001, ARG002, E501, PT018 - dense assertions on hand-derived values; fakes mirror the client API

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bricksmith.cli.main import main
from bricksmith.errors import EXIT_OK, StalePlanError
from bricksmith.graph import load_graph
from bricksmith.mlops import load_spec_file
from bricksmith.mlops.adapter import Contract, _auc, contract_of, get_adapter
from bricksmith.mlops.errors import (
    FeatureContractError,
    ModelAmbiguousError,
    ModelError,
    ModelIncompatibleError,
    ModelInUseError,
    ModelNotFoundError,
)
from bricksmith.mlops.lifecycle import (
    alias_impact,
    apply_alias_plan,
    apply_retirement,
    downstream,
    model_uses,
    plan_promotion,
    plan_retirement,
    plan_rollback,
)
from bricksmith.mlops.meta import FILE, ModelMetadata, check_runtime, render_card
from bricksmith.mlops.store import ModelStore, check_selector, registered_name
from bricksmith.mlops.tasks import parse_model_task
from bricksmith.mlops.training import (
    evaluate_rows,
    predict_rows,
    register_run,
    relog_model,
    train_model,
)

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
SHARED = EXAMPLES / "shared_schema"
CHURN = load_spec_file(SHARED / "models/churn_v1.toml")
UPSELL = load_spec_file(SHARED / "models/upsell_v1.toml")
NAME = "main.sandbox.churn_v1"


class FakeTracking:
    """In-memory stand-in for the tracking client: experiments, runs with artifacts, registered models, aliases."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.experiments: dict[str, str] = {}
        self.runs: dict[str, SimpleNamespace] = {}
        self.models: dict[str, SimpleNamespace] = {}
        self.versions: dict[tuple[str, int], SimpleNamespace] = {}

    def get_experiment_by_name(self, name: str) -> SimpleNamespace | None:
        return (
            SimpleNamespace(experiment_id=self.experiments[name], lifecycle_stage="active")
            if name in self.experiments
            else None
        )

    def create_experiment(self, name: str) -> str:
        self.experiments[name] = f"e{len(self.experiments) + 1}"
        return self.experiments[name]

    def delete_experiment(self, experiment_id: str) -> None:
        self.experiments = {k: v for k, v in self.experiments.items() if v != experiment_id}

    def create_run(self, experiment_id: str, run_name: str = "", tags: dict[str, str] | None = None) -> SimpleNamespace:
        run_id = f"run{len(self.runs) + 1}"
        data = SimpleNamespace(tags=dict(tags or {}), metrics={}, params={})
        self.runs[run_id] = SimpleNamespace(info=SimpleNamespace(run_id=run_id, status="RUNNING"), data=data)
        return self.runs[run_id]

    def get_run(self, run_id: str) -> SimpleNamespace:
        return self.runs[run_id]

    def log_param(self, run_id: str, key: str, value: str) -> None:
        self.runs[run_id].data.params[key] = value

    def log_metric(self, run_id: str, key: str, value: float) -> None:
        self.runs[run_id].data.metrics[key] = value

    def log_artifacts(self, run_id: str, local_dir: str, artifact_path: str) -> None:
        shutil.copytree(local_dir, self.root / run_id / artifact_path)

    def list_artifacts(self, run_id: str, path: str = "") -> list[SimpleNamespace]:
        directory = self.root / run_id / path
        if not directory.is_dir():
            return []
        return [
            SimpleNamespace(path=file.relative_to(self.root / run_id).as_posix(), is_dir=file.is_dir())
            for file in sorted(directory.iterdir())
        ]

    def set_terminated(self, run_id: str, status: str) -> None:
        self.runs[run_id].info.status = status

    def get_registered_model(self, name: str) -> SimpleNamespace:
        if name not in self.models:
            msg = f"registered model {name} does not exist"
            raise LookupError(msg)
        return self.models[name]

    def create_registered_model(self, name: str, tags: dict[str, str] | None = None) -> None:
        self.models[name] = SimpleNamespace(tags=dict(tags or {}), aliases={})

    def create_model_version(self, name: str, source: str, run_id: str, tags: dict[str, str]) -> SimpleNamespace:
        prefix = f"runs:/{run_id}/"
        if not source.startswith(prefix):
            msg = "registration must read the run's persisted artifact source"
            raise ValueError(msg)
        directory = self.root / run_id / source.removeprefix(prefix)
        if not directory.is_dir() or not all((directory / file).is_file() for file in (FILE, "model.json")):
            msg = "registration source is not a persisted bricksmith model"
            raise FileNotFoundError(msg)
        ModelMetadata.from_json((directory / FILE).read_text(encoding="utf-8"))
        payload = json.loads((directory / "model.json").read_text(encoding="utf-8"))
        number = 1 + sum(1 for n, _ in self.versions if n == name)
        self.versions[name, number] = SimpleNamespace(
            version=str(number), run_id=run_id, tags=dict(tags), status="READY", source=source, payload=payload
        )
        return self.versions[name, number]

    def get_model_version(self, name: str, version: str) -> SimpleNamespace:
        return self.versions[name, int(version)]

    def search_model_versions(self, query: str) -> list[SimpleNamespace]:
        name = query.split("'")[1]
        return [v for (n, _), v in sorted(self.versions.items()) if n == name]

    def set_registered_model_alias(self, name: str, alias: str, version: str) -> None:
        self.models[name].aliases[alias] = version

    def delete_registered_model_alias(self, name: str, alias: str) -> None:
        del self.models[name].aliases[alias]

    def set_model_version_tag(self, name: str, version: str, key: str, value: str) -> None:
        self.versions[name, int(version)].tags[key] = value

    def set_registered_model_tag(self, name: str, key: str, value: str) -> None:
        self.models[name].tags[key] = value

    def delete_model_version(self, name: str, version: str) -> None:
        del self.versions[name, int(version)]

    def delete_registered_model(self, name: str) -> None:
        del self.models[name]


class ThresholdAdapter:
    """Counts fits; the model is the mean of the first feature of the training rows (a cut-off)."""

    name = "threshold"

    def __init__(self, deps: dict[str, str] | None = None, drift: float = 0.0) -> None:
        self.fits = 0
        self.deps = deps or {"python": "3.12.1", "threshold": "1.0"}
        self.drift = drift

    def train(self, spec: Any, rows: Any, params: Any) -> object:
        self.fits += 1
        name = spec.features[0].name
        return {"cut": sum(r[name] for r in rows) / len(rows), "feature": name}

    def predict(self, model: Any, features: Contract, rows: Any) -> list[dict[str, Any]]:
        for row in rows:
            if model["feature"] not in row:
                msg = "feature missing"
                raise FeatureContractError(msg)
        return [
            {
                "score": float(r[model["feature"]] >= model["cut"]) + self.drift,
                "prediction": int(r[model["feature"]] >= model["cut"]),
            }
            for r in rows
        ]

    def evaluate(self, model: Any, features: Contract, label: str, rows: Any) -> dict[str, float | None]:
        out = self.predict(model, features, rows)
        return {"accuracy": sum(o["prediction"] == r[label] for o, r in zip(out, rows, strict=True)) / len(rows)}

    def save(self, model: Any, path: Path, features: Contract, rows: Any) -> None:
        path.mkdir(parents=True)
        (path / "model.json").write_text(json.dumps(model), encoding="utf-8")

    def load(self, path: Path) -> object:
        return json.loads((path / "model.json").read_text(encoding="utf-8"))

    def dependencies(self) -> dict[str, str]:
        return self.deps


def make_store(tmp_path: Path) -> tuple[ModelStore, FakeTracking]:
    client = FakeTracking(tmp_path / "artifacts")

    def download(name: str, version: int, dest: Path) -> Path:
        run_id = client.versions[name, version].run_id
        return Path(shutil.copytree(client.root / run_id / "model", dest / f"{name}-{version}"))

    return ModelStore(client, downloader=download, sleep=lambda _: None), client


def training_rows(spec: Any = CHURN) -> list[dict[str, Any]]:
    """Five train rows (snapshot Jan) and five evaluation rows (snapshot Mar); spend 50..300, churn when high spend."""
    spends = [(1, 12, 50.0, 0), (4, 8, 120.0, 1), (5, 60, 300.0, 1), (7, 24, 99.0, 0), (8, 18, 100.0, 1)]
    rows = []
    for day, label_day, split in (("2026-01-31", "2026-03-02", "train"), ("2026-03-31", "2026-04-30", "evaluation")):
        rows += [
            {
                "customer_id": c, "snapshot_date": day, "segment": "standard", "feature_time": day,
                "tenure_months": t, "spend": s, "churned": y, "label_time": label_day, "split": split,
            }
            for c, t, s, y in spends
        ]  # fmt: skip
    return rows


def train(store: ModelStore, adapter: ThresholdAdapter, spec: Any = CHURN, **extra: Any) -> str:
    return train_model(store, adapter, spec, training_rows(), experiment="/exp/churn", **extra).run_id


def test_silent_artifact_upload_cannot_finish_a_training_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, client = make_store(tmp_path)
    monkeypatch.setattr(client, "log_artifacts", lambda *_args: None)
    with pytest.raises(ModelError, match="without persisting 2 required files"):
        train(store, ThresholdAdapter())
    assert client.get_run("run1").info.status == "FAILED"
    assert not client.models and not client.versions


def test_upload_inventory_requires_every_nested_payload_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, client = make_store(tmp_path)
    payload = tmp_path / "payload"
    (payload / "nested").mkdir(parents=True)
    (payload / "nested" / "weights.bin").write_bytes(b"synthetic weights")
    (payload / "metadata.json").write_text("{}", encoding="utf-8")
    log = client.log_artifacts

    def incomplete_upload(run_id: str, local_dir: str, artifact_path: str) -> None:
        log(run_id, local_dir, artifact_path)
        (client.root / run_id / artifact_path / "nested" / "weights.bin").unlink()

    monkeypatch.setattr(client, "log_artifacts", incomplete_upload)
    with pytest.raises(ModelError, match="without persisting 1 required files"):
        store.log_run("/exp/partial", run_name="partial", tags={}, params={}, metrics={}, artifact_dir=payload)
    assert client.get_run("run1").info.status == "FAILED"

    monkeypatch.setattr(client, "log_artifacts", log)
    run_id = store.log_run("/exp/partial", run_name="complete", tags={}, params={}, metrics={}, artifact_dir=payload)
    assert client.get_run(run_id).info.status == "FINISHED"
    assert (client.root / run_id / "model" / "nested" / "weights.bin").read_bytes() == b"synthetic weights"


def test_inventory_transport_failure_has_safe_error_and_preserves_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client = make_store(tmp_path)
    transport = RuntimeError("https://private-workspace.invalid token=synthetic-secret")

    def unavailable(run_id: str, path: str) -> list[SimpleNamespace]:
        raise transport

    monkeypatch.setattr(client, "list_artifacts", unavailable)
    with pytest.raises(ModelError, match="unable to verify persisted model artifacts \\(RuntimeError\\)") as caught:
        train(store, ThresholdAdapter())
    assert caught.value.__cause__ is transport
    assert "synthetic-secret" not in str(caught.value) and "private-workspace" not in str(caught.value.to_dict())
    assert client.get_run("run1").info.status == "FAILED"


def test_registration_reads_persisted_payload_instead_of_accepting_a_source_string(tmp_path: Path) -> None:
    store, client = make_store(tmp_path)
    run_id = train(store, ThresholdAdapter())
    (client.root / run_id / "model" / "model.json").unlink()
    with pytest.raises(FileNotFoundError, match="not a persisted bricksmith model"):
        register_run(store, NAME, CHURN.model, run_id)
    assert not client.versions


def test_registration_source_must_belong_to_the_supplied_run(tmp_path: Path) -> None:
    store, client = make_store(tmp_path)
    run_id = train(store, ThresholdAdapter())
    with pytest.raises(ValueError, match="must read the run's persisted artifact source"):
        client.create_model_version(NAME, "runs:/foreign-run/model", run_id, {})
    assert not client.versions


def test_training_records_datasets_contract_parameters_metrics_dependencies_and_revision(tmp_path: Path) -> None:
    store, client = make_store(tmp_path)
    datasets = {
        "training": {"fingerprint": "ab" * 32, "config": CHURN.config_hash(), "inputs": {"c.s.t": 3}, "rows": 10}
    }
    result = train_model(
        store, ThresholdAdapter(), CHURN, training_rows(), experiment="/exp/churn", params={"depth": 2},
        datasets=datasets, card={"summary": "demo", "owner": "team"}, revision="abc123",
    )  # fmt: skip
    run = client.runs[result.run_id]
    assert run.info.status == "FINISHED" and run.data.params["param_depth"] == "2"
    assert run.data.metrics == {
        "evaluation_accuracy": 0.6, "train_accuracy": 0.6,
    }  # fmt: skip
    assert run.data.tags["bricksmith_model"] == "churn_v1" and run.data.tags["bricksmith_code_revision"] == "abc123"
    assert json.loads(run.data.tags["bricksmith_features"]) == [
        {"name": "tenure_months", "type": "int"}, {"name": "spend", "type": "double"},
    ]  # fmt: skip
    meta = ModelMetadata.from_json((client.root / result.run_id / "model" / FILE).read_text(encoding="utf-8"))
    assert meta.datasets["training"]["inputs"] == {"c.s.t": 3} and meta.dependencies["python"] == "3.12.1"
    assert (
        meta.spec_config == CHURN.config_hash() and meta.features == contract_of(CHURN) and meta.card["owner"] == "team"
    )
    # Train split mean spend is 133.8: rows with spend >= cut are positive; labels 0,1,1,0,1 -> 3 of 5 correct.


def test_unknown_card_field_and_missing_train_rows_are_rejected(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    with pytest.raises(ModelError, match="unknown model card fields"):
        train(store, ThresholdAdapter(), card={"secret": "x"})
    rows = [r for r in training_rows() if r["split"] != "train"]
    with pytest.raises(ModelError, match="no 'train' rows"):
        train_model(store, ThresholdAdapter(), CHURN, rows, experiment="/e")


def test_feature_contract_mismatch_is_rejected_before_fitting(tmp_path: Path) -> None:
    store, client = make_store(tmp_path)
    adapter = ThresholdAdapter()
    rows = [{k: v for k, v in r.items() if k != "spend"} for r in training_rows()]
    with pytest.raises(FeatureContractError, match="missing spend"):
        train_model(store, adapter, CHURN, rows, experiment="/e")
    assert adapter.fits == 0 and not client.runs


def test_register_names_and_resolves_explicit_versions_and_aliases(tmp_path: Path) -> None:
    store, client = make_store(tmp_path)
    adapter = ThresholdAdapter()
    ctx = SimpleNamespace(
        physical_catalog=lambda c: {"demo": "main"}.get(c, c), physical_schema=lambda s: {"ml": "sandbox"}.get(s, s)
    )
    assert registered_name(ctx, "demo.ml", "churn_v1") == NAME  # type: ignore[arg-type]
    v1 = register_run(store, NAME, "churn_v1", train(store, adapter))
    v2 = register_run(store, NAME, "churn_v1", train(store, adapter))
    assert (v1, v2) == (1, 2) and client.models[NAME].tags["bricksmith_model"] == "churn_v1"
    store.set_alias(NAME, "champion", 1)
    assert store.resolve(NAME, "churn_v1", alias="champion").version == 1
    assert store.resolve(NAME, "churn_v1", version=2).version == 2
    store.set_alias(NAME, "champion", 2)  # the alias moves; a pinned version does not
    assert store.resolve(NAME, "churn_v1", alias="champion").version == 2
    assert store.resolve(NAME, "churn_v1", version=1).aliases == ()
    assert store.aliases(NAME) == {"champion": 2}


@pytest.mark.parametrize(
    ("alias", "version"), [(None, None), ("champion", 1), ("latest", None), ("v3", None), (None, 0), (None, "latest")]
)
def test_selectors_are_explicit_and_never_latest(alias: str | None, version: int | str | None) -> None:
    with pytest.raises(ModelAmbiguousError):
        check_selector(alias, version)


def test_missing_ambiguous_or_incompatible_model_never_trains(tmp_path: Path) -> None:
    store, client = make_store(tmp_path)
    adapter = ThresholdAdapter()
    rows = [r for r in training_rows() if r["split"] == "evaluation"]
    kwargs: dict[str, Any] = {"name": NAME}
    with pytest.raises(ModelNotFoundError, match="does not exist"):
        predict_rows(store, adapter, CHURN, rows, version=1, **kwargs)
    register_run(store, NAME, "churn_v1", train(store, adapter))
    fits, runs = adapter.fits, len(client.runs)
    with pytest.raises(ModelNotFoundError, match="alias 'champion' is not set"):
        predict_rows(store, adapter, CHURN, rows, alias="champion", **kwargs)
    with pytest.raises(ModelNotFoundError, match="version 9"):
        predict_rows(store, adapter, CHURN, rows, version=9, **kwargs)
    with pytest.raises(ModelAmbiguousError):
        predict_rows(store, adapter, CHURN, rows, **kwargs)
    with pytest.raises(ModelAmbiguousError):
        predict_rows(store, adapter, CHURN, rows, alias="champion", version=1, **kwargs)
    # incompatible: another identity, a changed contract, an older runtime, a foreign artifact, a missing column
    with pytest.raises(ModelIncompatibleError, match="belongs to churn_v1, not 'upsell_v1'"):
        predict_rows(store, adapter, UPSELL, rows, version=1, **kwargs)
    with pytest.raises(FeatureContractError, match="feature contract differs"):
        predict_rows(store, adapter, replace(CHURN, features=CHURN.features[:1]), rows, version=1, **kwargs)
    with pytest.raises(ModelIncompatibleError, match="not compatible with this runtime"):
        predict_rows(store, ThresholdAdapter({"python": "3.11.9"}), CHURN, rows, version=1, **kwargs)
    with pytest.raises(ModelIncompatibleError, match="needs adapter"):
        predict_rows(store, get_adapter("sklearn"), CHURN, rows, version=1, **kwargs)
    with pytest.raises(FeatureContractError, match="missing spend"):
        predict_rows(store, adapter, CHURN, [{"tenure_months": 1}], version=1, **kwargs)
    (client.root / "run1" / "model" / FILE).unlink()
    with pytest.raises(ModelIncompatibleError, match="not logged by bricksmith"):
        predict_rows(store, adapter, CHURN, rows, version=1, **kwargs)
    assert adapter.fits == fits and len(client.runs) == runs  # nothing was fitted or logged as a fallback


def test_prediction_and_evaluation_use_the_pinned_version(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    adapter = ThresholdAdapter()
    register_run(store, NAME, "churn_v1", train(store, adapter))
    rows = [r for r in training_rows() if r["split"] == "evaluation"]
    out = predict_rows(store, adapter, CHURN, rows, name=NAME, version=1)
    assert [o["prediction"] for o in out] == [0, 0, 1, 0, 0]  # cut 133.8: only spend 300 is positive
    assert out[0] == {
        "customer_id": 1, "snapshot_date": "2026-03-31", "score": 0.0, "prediction": 0, "model": "churn_v1", "model_version": 1,
    }  # fmt: skip
    resolved, metrics = evaluate_rows(store, adapter, CHURN, rows, name=NAME, version=1)
    assert resolved.version == 1 and metrics == {"accuracy": 0.6}


def test_two_models_in_one_schema_never_share_an_artifact(tmp_path: Path) -> None:
    store, client = make_store(tmp_path)
    adapter = ThresholdAdapter()
    other = "main.sandbox.upsell_v1"
    up_rows = [{**r, "plan": "basic", "upsold": r["churned"]} for r in training_rows()]
    run_a = train(store, adapter)
    run_b = train_model(store, adapter, UPSELL, up_rows, experiment="/exp/upsell").run_id
    assert run_a != run_b and (client.root / run_a / "model").is_dir() and (client.root / run_b / "model").is_dir()
    register_run(store, NAME, "churn_v1", run_a)
    register_run(store, other, "upsell_v1", run_b)
    metas = {
        m: json.loads((client.root / r / "model" / FILE).read_text())["model"] for m, r in (("a", run_a), ("b", run_b))
    }
    assert metas == {"a": "churn_v1", "b": "upsell_v1"}
    with pytest.raises(ModelIncompatibleError, match="belongs to upsell_v1"):
        register_run(store, NAME, "churn_v1", run_b)  # a run of one model cannot enter another model's name
    with pytest.raises(ModelIncompatibleError, match="belongs to churn_v1"):
        register_run(store, NAME, "upsell_v1", run_a)
    with pytest.raises(ModelIncompatibleError, match="belongs to churn_v1"):
        store.ensure_model(NAME, "upsell_v1")  # a registered name is bound to one identity
    store.set_alias(NAME, "champion", 1)
    with pytest.raises(ModelIncompatibleError):
        store.resolve(other, "churn_v1", version=1)


def test_promotion_rollback_record_evidence_and_history(tmp_path: Path) -> None:
    store, client = make_store(tmp_path)
    adapter = ThresholdAdapter()
    graph = load_graph(SHARED)
    for _ in range(2):
        register_run(store, NAME, "churn_v1", train(store, adapter))
    with pytest.raises(ModelError, match="reviewer and a reason"):
        plan_promotion(store, NAME, "churn_v1", "champion", 1, reviewer=" ", reason="x")
    first = plan_promotion(store, NAME, "churn_v1", "champion", 1, reviewer="rev", reason="best metrics", graph=graph)
    assert first.from_version is None and first.evidence["metrics"]["evaluation_accuracy"] == 0.6
    assert first.impact.alias_bound == ("demo.ml.churn_evaluate", "demo.ml.churn_predict")
    apply_alias_plan(store, first)
    second = plan_promotion(store, NAME, "churn_v1", "champion", 2, reviewer="rev", reason="retrained", graph=graph)
    stale = plan_promotion(store, NAME, "churn_v1", "champion", 2, reviewer="rev", reason="retrained")
    apply_alias_plan(store, second)
    with pytest.raises(StalePlanError):
        apply_alias_plan(store, stale)
    assert store.aliases(NAME) == {"champion": 2}
    back = plan_rollback(store, NAME, "churn_v1", "champion", reviewer="rev", reason="regression")
    assert (back.from_version, back.to_version) == (2, 1) and "v2 -> v1" in back.to_text()
    apply_alias_plan(store, back)
    assert store.aliases(NAME) == {"champion": 1}
    events = store.events(NAME, 1)
    assert [e["action"] for e in events] == ["promote", "rollback"] and events[1]["reason"] == "regression"
    assert [(e["from"], e["to"]) for e in store.history(NAME, "champion")] == [(None, 1), (1, 2), (2, 1)]
    again = plan_rollback(store, NAME, "churn_v1", "champion", reviewer="r", reason="again")
    assert again.to_version == 2  # a rollback is itself a recorded change; the previous target is v2
    apply_alias_plan(store, plan_promotion(store, NAME, "churn_v1", "challenger", 2, reviewer="r", reason="new alias"))
    with pytest.raises(ModelNotFoundError, match="no earlier version"):
        plan_rollback(store, NAME, "churn_v1", "challenger", reviewer="r", reason="x")  # first assignment
    client.versions[NAME, 2].tags["bricksmith_state"] = "retired"
    with pytest.raises(ModelNotFoundError, match="retired"):
        plan_promotion(store, NAME, "churn_v1", "champion", 2, reviewer="r", reason="x")


def test_retirement_detects_consumers_from_graph_and_aliases_and_protects_training_artifacts(tmp_path: Path) -> None:
    store, client = make_store(tmp_path)
    adapter = ThresholdAdapter()
    graph = load_graph(SHARED)
    for _ in range(2):
        register_run(store, NAME, "churn_v1", train(store, adapter))
    apply_alias_plan(store, plan_promotion(store, NAME, "churn_v1", "champion", 1, reviewer="r", reason="first"))
    plan = plan_retirement(store, NAME, "churn_v1", graph=graph, versions=[1])
    assert plan.blocked and [c.task_id for c in plan.consumers] == ["demo.ml.churn_evaluate", "demo.ml.churn_predict"]
    assert plan.remove_aliases == {"champion": 1} and plan.retained["training_runs"] == ["run1"]
    assert plan.retained["training_artifacts"] == ["runs:/run1/model"]
    with pytest.raises(ModelInUseError, match="active consumers"):
        apply_retirement(store, plan)
    assert store.aliases(NAME) == {"champion": 1} and client.versions[NAME, 1].tags["bricksmith_state"] == "active"
    # v2 has no consumer (the alias points elsewhere); moving the alias away unblocks v1
    assert not plan_retirement(store, NAME, "churn_v1", graph=graph, versions=[2]).blocked
    apply_alias_plan(store, plan_promotion(store, NAME, "churn_v1", "champion", 2, reviewer="r", reason="swap"))
    free = plan_retirement(store, NAME, "churn_v1", graph=graph, versions=[1])
    assert not free.blocked and free.remove_aliases == {}
    apply_retirement(store, free)
    assert client.versions[NAME, 1].tags["bricksmith_state"] == "retired" and (client.root / "run1" / "model").is_dir()
    with pytest.raises(ModelNotFoundError, match="retired"):
        store.resolve(NAME, "churn_v1", version=1)
    assert plan_retirement(store, NAME, "churn_v1", graph=graph).blocked  # the whole model: champion -> v2 is consumed
    with pytest.raises(ModelNotFoundError, match="not active versions"):
        plan_retirement(store, NAME, "churn_v1", versions=[1])


def test_pinned_version_consumers_block_retirement(tmp_path: Path) -> None:
    root = tmp_path / "project"
    shutil.copytree(SHARED, root, ignore=shutil.ignore_patterns("__pycache__"))
    path = root / "catalogs/demo/ml/churn_model.py"
    path.write_text(path.read_text().replace('"alias": "champion"', '"version": 1'), encoding="utf-8")
    graph = load_graph(root)
    assert {(u.task_id.split(".")[-1], u.version) for u in model_uses(graph)} == {
        ("churn_evaluate", 1),
        ("churn_predict", 1),
    }
    store, _ = make_store(tmp_path)
    register_run(store, NAME, "churn_v1", train(store, ThresholdAdapter()))
    assert len(plan_retirement(store, NAME, "churn_v1", graph=graph).consumers) == 2
    assert (
        alias_impact(graph, "churn_v1", "champion").pinned
        and not alias_impact(graph, "churn_v1", "champion").alias_bound
    )


def test_change_propagation_lists_downstream_tasks_from_the_graph() -> None:
    graph = load_graph(SHARED)
    bound = alias_impact(graph, "churn_v1", "champion")
    assert bound.downstream == () and bound.alias_bound == ("demo.ml.churn_evaluate", "demo.ml.churn_predict")
    assert alias_impact(graph, "upsell_v1", "champion").alias_bound == ()
    assert downstream(graph, ["demo.ml.churn_prediction"]) == ["demo.ml.churn_predict"]
    assert "demo.ml.churn_train" in downstream(graph, ["demo.ml.churn_training"])


def test_relog_preserves_identity_and_predictions_and_never_moves_aliases(tmp_path: Path) -> None:
    store, client = make_store(tmp_path)
    old = ThresholdAdapter({"python": "3.11.9", "threshold": "1.0"})
    register_run(store, NAME, "churn_v1", train(store, old))
    store.set_alias(NAME, "champion", 1)
    sample = [r for r in training_rows() if r["split"] == "evaluation"]
    current = ThresholdAdapter()
    resolved = store.resolve(NAME, "churn_v1", version=1)
    with pytest.raises(ModelIncompatibleError, match="not compatible"):
        predict_rows(store, current, CHURN, sample, name=NAME, version=1)  # the old artifact does not fit this runtime
    result = relog_model(store, replace_runtime(old), resolved, sample, experiment="/exp/churn")
    assert (result.from_version, result.to_version, result.max_abs_diff) == (1, 2, 0.0)
    assert store.aliases(NAME) == {"champion": 1} and client.versions[NAME, 2].tags["bricksmith_migrated_from"] == "1"
    before = predict_rows(store, old, CHURN, sample, name=NAME, version=1)
    after = predict_rows(store, current, CHURN, sample, name=NAME, version=2)
    assert [(a["score"], a["prediction"]) for a in after] == [(b["score"], b["prediction"]) for b in before]
    meta = ModelMetadata.from_json((client.root / result.run_id / "model" / FILE).read_text())
    assert meta.model == "churn_v1" and meta.migrated_from == 1 and meta.dependencies["python"] == "3.12.1"


def replace_runtime(old: ThresholdAdapter) -> ThresholdAdapter:
    """An adapter whose loader works on the old artifact but whose runtime is the current one."""
    return ThresholdAdapter()


def test_relog_with_different_predictions_is_retired(tmp_path: Path) -> None:
    store, client = make_store(tmp_path)
    adapter = ThresholdAdapter()
    register_run(store, NAME, "churn_v1", train(store, adapter))
    resolved = store.resolve(NAME, "churn_v1", version=1)
    sample = [r for r in training_rows() if r["split"] == "evaluation"]

    class Drifting(ThresholdAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.loads = 0

        def load(self, path: Path) -> object:
            self.loads += 1
            model = super().load(path)
            if self.loads > 1:
                model["cut"] += 100.0  # type: ignore[index]
            return model

    with pytest.raises(ModelIncompatibleError, match="differs"):
        relog_model(store, Drifting(), resolved, sample, experiment="/e")
    assert client.versions[NAME, 2].tags["bricksmith_state"] == "retired"


def test_runtime_compatibility_compares_strict_dependencies() -> None:
    recorded = {"python": "3.12.1", "scikit-learn": "1.9.1", "mlflow": "3.1"}
    assert check_runtime(recorded, {"python": "3.12.9", "scikit-learn": "1.9.4", "mlflow": "9.0"}) == []
    problems = check_runtime(recorded, {"python": "3.13.0"})
    assert len(problems) == 2 and "scikit-learn 1.9.1 was recorded but is not installed" in problems[1]


def test_model_card_renders_from_metadata(tmp_path: Path) -> None:
    store, client = make_store(tmp_path)
    run = train_model(
        store, ThresholdAdapter(), CHURN, training_rows(), experiment="/e", revision="r1",
        datasets={"training": {"fingerprint": "f" * 64, "inputs": {"c.s.t": 4}, "rows": 5}},
        card={"summary": "Demo model.", "limitations": "Tiny data."},
    )  # fmt: skip
    meta = ModelMetadata.from_json((client.root / run.run_id / "model" / FILE).read_text())
    card = render_card(
        meta,
        name=NAME,
        version=3,
        aliases=("champion",),
        state="active",
        history=({"action": "promote", "alias": "champion", "to": 3, "reviewer": "rev", "reason": "ok"},),
    )
    for expected in (
        "# Model card: churn_v1 v3", "- registered name: main.sandbox.churn_v1", "- aliases: champion", "## Summary", "Demo model.",
        "## Limitations", "- `spend`: double", "training: fingerprint `ffffffffffff`, rows 5, inputs c.s.t@4",
        "- evaluation: accuracy=0.6000", "- code revision: r1", "- promote `champion` v3 by rev: ok",
    ):  # fmt: skip
        assert expected in card
    assert "## Intended use" not in card  # fields that were not given are not invented


def test_model_task_declarations_require_an_explicit_selector() -> None:
    spec = "models/churn_v1.toml"
    base = {"model": "churn_v1", "spec": spec}
    assert parse_model_task({"model_task": {**base, "role": "train"}}).role == "train"  # type: ignore[union-attr]
    assert parse_model_task({}) is None
    for bad in (
        {**base, "role": "predict"},
        {**base, "role": "predict", "alias": "latest"},
        {**base, "role": "evaluate", "alias": "a", "version": 1},
        {**base, "role": "train", "alias": "champion"},
        {**base, "role": "serve"},
        {**base, "role": "predict", "version": 1, "extra": 1},
    ):
        with pytest.raises(ModelError):
            parse_model_task({"model_task": bad})


def test_example_declares_train_evaluate_predict_tasks() -> None:
    graph = load_graph(SHARED)
    assert graph.ok
    kinds = {t.name: t.kind for t in graph.tasks if "model_task" in t.metadata}
    assert kinds == {"churn_train": "train", "churn_evaluate": "evaluate", "churn_predict": "predict"}


def test_cli_group_is_registered_and_card_renders_offline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store, client = make_store(tmp_path)
    run = train(store, ThresholdAdapter(), card={"summary": "Offline card."})
    (tmp_path / "meta.json").write_text((client.root / run / "model" / FILE).read_text(), encoding="utf-8")
    assert main(["model", "card", "--file", str(tmp_path / "meta.json")]) == EXIT_OK
    assert "# Model card: churn_v1" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["model", "promote", "--help"])
    help_text = capsys.readouterr().out
    assert "--reviewer" in help_text and "--apply" in help_text


def test_auc_ties_and_missing_class_are_defined() -> None:
    assert _auc([1, 0], [0.5, 0.5]) == 0.5
    assert _auc([1, 1, 0, 0], [0.9, 0.4, 0.5, 0.1]) == 0.75
    assert _auc([1, 1], [0.2, 0.9]) is None


def test_sklearn_adapter_round_trips_a_saved_model(tmp_path: Path) -> None:
    pytest.importorskip("sklearn")
    pytest.importorskip("mlflow")
    adapter = get_adapter("sklearn")
    rows = [r for r in training_rows() if r["split"] == "train"]
    model = adapter.train(CHURN, rows, {"C": 10.0})
    features = contract_of(CHURN)
    adapter.save(model, tmp_path / "m", features, rows)
    loaded = adapter.load(tmp_path / "m")
    assert adapter.predict(model, features, rows) == adapter.predict(loaded, features, rows)
    metrics = adapter.evaluate(model, features, "churned", rows)
    assert metrics["rows"] == 5.0 and metrics["positive_rate"] == 0.6 and metrics["auc"] is not None
    with pytest.raises(ModelError, match="both classes"):
        adapter.train(CHURN, [{**r, "churned": 0} for r in rows], {})
    with pytest.raises(ModelError, match="unsupported sklearn parameters"):
        adapter.train(CHURN, rows, {"n_jobs": 2})
    with pytest.raises(FeatureContractError, match="null features"):
        adapter.predict(model, features, [{**rows[0], "spend": None}])
    meta = ModelMetadata(
        "churn_v1", "sklearn", features, "churned", ("customer_id",), "snapshot_date", CHURN.config_hash()
    )
    (tmp_path / "meta.json").write_text(meta.to_json(), encoding="utf-8")
    assert main(["--json", "model", "card", "--file", str(tmp_path / "meta.json")]) == EXIT_OK
