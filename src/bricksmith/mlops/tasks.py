"""``model_task`` declarations and the helpers that run train, evaluate and predict inside ordinary tasks.

A task declares ``metadata={"model_task": {"model", "role", "spec", ...}}`` next to its kind (``train``,
``evaluate``, ``predict``); the graph treats it like any other task. Evaluate and predict tasks must pin an
explicit ``version`` or ``alias``. Tracking clients come from ``Clients(ctx.execution)`` unless injected.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.adapter import ModelAdapter, get_adapter
from bricksmith.mlops.errors import ModelError
from bricksmith.mlops.spec import ModelSpec, find_project_root, load_spec_file, parse_spec
from bricksmith.mlops.store import ModelStore, check_selector, registered_name
from bricksmith.mlops.training import TrainResult, evaluate_rows, predict_rows, train_model
from bricksmith.mlops.validate import rows_from

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from bricksmith.clients import Clients

MODEL_TASK_KEY = "model_task"
MODEL_ROLES = ("train", "evaluate", "predict")
EXPERIMENT_PARAMETER = "mlflow_experiment"
_FIELDS = ("model", "role", "spec", "adapter", "alias", "version", "params", "card", "scope")


@dataclass(frozen=True)
class ModelTask:
    """The static ``model_task`` declaration of a task."""

    model: str
    role: str
    spec: str | Mapping[str, Any]
    adapter: str = "sklearn"
    alias: str | None = None
    version: int | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    card: Mapping[str, str] = field(default_factory=dict)
    scope: str | None = None

    def resolve(self, root: Path | str) -> ModelSpec:
        """Load the spec and require the same explicit model id."""
        spec = parse_spec(self.spec) if isinstance(self.spec, dict) else load_spec_file(Path(root) / str(self.spec))
        if spec.model != self.model:
            msg = f"task declares model {self.model!r} but its spec declares {spec.model!r}"
            raise ModelError(msg)
        return spec


def parse_model_task(metadata: Mapping[str, object]) -> ModelTask | None:
    """Return the declaration in task metadata (validated), or None when the task has none."""
    if MODEL_TASK_KEY not in metadata:
        return None
    data = metadata[MODEL_TASK_KEY]
    if not isinstance(data, dict):
        msg = f"{MODEL_TASK_KEY} must be a mapping"
        raise ModelError(msg)
    unknown = set(data) - set(_FIELDS)
    if unknown:
        msg = f"{MODEL_TASK_KEY} has unknown keys: {', '.join(sorted(unknown))}"
        raise ModelError(msg)
    role = data.get("role")
    if role not in MODEL_ROLES:
        msg = f"{MODEL_TASK_KEY}.role must be one of {', '.join(MODEL_ROLES)}, got {role!r}"
        raise ModelError(msg)
    model, spec = data.get("model"), data.get("spec")
    if not isinstance(model, str) or not model.isidentifier():
        msg = f"{MODEL_TASK_KEY}.model must be an explicit identifier, got {model!r}"
        raise ModelError(msg)
    if not isinstance(spec, (str, dict)):
        msg = f"{MODEL_TASK_KEY}.spec must be a path relative to the project root or an inline mapping"
        raise ModelError(msg)
    alias, version = data.get("alias"), data.get("version")
    if role == "train":
        if alias is not None or version is not None:
            msg = "a train task creates versions; it cannot pin an alias or version"
            raise ModelError(msg)
    else:
        alias, version = check_selector(alias, version)  # an explicit selector is mandatory; never latest
    return ModelTask(
        model,
        str(role),
        spec,
        str(data.get("adapter", "sklearn")),
        alias,
        version,
        data.get("params") or {},
        data.get("card") or {},
        data.get("scope"),
    )


def model_task(function: Callable[..., object]) -> tuple[ModelTask, ModelSpec]:
    """Return the declaration and resolved spec of the ``@task`` function being run."""
    from bricksmith.tasks import declaration_of  # noqa: PLC0415

    declared = declaration_of(function)
    parsed = parse_model_task(declared.metadata) if declared else None
    if parsed is None:
        msg = f"{getattr(function, '__name__', function)!r} has no {MODEL_TASK_KEY} metadata"
        raise ModelError(msg)
    source = inspect.getsourcefile(function)
    return parsed, parsed.resolve(find_project_root(source) if source else Path.cwd())


def _rows(frame: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    return rows_from(frame.collect()) if hasattr(frame, "collect") else rows_from(frame)


def _store(ctx: Any, clients: Clients | None) -> ModelStore:  # noqa: ANN401
    from bricksmith.clients import Clients as RealClients  # noqa: PLC0415

    return ModelStore.from_clients(clients or RealClients(ctx.execution))


def name_from_physical(physical: str, model: str) -> str:
    """Return ``<catalog>.<schema>.<model>`` from a physical table name of the model's scope."""
    parts = physical.split(".")
    if len(parts) < 3:  # noqa: PLR2004
        msg = f"cannot derive the registry scope from {physical!r}"
        raise ModelError(msg)
    return ".".join([*parts[:2], model])


def _name(ctx: Any, declared: ModelTask) -> str:  # noqa: ANN401
    """Return the registered name: the declared ``scope`` through the context mapping, else the task's output scope."""
    if declared.scope:
        return registered_name(ctx.execution, declared.scope, declared.model)
    return name_from_physical(ctx.entry.targets[0].physical, declared.model)


def _experiment(ctx: Any) -> str:  # noqa: ANN401
    value = ctx.execution.parameters.get(EXPERIMENT_PARAMETER)
    if not isinstance(value, str) or not value:
        msg = f"set the context parameter {EXPERIMENT_PARAMETER!r} to the tracking experiment path"
        raise ModelError(msg)
    return value


def _output(ctx: Any, rows: list[dict[str, Any]], ddl: str) -> Any:  # noqa: ANN401
    return ctx.io.spark.createDataFrame([tuple(r[c.split()[0]] for c in ddl.split(", ")) for r in rows], ddl)


def train_task(  # noqa: PLR0913 - the task passes its reads and output explicitly
    ctx: Any,  # noqa: ANN401
    function: Callable[..., object],
    training: Any,  # noqa: ANN401
    provenance: Any,  # noqa: ANN401
    *,
    output: str,
    clients: Clients | None = None,
    adapter: ModelAdapter | None = None,
) -> TrainResult:
    """Train the declared model on ``training`` rows, log the run with the dataset provenance and write its record.

    The run is not registered: registration and promotion are deliberate steps (``bricksmith model ...``).
    """
    declared, spec = model_task(function)
    stamps = {
        str(p["dataset"]): {
            "config": p["config"],
            "fingerprint": p["fingerprint"],
            "inputs": json.loads(p["inputs"]),
            "rows": json.loads(p["counts"]).get("rows"),
        }
        for p in _rows(provenance)
        if p["model"] == spec.model
    }
    result = train_model(
        _store(ctx, clients),
        adapter or get_adapter(declared.adapter),
        spec,
        _rows(training),
        experiment=_experiment(ctx),
        params=declared.params,
        datasets=stamps,
        card=declared.card,
    )
    meta = result.metadata
    row = {
        "model": spec.model,
        "run_id": result.run_id,
        "experiment": result.experiment,
        "registered_name": _name(ctx, declared),
        "training_fingerprint": stamps.get("training", {}).get("fingerprint", ""),
        "metrics": json.dumps(meta.to_dict()["metrics"], sort_keys=True),
        "code_revision": meta.code_revision,
        "trained_at": meta.trained_at,
    }
    ddl = ", ".join(f"{c} STRING" for c in row)
    ctx.write(output, _output(ctx, [row], ddl))
    return result


def evaluate_task(  # noqa: PLR0913 - the task passes its reads and output explicitly
    ctx: Any,  # noqa: ANN401
    function: Callable[..., object],
    training: Any,  # noqa: ANN401
    *,
    output: str,
    clients: Clients | None = None,
    adapter: ModelAdapter | None = None,
    split: str = "evaluation",
) -> dict[str, float | None]:
    """Evaluate the pinned model on the ``split`` rows of ``training`` and write one row per metric."""
    declared, spec = model_task(function)
    rows = [r for r in _rows(training) if r.get("split") == split]
    resolved, metrics = evaluate_rows(
        _store(ctx, clients), adapter or get_adapter(declared.adapter), spec, rows,
        name=_name(ctx, declared), alias=declared.alias, version=declared.version,
    )  # fmt: skip
    table = [
        {"model": spec.model, "model_version": resolved.version, "split": split, "metric": k, "value": v}
        for k, v in sorted(metrics.items())
    ]
    ctx.write(output, _output(ctx, table, "model STRING, model_version INT, split STRING, metric STRING, value DOUBLE"))
    return metrics


def predict_task(  # noqa: PLR0913 - the task passes its reads and output explicitly
    ctx: Any,  # noqa: ANN401
    function: Callable[..., object],
    prediction: Any,  # noqa: ANN401
    *,
    output: str,
    clients: Clients | None = None,
    adapter: ModelAdapter | None = None,
) -> int:
    """Predict ``prediction`` rows with the pinned model.

    Output columns: key, snapshot, score, prediction, model, model_version.

    A missing, ambiguous or incompatible model fails the task; nothing is fitted as a fallback.
    """
    from pyspark.sql import types as t  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    declared, spec = model_task(function)
    rows = _rows(prediction)
    result = predict_rows(
        _store(ctx, clients), adapter or get_adapter(declared.adapter), spec, rows,
        name=_name(ctx, declared), alias=declared.alias, version=declared.version,
    )  # fmt: skip
    base = prediction.select(*spec.key, spec.snapshot).schema.fields
    schema = t.StructType(
        [
            *base,
            t.StructField("score", t.DoubleType()),
            t.StructField("prediction", t.IntegerType()),
            t.StructField("model", t.StringType()),
            t.StructField("model_version", t.IntegerType()),
        ]
    )
    names = [f.name for f in schema.fields]
    originals = {tuple(r[c] for c in (*spec.key, spec.snapshot)): r for r in rows}
    ctx.write(output, ctx.io.spark.createDataFrame([tuple(o[n] for n in names) for o in result if originals], schema))
    return len(result)
