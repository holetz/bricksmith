---
type: how-to
title: "Model evaluation and technical documentation"
description: "How to evaluate a model on every sample and closing and render its technical document from a run's persisted artifacts."
resource: "src/bricksmith/mlops/evaluation/**, src/bricksmith/mlops/documentation/**"
timestamp: "2026-11-24T12:00:00-03:00"
audience: both
authority: current
---

# Model evaluation and technical documentation

Two packages close the development of a model:

- `bricksmith.mlops.evaluation` measures every sample (train, test, out-of-time, through-the-door) and every
  closing against a reference sample. It covers discrimination, stability and calibration by grade.
- `bricksmith.mlops.documentation` renders a technical model document, in Markdown and as self-contained HTML,
  from the artifacts one run persisted. The same run gives the same bytes every time.

Both import only the standard library. Spark and MLflow are loaded only where they are used.

The [end-to-end model flow](model-flow.md) evaluates scored samples, logs the evaluation to the training run and publishes the technical document beside it.

## Evaluate every sample

The input is the scored development set: one row per entity and closing, with a sample column, the score, the
label and, optionally, the grade and the selected features. Rows can be a list of mappings or a Spark DataFrame.

```python
from bricksmith.mlops.evaluation import evaluate, log_evaluation
from bricksmith.mlops.stages import parse_development

EVALUATION = {
    "name": "churn_v1",
    "score": "score",  # the model output; also read as the probability unless "probability" is declared
    "label": "churned",
    "grade": "grade",  # optional: the per-grade calibration table and its inversions
    "features": ["tenure_months", "days_past_due"],  # CSI against the reference
    "categorical": ["segment"],  # features binned by category instead of quantiles
}

development = parse_development(DEVELOPMENT)  # gives the closing column and the grade order (its bands)
result = evaluate(scored_frame, EVALUATION, development)
result.samples  # ("train", "test", "oot", "ttd"): the reference first
result.value("oot", "ks")  # a whole-sample value
result.value("oot", "csi", "tenure_months", closing="2026-01-31")  # one closing
log_evaluation(result)  # params, metrics and JSON artifacts when an MLflow run is active
```

Every sample is measured against the **reference** sample, which is `train` by default. The score, the grade and
each declared feature get bins fitted on the reference, so PSI and CSI compare each sample with the same bins.
The metrics come from the shared monitor core (`bricksmith.monitor`). This is the code that model monitoring
uses later, so a value from development and a value from production mean the same thing.

| Metric | What it measures |
| --- | --- |
| `row_count`, `null_rate` | rows, and the null share of the score or a feature |
| `auc`, `gini`, `ks` | discrimination of the score against the label |
| `psi` | score distribution against the reference |
| `csi` (subject: the feature) | feature distribution against the reference |
| `grade_psi` | grade distribution against the reference |
| `brier`, `ece` | calibration of the probability |
| `inversions` | declared grades whose observed rate moves against `direction` |

Each metric is computed for the whole sample (`closing` is the empty string) and, unless `per_closing` is
`false`, for every closing. A metric that cannot be computed has the status `unavailable` and a reason. It never
gets a guessed value.

The grade table holds, for each sample, closing and grade: rows, events, observed rate, expected rate (the mean
probability), the observed/expected ratio, the Brier score and whether the grade is an inversion. Grades follow
the declared order (`grades`, else the development bands). A grade outside that order is listed last and is
never counted as an inversion.

### Options

| Key | Default | Meaning |
| --- | --- | --- |
| `name` | `evaluation` | identifies the evaluation in tables and runs |
| `probability` | the score | the column read as the event probability |
| `direction` | `increasing` | the expected way the observed rate moves along the grades (`increasing` or `decreasing`) |
| `sample`, `closing` | `sample`, the development closing | the sample and closing columns |
| `reference`, `samples` | `train`, every sample present | the reference sample, and the samples to evaluate |
| `feature_bins` | ten quantile bins | a bin configuration for each feature |
| `score_bins`, `calibration_bins` | 10, 10 | bins of the score and of the calibration |
| `epsilon` | 0.0001 | the floor for an empty bin share in PSI and CSI |

Unknown keys are refused, and so is a column that plays two roles. A misspelled option never silently falls
back to its default.

## Store the evaluation

Three tables, keyed for a merge, so a rerun with the same evaluation name and run id replaces its own rows:

| Table kind | Key | Columns |
| --- | --- | --- |
| `results` | `evaluation, run_id, sample, closing, metric, subject` | `config_fingerprint, value, count, status, reason, computed_at` |
| `distribution` | `evaluation, run_id, sample, closing, metric, subject, bin` | `config_fingerprint, reference_share, current_share, contribution, computed_at` |
| `grades` | `evaluation, run_id, sample, closing, grade` | `config_fingerprint, grade_order, rows, events, observed_rate, expected_rate, observed_expected, brier, inversion, computed_at` |

```python
from bricksmith.mlops.evaluation import evaluate, table_rows, to_frame, write_options


@task(kind="table", outputs="churn_evaluation_results", metadata=write_options("results"))
def churn_evaluation_results(ctx):
    frame = ctx.read("churn_scored_development")
    result = evaluate(frame, EVALUATION, development)
    rows = table_rows(result, "results", run_id="2026-03-31", now=datetime.now(UTC).isoformat())
    ctx.write("churn_evaluation_results", to_frame(frame.sparkSession, "results", rows))
```

`log_evaluation` logs the declaration as `evaluation.*` params, plus its fingerprint, and the whole-sample values as
`evaluation.<sample>.<metric>[.<feature>]` metrics. It writes `evaluation/results.json`, `distribution.json`,
`grades.json` and `evaluation.json` as artifacts. Without MLflow or an active run it does nothing and returns
`False`.

## Document a run

The document is rendered from artifacts only. Log them to the training run, then render from the run id:

| Artifact | Written by | Feeds |
| --- | --- | --- |
| `development/development.json`, `composition.json` | `log_development(development, composition(rows, development, by=("sample",)))` | population and exclusions, target, sample windows, feature store lookups |
| `selection/ledger.json`, `variables.json`, `features.json`, `woe.json` | `log_selection` ([feature selection](feature-selection.md)) | feature selection, binning and weight of evidence |
| `evaluation/*.json` | `log_evaluation` | evaluation by sample, calibration by grade, stability |
| `documentation/specification.json` | `log_specification(model_specification(estimator, features))` | model specification |
| `documentation/narrative.json` | `log_narrative({...})` | purpose and use, limitations, inherent risk and materiality |
| `model/bricksmith_model.json` | the training task ([models](models.md)) | document information, input table versions |

```python
import mlflow
from bricksmith.mlops.documentation import (
    collect_run,
    log_development,
    log_narrative,
    log_specification,
    model_specification,
    publish_document,
)

with mlflow.start_run() as run:
    log_development(development, composition_rows)
    log_selection(selection)
    log_evaluation(result)
    log_specification(model_specification(estimator, selection.selected))
    log_narrative(
        {
            "purpose": "Rank active customers by the likelihood of leaving.",
            "intended_use": "Retention campaigns only.",
            "limitations": "Not validated for customers without a full history.",
        }
    )

client = mlflow.MlflowClient()
inputs = collect_run(client, run.info.run_id)
published = publish_document(client, inputs, store=store, model="main.churn.churn_v1", version=3)
```

`publish_document` logs `documentation/model_document.md` and `documentation/model_document.html` to the same run.
Given a registered version, it also sets the version tag `bricksmith_documentation` to JSON holding the run id and
both artifact paths. Pass `model` and `version` together, or neither. With a `ModelStore` the tag goes through the
store; otherwise it goes through the client.
The tag key holds no `.`, because Unity Catalog refuses tag keys with `.`, `=`, `>`, `<`, `%`, `&`, `?` or `\`.

`collect_run` downloads each artifact the run carries and skips the ones it lacks. It handles both download
layouts: a local store keeps the artifact path, while a Databricks workspace returns the file by its bare name.
A missing optional artifact is reported in several ways, including the workspace's "Failed to download artifacts"
message, and every one of them means "not recorded". Any other error propagates. Offline,
`write_directory(inputs, path)` and `read_directory(path, run_id)` use the same layout.

### What the document holds

The sections, in order: document information, purpose and use, population and exclusions, target definition
and maturity, sample windows, feature sources, feature selection, binning and weight of evidence, model
specification, evaluation by sample, calibration by grade, stability, limitations, and inherent risk and
materiality. Section names describe their content and name no regulation. Nothing in the document claims
conformity with one.

- **Numbers come from artifacts only.** The narrative is declared text for seven fields: `purpose`,
  `intended_use`, `population`, `target`, `limitations`, `inherent_risk` and `materiality`. A narrative containing
  a digit is refused. Cells format floats with four decimals and show a missing value as `n/a`.
- **A missing artifact says so.** A section whose artifact the run lacks reads "Not recorded." and invents
  nothing.
- **The model specification** follows the estimator. A linear estimator gives the intercept and the
  coefficients; given the selection's WoE tables and a `Scaling` (points to double the odds, base points and base
  odds), it also gives the points of every WoE group. A tree estimator gives its importances, highest first.
  Anything else records its class.
- **Regeneration is byte for byte.** The same artifacts and the same `generated_at` give identical Markdown and
  HTML. `generated_at` defaults to the current UTC time, so pass it explicitly to reproduce an earlier document.
  The HTML embeds its style and loads nothing from the network.

## Engines

The row engine is the reference. The Spark engine computes the same counts with built-in functions only, so it
runs where Python UDFs are refused, and folds them with the same code. A remote test on the free workspace
proves that the Spark evaluation equals the row engine. It also proves that a document logged to a real run is
regenerated byte for byte from that run's artifacts ([evidence](../project/evidence/model-evaluation-free.md)).
The choices behind both packages are recorded in
[decision 0020](../decisions/0020-model-evaluation-and-documentation.md).

## Limits

- The free workspace cannot register a model version from a run, because its artifact listing is empty. There,
  the version tag is proven with unit tests over a fake store.
- The narrative is written by people. Nothing in the document is generated by a language model.
- Performance estimation before labels mature is not part of the evaluation. Monitor the production score with
  [model monitoring on tasks](model-monitoring.md).
