"""Train, register, load, evaluate, predict and re-log models through an adapter and a :class:`ModelStore`.

Prediction and evaluation only resolve an explicit version or alias, check identity, feature contract and
runtime compatibility, and then call the adapter; no code path here fits a model except :func:`train_model`.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.adapter import ARTIFACT_DIR, contract_of
from bricksmith.mlops.errors import FeatureContractError, ModelError, ModelIncompatibleError
from bricksmith.mlops.meta import FILE, ModelMetadata, check_card, check_runtime, code_revision
from bricksmith.mlops.store import TAG_MODEL, ModelStore, ResolvedModel
from bricksmith.mlops.validate import validate_dataset

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bricksmith.mlops.adapter import Contract, ModelAdapter
    from bricksmith.mlops.spec import ModelSpec

TOLERANCE = 1e-9
PREDICTION_COLUMNS = ("score", "prediction", "model", "model_version")


@dataclass(frozen=True)
class TrainResult:
    """A finished training run: the run id, the metadata logged with the artifact and the metrics."""

    run_id: str
    metadata: ModelMetadata
    experiment: str

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return {"experiment": self.experiment, "metadata": self.metadata.to_dict(), "run_id": self.run_id}


def check_rows_contract(spec: ModelSpec, rows: Sequence[Mapping[str, Any]], *, labelled: bool) -> None:
    """Require every feature (and the label when ``labelled``) in each row, so a mismatch fails before any fit."""
    needed = [f.name for f in spec.features] + ([spec.label.name] if labelled else [])
    for index, row in enumerate(rows[:1000]):
        missing = [n for n in needed if n not in row]
        if missing:
            msg = f"row {index} does not follow the feature contract of {spec.model}: missing {', '.join(missing)}"
            raise FeatureContractError(msg)


def _flatten(metrics: Mapping[str, Mapping[str, float | None]]) -> dict[str, float | None]:
    return {f"{split}_{k}": v for split, values in sorted(metrics.items()) for k, v in sorted(values.items())}


def write_artifact(
    adapter: ModelAdapter, model: object, meta: ModelMetadata, rows: Sequence[Mapping[str, Any]], into: Path
) -> Path:
    """Write the adapter artifact plus ``bricksmith_model.json`` under ``into/model``; return that directory."""
    target = into / ARTIFACT_DIR
    adapter.save(model, target, meta.features, rows)
    (target / FILE).write_text(meta.to_json(), encoding="utf-8")
    return target


def train_model(  # noqa: PLR0913 - one keyword per recorded kind of evidence
    store: ModelStore,
    adapter: ModelAdapter,
    spec: ModelSpec,
    rows: Sequence[Mapping[str, Any]],
    *,
    experiment: str,
    params: Mapping[str, Any] | None = None,
    datasets: Mapping[str, Mapping[str, Any]] | None = None,
    card: Mapping[str, str] | None = None,
    revision: str | None = None,
) -> TrainResult:
    """Validate the training dataset, fit on the ``train`` split, evaluate, and log everything to one run.

    ``datasets`` maps a role to ``{"fingerprint", "config", "inputs", "rows"}`` (BS-11 fingerprints and the
    Delta table versions of the inputs). The model is not registered here.
    """
    check_card(card or {})
    check_rows_contract(spec, rows, labelled=True)
    validate_dataset(spec, rows).require()  # leaks, immature labels and split overlap stop the run before fitting
    train_rows = [r for r in rows if r.get("split") == "train"]
    eval_rows = [r for r in rows if r.get("split") == "evaluation"]
    if not train_rows:
        msg = f"model {spec.model}: the training dataset has no 'train' rows"
        raise ModelError(msg)
    parameters = dict(params or {})
    model = adapter.train(spec, train_rows, parameters)
    features = contract_of(spec)
    metrics: dict[str, dict[str, float | None]] = {
        "train": adapter.evaluate(model, features, spec.label.name, train_rows)
    }
    if eval_rows:
        metrics["evaluation"] = adapter.evaluate(model, features, spec.label.name, eval_rows)
    meta = ModelMetadata(
        model=spec.model,
        adapter=adapter.name,
        features=features,
        label=spec.label.name,
        key=spec.key,
        snapshot=spec.snapshot,
        spec_config=spec.config_hash(),
        datasets=datasets or {},
        parameters=parameters,
        metrics=metrics,
        dependencies=adapter.dependencies(),
        code_revision=revision or code_revision(),
        trained_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        card=card or {},
    )
    with tempfile.TemporaryDirectory(prefix="bricksmith-model-") as tmp:
        artifact = write_artifact(adapter, model, meta, train_rows, Path(tmp))
        tags = {
            TAG_MODEL: spec.model,
            "bricksmith_adapter": adapter.name,
            "bricksmith_code_revision": meta.code_revision,
            "bricksmith_spec_config": meta.spec_config,
            "bricksmith_features": json.dumps(meta.to_dict()["features"], separators=(",", ":")),
            "bricksmith_datasets": json.dumps(meta.to_dict()["datasets"], sort_keys=True, separators=(",", ":"))[:5000],
            "bricksmith_dependencies": json.dumps(dict(meta.dependencies), sort_keys=True, separators=(",", ":")),
        }
        shown = {**{f"param_{k}": v for k, v in parameters.items()}, "adapter": adapter.name, "label": spec.label.name}
        run_id = store.log_run(
            experiment,
            run_name=f"train-{spec.model}",
            tags=tags,
            params=shown,
            metrics=_flatten(metrics),
            artifact_dir=artifact,
            artifact_path=ARTIFACT_DIR,
        )
    return TrainResult(run_id, dataclasses.replace(meta, run_id=run_id), experiment)


def register_run(store: ModelStore, name: str, model: str, run_id: str) -> int:
    """Register the artifact of ``run_id`` as a new version of ``name``; the run must belong to identity ``model``."""
    tags = dict(store.client.get_run(run_id).data.tags or {})
    if tags.get(TAG_MODEL) != model:
        msg = f"run {run_id} belongs to {tags.get(TAG_MODEL) or 'no bricksmith model'}, not {model!r}"
        raise ModelIncompatibleError(msg)
    return store.create_version(name, model, run_id, {"bricksmith_run_id": run_id})


def load_model(
    store: ModelStore,
    adapter: ModelAdapter,
    resolved: ResolvedModel,
    workdir: Path,
    *,
    contract: Contract | None = None,
) -> tuple[object, ModelMetadata]:
    """Download and load a pinned version after checking adapter, feature contract and runtime compatibility."""
    path, meta = store.fetch(resolved, workdir)
    if meta.adapter != adapter.name:
        msg = f"{resolved.name} v{resolved.version} needs adapter {meta.adapter!r}, not {adapter.name!r}"
        raise ModelIncompatibleError(msg)
    if contract is not None and tuple(contract) != meta.features:
        msg = f"the feature contract differs from {resolved.name} v{resolved.version}: expected {list(meta.features)}"
        raise FeatureContractError(msg)
    problems = check_runtime(meta.dependencies, adapter.dependencies())
    if problems:
        msg = f"{resolved.name} v{resolved.version} is not compatible with this runtime: {'; '.join(problems)}"
        raise ModelIncompatibleError(msg)
    return adapter.load(path), meta


def prediction_rows(
    spec_key: Sequence[str],
    snapshot: str,
    rows: Sequence[Mapping[str, Any]],
    outputs: Sequence[Mapping[str, Any]],
    resolved: ResolvedModel,
) -> list[dict[str, Any]]:
    """Join key and snapshot columns with the scores and the exact model identity and version used."""
    return [
        {
            **{c: row[c] for c in (*spec_key, snapshot)},
            **out,
            "model": resolved.model,
            "model_version": resolved.version,
        }
        for row, out in zip(rows, outputs, strict=True)
    ]


def predict_rows(  # noqa: PLR0913 - explicit identity and selector
    store: ModelStore,
    adapter: ModelAdapter,
    spec: ModelSpec,
    rows: Sequence[Mapping[str, Any]],
    *,
    name: str,
    alias: str | None = None,
    version: int | None = None,
) -> list[dict[str, Any]]:
    """Predict with the pinned version of ``spec.model``; a missing, ambiguous or incompatible model never trains."""
    resolved = store.resolve(name, spec.model, alias=alias, version=version)
    check_rows_contract(spec, rows, labelled=False)
    with tempfile.TemporaryDirectory(prefix="bricksmith-load-") as tmp:
        model, _ = load_model(store, adapter, resolved, Path(tmp), contract=contract_of(spec))
        outputs = adapter.predict(model, contract_of(spec), rows)
    return prediction_rows(spec.key, spec.snapshot, rows, outputs, resolved)


def evaluate_rows(  # noqa: PLR0913 - explicit identity and selector
    store: ModelStore,
    adapter: ModelAdapter,
    spec: ModelSpec,
    rows: Sequence[Mapping[str, Any]],
    *,
    name: str,
    alias: str | None = None,
    version: int | None = None,
) -> tuple[ResolvedModel, dict[str, float | None]]:
    """Evaluate the pinned version on labelled ``rows``; returns the resolved version and the metrics."""
    resolved = store.resolve(name, spec.model, alias=alias, version=version)
    check_rows_contract(spec, rows, labelled=True)
    with tempfile.TemporaryDirectory(prefix="bricksmith-load-") as tmp:
        model, _ = load_model(store, adapter, resolved, Path(tmp), contract=contract_of(spec))
        return resolved, adapter.evaluate(model, contract_of(spec), spec.label.name, rows)


def predict_in_subprocess(
    store: ModelStore, resolved: ResolvedModel, rows: Sequence[Mapping[str, Any]], *, timeout: float = 600.0
) -> list[dict[str, Any]]:
    """Load the pinned version in a fresh interpreter, validate it there and predict ``rows`` (key-free rows)."""
    if store.uris is None:
        msg = "the store has no tracking URIs; a fresh process cannot reach the registry"
        raise ModelError(msg)
    request = {
        "adapter": "sklearn",
        "model": resolved.model,
        "name": resolved.name,
        "registry": store.uris[1],
        "rows": list(rows),
        "tracking": store.uris[0],
        "version": resolved.version,
    }
    done = subprocess.run(
        [sys.executable, "-m", "bricksmith.mlops.worker"],
        input=json.dumps(request, default=str),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    try:
        reply = json.loads(done.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        msg = f"fresh process failed without a result (exit {done.returncode}): {done.stderr.strip()[-400:]}"
        raise ModelIncompatibleError(msg) from None
    if "error" in reply:
        msg = f"[{reply['error']['code']}] {reply['error']['message']}"
        raise ModelIncompatibleError(msg)
    return list(reply["predictions"])


@dataclass(frozen=True)
class MigrationResult:
    """The outcome of a deliberate re-log: new version, the source version and the prediction difference."""

    name: str
    model: str
    from_version: int
    to_version: int
    max_abs_diff: float
    run_id: str

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return dataclasses.asdict(self)


def relog_model(  # noqa: PLR0913 - explicit migration inputs
    store: ModelStore,
    adapter: ModelAdapter,
    resolved: ResolvedModel,
    sample: Sequence[Mapping[str, Any]],
    *,
    experiment: str,
    tolerance: float = TOLERANCE,
) -> MigrationResult:
    """Re-log a pinned version under the current runtime as a new version of the same identity.

    Predictions on ``sample`` must match within ``tolerance``; otherwise the new version is retired and the
    call fails. Aliases are never moved: promotion stays a separate, reviewed step.
    """
    from bricksmith.mlops.store import RETIRED, TAG_STATE  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="bricksmith-relog-") as tmp:
        old = Path(tmp) / "old"
        path, meta = store.fetch(resolved, old)
        model = adapter.load(path)
        before = adapter.predict(model, meta.features, sample)
        fresh = dataclasses.replace(
            meta, dependencies=adapter.dependencies(), migrated_from=resolved.version, run_id=""
        )
        artifact = write_artifact(adapter, model, fresh, sample, Path(tmp) / "new")
        run_id = store.log_run(
            experiment,
            run_name=f"relog-{resolved.model}-v{resolved.version}",
            tags={TAG_MODEL: resolved.model, "bricksmith_migrated_from": str(resolved.version)},
            params={"migrated_from": resolved.version},
            metrics={},
            artifact_dir=artifact,
            artifact_path=ARTIFACT_DIR,
        )
    version = store.create_version(
        resolved.name, resolved.model, run_id, {"bricksmith_migrated_from": str(resolved.version)}
    )
    with tempfile.TemporaryDirectory(prefix="bricksmith-relog-") as tmp:
        candidate = ResolvedModel(resolved.name, resolved.model, version, run_id, (), "active")
        reloaded, _ = load_model(store, adapter, candidate, Path(tmp), contract=meta.features)
        after = adapter.predict(reloaded, meta.features, sample)
    diff = max((abs(a["score"] - b["score"]) for a, b in zip(before, after, strict=True)), default=0.0)
    same_class = all(a["prediction"] == b["prediction"] for a, b in zip(before, after, strict=True))
    if diff > tolerance or not same_class:
        store.tag_version(resolved.name, version, TAG_STATE, RETIRED)
        msg = f"re-logged v{version} differs from v{resolved.version} (max score difference {diff:.3g}); it was retired"
        raise ModelIncompatibleError(msg)
    return MigrationResult(resolved.name, resolved.model, resolved.version, version, diff, run_id)
