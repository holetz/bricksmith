"""Training, scoring, monitoring and documentation of the attrition model.

``attrition_train`` fits a logistic regression on the development sample of the training ABT, on the features the
selection funnel kept. It logs everything the technical document needs to one MLflow run, plus a bricksmith model
artifact (``model/`` with ``bricksmith_model.json``), so a workspace that supports it can register the run.

Registration and the alias are deliberate steps, as in every bricksmith project: ``register_run`` and an alias plan
to ``champion`` (``bricksmith model register|promote``). Scoring always pins its model: the alias named by the task
parameter ``model_alias`` (default ``champion``), or the run named by ``model_run`` for a run that is not registered
yet. Nothing resolves "latest" and nothing trains on a missing model.

The scoring task writes the prediction contract and declares ``model_monitoring``; the evaluation task measures the
labelled samples, logs the evaluation to the training run and publishes the technical document beside it.
"""

import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import reduce
from pathlib import Path

from bricksmith.tasks import task

from .development import SELECTION, development, feature_inputs

MODEL = "attrition"
ALIAS = "champion"
LABEL = "attrited"
KEYS = ("account_id", "closing_date")
SAMPLES = ("dev", "oot", "live")
CATEGORICAL = ("segment",)  # selected or not, the logistic regression reads numeric features only
MONITORED = ("balance_mean_3p", "days_past_due_max_3p", "payment_ratio_3p")
MODEL_FILES = ("MLmodel", "bricksmith_model.json")  # plus the serialized model that MLmodel names
EXPERIMENT_PARAMETER = "mlflow_experiment"
RUN_DDL = "run_id STRING, model STRING, experiment STRING, features STRING, selection STRING, trained_at STRING"
NARRATIVE = {
    "purpose": "Rank open accounts by the likelihood that they close within the label horizon.",
    "intended_use": "Demonstration of the bricksmith model flow; not for decisions about real accounts.",
    "population": "Every synthetic account at each monthly closing, minus the declared exclusions.",
    "target": "An account attrites when it closes after the closing and within the label horizon.",
    "limitations": "Trained on a small synthetic history; scores carry no real-world meaning.",
    "inherent_risk": "Low: the scores drive nothing.",
    "materiality": "None.",
}
EVALUATION = {
    "name": MODEL,
    "score": "score",
    "label": LABEL,
    "grade": "grade",
    "reference": "dev",
    "features": list(MONITORED),
}


@dataclass(frozen=True)
class Pin:
    """The model a scoring or evaluation task uses: its run and, when registered, its name, version and alias."""

    run_id: str
    version: str
    name: str = ""
    alias: str | None = None

    @property
    def registered(self) -> bool:
        """Whether the pin is a registered version (an alias) rather than a bare run."""
        return bool(self.name)


def model_features(selected):
    """Return the selected features the model reads, in selection order (numeric only)."""
    return [name for name in selected if name not in CATEGORICAL]


def model_rows(rows, features):
    """Return ``rows`` with a null feature read as 0.0 (an empty window or a missing lookup); training and scoring
    share this rule, so the registered artifact and every scored row see the same inputs."""
    return [{**row, **{name: 0.0 if row.get(name) is None else float(row[name]) for name in features}} for row in rows]


def store_of(ctx):
    """Return the model store of the run's context."""
    from bricksmith.clients import Clients  # noqa: PLC0415 - the tracking client loads only when the task runs
    from bricksmith.mlops.store import ModelStore  # noqa: PLC0415

    return ModelStore.from_clients(Clients(ctx.execution))


def experiment(ctx):
    """Return the tracking experiment path from the context parameter ``mlflow_experiment``."""
    value = ctx.execution.parameters.get(EXPERIMENT_PARAMETER)
    if not value:
        msg = f"set the context parameter {EXPERIMENT_PARAMETER!r} to the tracking experiment path"
        raise ValueError(msg)
    return value


def pin_of(ctx, store):
    """Return the explicit pin: the ``model_run`` parameter, else the ``model_alias`` alias (default champion)."""
    from bricksmith.mlops.tasks import name_from_physical  # noqa: PLC0415

    run = ctx.execution.parameters.get("model_run")
    if run:
        return Pin(run_id=run, version=f"run:{run}")
    alias = ctx.execution.parameters.get("model_alias") or ALIAS
    name = name_from_physical(ctx.entry.targets[0].physical, MODEL)
    resolved = store.resolve(name, MODEL, alias=alias)
    return Pin(run_id=resolved.run_id, version=str(resolved.version), name=name, alias=alias)


def download_model(client, run_id, into):
    """Download the model artifact of a run file by file (no artifact listing) and return its directory."""
    import yaml  # noqa: PLC0415 - installed with MLflow  # pyright: ignore[reportMissingModuleSource]

    target = into / "model"
    target.mkdir(parents=True, exist_ok=True)

    def fetch(file):
        # Where a download lands differs by artifact store, so each file goes to its own directory first.
        with tempfile.TemporaryDirectory(dir=into) as landing:
            Path(client.download_artifacts(run_id, f"model/{file}", landing)).replace(target / file)

    for file in MODEL_FILES:
        fetch(file)
    flavor = yaml.safe_load((target / "MLmodel").read_text(encoding="utf-8"))["flavors"]["sklearn"]
    fetch(flavor["pickled_model"])  # model.skops or model.pkl, by the serialization format MLflow chose
    return target


def load_pinned(store, pin, into):
    """Load the pinned model after checking its identity and runtime; return the model and its metadata."""
    from bricksmith.mlops.adapter import SklearnAdapter  # noqa: PLC0415
    from bricksmith.mlops.errors import ModelIncompatibleError  # noqa: PLC0415
    from bricksmith.mlops.meta import FILE, ModelMetadata, check_runtime  # noqa: PLC0415
    from bricksmith.mlops.store import ResolvedModel  # noqa: PLC0415
    from bricksmith.mlops.training import load_model  # noqa: PLC0415

    adapter = SklearnAdapter()
    if pin.registered:
        resolved = ResolvedModel(pin.name, MODEL, int(pin.version), pin.run_id, (pin.alias or "",), "active")
        return load_model(store, adapter, resolved, into)
    path = download_model(store.client, pin.run_id, into)
    meta = ModelMetadata.from_json((path / FILE).read_text(encoding="utf-8"))
    problems = check_runtime(meta.dependencies, adapter.dependencies())
    if meta.model != MODEL or problems:
        msg = f"run {pin.run_id} holds model {meta.model!r}; runtime: {'; '.join(problems) or 'compatible'}"
        raise ModelIncompatibleError(msg)
    return adapter.load(path), meta


def documentation_files(ctx, selection, model, features):
    """Return the development, narrative, specification and selection artifacts of the training run."""
    from pyspark.sql import DataFrame  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    from bricksmith.mlops.documentation import check_narrative, development_files, model_specification  # noqa: PLC0415
    from bricksmith.mlops.documentation.artifacts import NARRATIVE as NARRATIVE_FILE  # noqa: PLC0415
    from bricksmith.mlops.documentation.artifacts import SELECTION as SELECTION_FILES  # noqa: PLC0415
    from bricksmith.mlops.documentation.artifacts import SPECIFICATION, plain  # noqa: PLC0415
    from bricksmith.mlops.stages.spark import composition, select_sample  # noqa: PLC0415

    declared = development()
    population = ctx.read("attrition_population")
    sampled = reduce(DataFrame.unionByName, [select_sample(population, declared, s) for s in SAMPLES])
    mix = [row.asDict() for row in composition(sampled, declared, by=("sample",)).collect()]
    found = selection.to_dict()
    return plain({
        **development_files(declared, mix),
        NARRATIVE_FILE: check_narrative(NARRATIVE),
        SPECIFICATION: model_specification(model, features),
        **{SELECTION_FILES[k]: found[k] for k in ("ledger", "variables", "woe")},
    })  # fmt: skip


@task(kind="table", outputs="attrition_training_run", reads=["attrition_population"], metadata={"write": "overwrite"})
def attrition_train(ctx):
    """Fit on the development sample, log the document artifacts and the model artifact, record the run id."""
    from sklearn.linear_model import LogisticRegression  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    from bricksmith.mlops.adapter import SklearnAdapter, feature_frame  # noqa: PLC0415
    from bricksmith.mlops.meta import ModelMetadata, code_revision  # noqa: PLC0415
    from bricksmith.mlops.selection import select_features  # noqa: PLC0415
    from bricksmith.mlops.store import TAG_MODEL  # noqa: PLC0415
    from bricksmith.mlops.training import write_artifact  # noqa: PLC0415

    rows = [row.asDict() for row in ctx.read("attrition_training").collect()]
    selection = select_features(rows, SELECTION)  # the row engine equals the Spark engine of the selection tasks
    features = model_features(selection.selected)
    contract = tuple((name, "double") for name in features)
    train = model_rows([r for r in rows if r["sample"] == "dev"], features)
    labels = [int(r[LABEL]) for r in train]
    model = LogisticRegression(max_iter=500, random_state=0).fit(feature_frame(contract, train), labels)
    files = documentation_files(ctx, selection, model, features)
    store, path, now = store_of(ctx), experiment(ctx), datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    client = store.client
    # The run is created directly: ModelStore.log_run verifies the upload by listing artifacts, which the free tier
    # does not return (spec 88).
    run = client.create_run(store.experiment_id(path), run_name=f"train-{MODEL}", tags={TAG_MODEL: MODEL})
    run_id = str(run.info.run_id)
    try:
        for name, data in files.items():
            client.log_dict(run_id, data, name)
        adapter = SklearnAdapter()
        meta = ModelMetadata(
            model=MODEL, adapter=adapter.name, features=contract, label=LABEL, key=KEYS[:1], snapshot=KEYS[1],
            spec_config=selection.config.fingerprint(),
            datasets={"training": {"inputs": json.loads(rows[0]["feature_reads"])}},
            parameters={"max_iter": 500, "random_state": 0}, dependencies=adapter.dependencies(),
            code_revision=code_revision(), run_id=run_id, trained_at=now,
        )  # fmt: skip
        with tempfile.TemporaryDirectory(prefix="bricksmith-model-") as tmp:
            client.log_artifacts(run_id, str(write_artifact(adapter, model, meta, train, Path(tmp))), "model")
    except BaseException:
        client.set_terminated(run_id, "FAILED")
        raise
    client.set_terminated(run_id, "FINISHED")
    record = (run_id, MODEL, path, json.dumps(features), selection.config.fingerprint(), now)
    ctx.write("attrition_training_run", ctx.io.spark.createDataFrame([record], RUN_DDL))


@task(
    kind="table",
    outputs="attrition_scores",
    reads=["attrition_population", "attrition_labels", "account_features", "customer_features"],
    metadata={
        "write": "overwrite",
        "model_monitoring": {
            "model": "attrition",
            "period": "closing_date",
            "score": "score",
            "label": "attrited",
            "grade": "grade",
            "sample": "sample",
            "references": {"development": {"sample": "dev"}, "out_of_time": {"sample": "oot"}},
            "features": ["balance_mean_3p", "days_past_due_max_3p", "payment_ratio_3p"],
            "critical_fields": ["balance_mean_3p"],
            "model_version_column": "model_version",
            "label_lag": 3,
        },
    },
)
def attrition_scores(ctx):
    """Score every row of the development, out-of-time and prediction samples with the pinned model."""
    from pyspark.sql import DataFrame  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    from bricksmith.mlops.adapter import SklearnAdapter  # noqa: PLC0415
    from bricksmith.mlops.stages.spark import build_abt, prediction_frame  # noqa: PLC0415

    bound, definitions = feature_inputs(ctx)
    population, spark = ctx.read("attrition_population"), ctx.io.spark
    parts = [
        build_abt(population, bound, s, purpose="scoring", definitions=definitions, spark=spark).data for s in SAMPLES
    ]
    abt = reduce(DataFrame.unionByName, parts)
    store = store_of(ctx)
    pin = pin_of(ctx, store)
    with tempfile.TemporaryDirectory(prefix="bricksmith-load-") as tmp:
        model, meta = load_pinned(store, pin, Path(tmp))
        features = [name for name, _ in meta.features]
        rows = model_rows([row.asDict() for row in abt.select(*KEYS, *features).collect()], features)
        scores = SklearnAdapter().predict(model, meta.features, rows)
    scored = spark.createDataFrame(
        [(r["account_id"], r["closing_date"], s["score"]) for r, s in zip(rows, scores, strict=True)],
        "account_id BIGINT, closing_date DATE, score DOUBLE",
    )
    out = prediction_frame(abt.join(scored, list(KEYS)), bound, model_version=pin.version, model_alias=pin.alias)
    labels = ctx.read("attrition_labels").select(*KEYS, LABEL)
    out = out.join(labels, list(KEYS), "left").join(abt.select(*KEYS, *MONITORED), list(KEYS))
    ctx.write("attrition_scores", out)


@task(
    kind="table",
    outputs=["attrition_evaluation_results", "attrition_evaluation_distribution", "attrition_evaluation_grades"],
    reads=["attrition_scores"],
    metadata={"write": "overwrite"},
)
def attrition_evaluation(ctx):
    """Evaluate the labelled scored rows, log the evaluation to the training run and publish the document."""
    from bricksmith.mlops.documentation import collect_run, publish_document  # noqa: PLC0415
    from bricksmith.mlops.documentation.artifacts import EVALUATION as EVALUATION_FILES  # noqa: PLC0415
    from bricksmith.mlops.documentation.artifacts import plain  # noqa: PLC0415
    from bricksmith.mlops.evaluation import evaluate, table_rows, to_frame  # noqa: PLC0415

    labelled = ctx.read("attrition_scores").where(f"{LABEL} IS NOT NULL AND NOT excluded")
    result = evaluate(labelled, EVALUATION, development())
    store = store_of(ctx)
    pin = pin_of(ctx, store)
    found = result.to_dict()
    files = {EVALUATION_FILES[k]: found[k] for k in ("results", "distribution", "grades")}
    summary = ("config", "fingerprint", "name", "reference", "samples")
    files[EVALUATION_FILES["evaluation"]] = {k: found[k] for k in summary}
    for name, data in plain(files).items():
        store.client.log_dict(pin.run_id, data, name)
    version = int(pin.version) if pin.registered else None  # the version tag needs a registered version
    publish_document(store.client, collect_run(store.client, pin.run_id), store=store,
                     model=pin.name if pin.registered else None, version=version)  # fmt: skip
    now = datetime.now(UTC).isoformat()
    for kind in ("results", "distribution", "grades"):
        rows = table_rows(result, kind, run_id=pin.run_id, now=now)
        ctx.write(f"attrition_evaluation_{kind}", to_frame(ctx.io.spark, kind, rows))
