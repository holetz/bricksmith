---
type: how-to
title: "End-to-end model flow"
description: "How the model flow example takes synthetic monthly data through feature tables, development stages, selection, training, pinned scoring, model monitoring and the technical document on one runner."
resource: "examples/model_flow/**"
timestamp: "2026-11-24T23:30:00-03:00"
audience: both
authority: current
---

# End-to-end model flow

`examples/model_flow/` is one bricksmith project that runs a whole batch model through the shared runner. Each stage
is an ordinary task, so one graph and one runner cover the data, the model and its monitoring
([decision 0009](../decisions/0009-one-library-shared-task-scopes.md)):

1. a synthetic periodic source;
2. two closing-based feature tables at two entity grains, with data stability;
3. the development stages: population, labels and the training ABT;
4. the feature selection funnel and its ledger;
5. training, with every artifact the technical document needs;
6. registration and an alias, as a deliberate step;
7. scoring pinned to an explicit model, with model monitoring on the prediction table;
8. evaluation, and the technical document published beside the training run.

All data is synthetic. The project uses the catalog alias `demo` and the schema alias `flow`, and declares one
model, `attrition`. This page walks the example stage by stage. Each stage links the guide that covers its
capability in full.

```bash
bricksmith tasks validate --project examples/model_flow   # 11 tasks, 21 edges, 0 errors
```

## 1. The periodic source

`catalogs/demo/flow/source.py` writes `accounts_monthly`: one row per account and monthly closing (a month-end
date) over two years. It has 240 accounts held by 120 customers, with balances, payments, days past due, a status,
a segment, a closed flag and the account's tenure. Some accounts close during the history. A few closings before
they close, their balance falls and their days past due rise, so windowed features can see it coming.

The generator uses a fixed seed and never reads the clock, so every run writes the same rows.

## 2. Feature tables with data stability

`catalogs/demo/flow/features.py` declares two feature tables on ordinary tasks
([feature store](feature-store.md)):

- `account_features`, entity `account_id`: balance means and slopes over three and six closings, mean payments,
  the worst days past due, a payment ratio, the count of late closings and the segment.
- `customer_features`, entity `customer_id`: the same kind of windows over each customer's total balance and worst
  days past due, plus the number of open accounts.

Both compute their windows with `compute_features` under `engine="auto"`
([windowed feature engineering](features.md)). `auto` resolves to the JVM kernel. Serverless compute reaches the
kernel through a Unity Catalog function, so set the task parameter `kernel_function` to the function you installed
with `bricksmith features install-kernel`. When the compute refuses the kernel, `auto` falls back to native Spark.
The run report then records the fallback and its reason under `feature_engines` in the task's result:

```json
{"requested": "auto", "engine": "spark", "path": "", "outputs": 8,
 "reason": "session: KernelUnavailableError: ..."}
```

The JVM kernel is the default engine because it was the fastest in the benchmark; see
[decision 0014](../decisions/0014-feature-engineering-engines.md) and
[decision 0019](../decisions/0019-jvm-array-kernel-default-engine.md). The closing-based feature store is
[decision 0013](../decisions/0013-closing-based-feature-store.md).

Both tables also declare `stability`, with a fixed baseline over the development closings
([data stability](stability.md)). After each write, the runner profiles every closing and stores the results in the
`stability_*` tables of the schema. The default gate warns and never fails the run, so data stability watches
exactly the tables the model reads.

## 3. Development stages

`models/attrition.toml` is the development declaration ([model development stages](model-development.md)):

- samples `dev` (train), `oot` (out of time) and `live` (prediction), each a window of closings;
- the label `attrited`: the account closes within the next three closings;
- an exclusion cascade: `closed`, then `new_account` (tenure under three months);
- grade bands `A` to `D`;
- three lookups: every account feature at the observation closing, two account features three closings earlier
  (prefix `prev3_`), and the customer features joined through the spine's `customer_id`.

`catalogs/demo/flow/development.py` holds the tasks. `attrition_population` is the spine: every account and closing,
flagged by the cascade. Excluded rows stay, and only the training ABT drops them. `attrition_labels` writes one
label per account and closing whose horizon is observed by `as_of`. `attrition_training` builds the ABT of the
labelled samples with `build_abt`. It joins both feature tables by exact closing and records the Delta version of
each table it read in `feature_reads`.

Lookups name feature tables by their short output names. The helper `feature_inputs(ctx)` binds them to the
physical tables of the run through `ctx.entry.reads`, so the same declaration works in every context.

## 4. Feature selection

`attrition_selection_ledger` and `attrition_selection_variables` run the selection funnel on the training ABT
([feature selection](feature-selection.md)). They store, for every candidate, why it was kept or dropped. Each
task's write metadata equals `write_options("ledger")` or `write_options("variables")`, a merge on the table key.
It is spelled out as a literal, because task declarations must be literal for static discovery.

## 5. Training

`catalogs/demo/flow/model.py` holds the model tasks. `attrition_train` fits a scikit-learn logistic regression on
the `dev` sample, using the numeric features the funnel kept. A null feature (an empty window, a missing lookup)
reads as 0.0, and scoring applies the same rule. The task creates one MLflow run in the experiment named by the
context parameter `mlflow_experiment`. It logs to that run:

- the development declaration and its exclusion composition by sample;
- the declared narrative, the model specification (intercept and coefficients) and the selection ledger,
  variables and WoE tables, which are the inputs of the technical document;
- a bricksmith model artifact, `model/` with `bricksmith_model.json`, so a workspace that supports registration
  can register the run.

The task writes the run id to `attrition_training_run`. It creates the run through the tracking client instead of
`ModelStore.log_run`, because that call verifies the upload by listing artifacts, and Free Edition does not list
them.

## 6. Registration and the alias

Registration is a deliberate step, never a side effect of training ([models](models.md)):

```python
from bricksmith.mlops.lifecycle import apply_alias_plan, plan_promotion
from bricksmith.mlops.training import register_run

version = register_run(store, "main.flow.attrition", "attrition", run_id)
apply_alias_plan(
    store,
    plan_promotion(
        store, "main.flow.attrition", "attrition", "champion", version, reviewer="you", reason="first candidate"
    ),
)
```

The CLI does the same with `bricksmith model register` and `bricksmith model promote`. Free Edition cannot register a
model version, because it does not return the run's artifacts to the registry. On that workspace the proof records
registration as Unsupported, with the platform's reason, and scores by run.

## 7. Pinned scoring and model monitoring

`attrition_scores` scores every row of the `dev`, `oot` and `live` samples, excluded rows included, with ABTs built
for scoring. It always pins its model:

| Task parameter | Model used |
| --- | --- |
| none | the version behind alias `champion` |
| `model_alias=<alias>` | the version behind that alias |
| `model_run=<run id>` | the model artifact of that run, for a run that is not registered yet |

Nothing resolves "latest", and nothing trains on a missing model. A run pin downloads the artifact one file at a
time and checks the model identity and the runtime before loading it.

The output follows the prediction contract of `prediction_frame`: entity, closing, model name, version and alias,
score, grade from the bands, sample, sample role and the cascade columns. It also carries the matured label and three
monitored features. A registered pin writes its version number; a run pin writes `run:<run id>`.

The task declares `model_monitoring` ([model monitoring](model-monitoring.md)). It uses the `dev` and `oot` samples
as references, the grade, the label with a three-period lag, CSI on the three features, and their null rate. Monitoring
fits its own feature bins (`feature_binning` is `own`). Reusing the stability binning of `account_features` would
need a fixed stability `dataset` name on that table, because the default name is the physical table and changes from
one context to another.

## 8. Evaluation and the technical document

`attrition_evaluation` evaluates the labelled, non-excluded scored rows against the `dev` reference
([model evaluation and technical documentation](model-evaluation.md)). It writes the results, distribution and grade
tables and logs the evaluation to the pinned training run. Then it renders the technical document from that run's
artifacts and publishes it beside them as `documentation/model_document.md` and `.html`. When the pin is a
registered version, the version also gets the documentation tag.

## Run it on Free Edition

`tests/remote/test_model_flow.py` runs the whole example on the selected workspace, in a test-owned schema. It
installs the kernel in a test volume, attempts registration and checks every stage's tables, then removes the
experiment, the registered model, the volume and the schema. Build the kernel JAR first; it is a build artifact
that is not tracked in git:

```bash
python scripts/build_kernel.py
BRICKSMITH_TEST_ENV=free BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_model_flow.py -q -s
```

The latest run is recorded in the [model flow evidence](../project/evidence/model-flow-free.md).
