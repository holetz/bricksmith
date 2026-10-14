# Models: training, registry and lifecycle

Train, evaluate and predict tasks are ordinary `@task` functions next to the dataset tasks of
[model datasets](model-datasets.md); they share discovery, the graph and the runner. Models are logged with
MLflow and registered on the Unity Catalog registry as `<catalog>.<schema>.<model>`. Install the `mlops` extra
(MLflow and scikit-learn).

## Declare the tasks

```python
@task(
    kind="train",
    outputs="churn_training_run",
    metadata={
        "write": "overwrite",
        "model_task": {
            "model": "churn_v1",
            "role": "train",
            "spec": "models/churn_v1.toml",
            "params": {"C": 1.0},
            "card": {"summary": "...", "owner": "..."},
        },
    },
)
def churn_train(ctx):
    train_task(ctx, churn_train, ctx.read("churn_training"), ctx.read("churn_provenance"), output="churn_training_run")


@task(
    kind="predict",
    outputs="churn_scores",
    metadata={
        "write": "overwrite",
        "model_task": {"model": "churn_v1", "role": "predict", "spec": "models/churn_v1.toml", "alias": "champion"},
    },
)
def churn_predict(ctx):
    predict_task(ctx, churn_predict, ctx.read("churn_prediction"), output="churn_scores")
```

- `model` is the explicit identity (it must match the spec). `scope` (optional, `catalog.schema`) fixes the
  registry location through the context mapping; otherwise the schema of the task's first output is used.
- `evaluate` and `predict` must name exactly one `alias` or `version`. Missing, both, or `latest`-style names are
  rejected when the project is read. Nothing resolves "latest" and nothing retrains as a fallback.
- The experiment path comes from the context parameter `mlflow_experiment` (or `--experiment` in the CLI).
- `predict_task` writes `<key>, <snapshot>, score, prediction, model, model_version`; `evaluate_task` writes
  `model, model_version, split, metric, value`; `train_task` writes one run record (run id, registered name,
  training fingerprint, metrics, code revision). Monitoring consumes the prediction table.

## What a run records

Training validates the dataset first (BS-11: leakage, immature labels, split overlap, feature contract), fits on the
`train` split and evaluates on `train` and `evaluation`. The run holds parameters, metrics, tags (model identity,
code revision, spec hash, feature contract, dataset fingerprints with Delta input versions, dependency versions) and
the artifact: an MLflow sklearn model with signature plus `bricksmith_model.json`, the source of the model card.

## Lifecycle (CLI)

```bash
bricksmith model train    --model churn_v1 --experiment /Users/me/exp            # plan; --apply runs the tasks
bricksmith model register --model churn_v1 --run-id <run> --apply                 # new version
bricksmith model promote  --model churn_v1 --alias champion --version 1 --reviewer me --reason "..." --apply
bricksmith model rollback --model churn_v1 --alias champion --reviewer me --reason "..." --apply
bricksmith model retire   --model churn_v1 [--version 1] --apply
bricksmith model card     --model churn_v1 --alias champion      # or --file bricksmith_model.json (offline)
```

Mutating commands print a plan unless `--apply` is given. A promotion plan shows the evidence (metrics, code
revision, run), the tasks that follow the alias (their results change), the pinned ones (unchanged) and the
tasks downstream of them. Events and alias history are stored as tags, so rollback returns to the previous target.
`retire` exits 4 and refuses while an `evaluate`/`predict` task still resolves to a retiring version, by pin or by
the current alias target; it removes aliases and marks versions retired but keeps training runs and artifacts.

## Artifacts and migration

Loading checks adapter, feature contract and python / scikit-learn / numpy major.minor against the recorded
dependencies. `predict_in_subprocess` does the same in a fresh interpreter. After an environment change,
`relog_model` re-logs a pinned version as a new version of the same identity, requires equal predictions on a
sample, and never moves aliases; promote the new version deliberately.

Limits: the training set is collected to the driver; the scikit-learn adapter takes numeric and boolean features.
