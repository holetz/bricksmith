---
type: how-to
title: "Model development stages"
description: "How to declare named samples, labels and an exclusion cascade, and build training and scoring ABTs and predictions from one spine."
resource: "src/bricksmith/mlops/stages/**"
timestamp: "2026-11-23T09:27:03-03:00"
audience: both
authority: current
---

# Model development stages

A model's data stages are the population, the analytical base table (ABT) for training and for scoring, the label
and the predictions. They must behave the same in development and in production. `bricksmith.mlops.stages` gives
them one declaration and one code path:

- **Named samples** are windows of closings with a role. Validation refuses an out-of-time window that overlaps the
  training window, and a label that has not matured by the sample's last closing.
- An **exclusion cascade** flags every population row. Excluded rows stay in the population; only the training ABT
  filters them, so monitoring can recompute the exclusion composition of scored populations.
- The **population is the spine** of the [feature store](feature-store.md) builder. The training ABT and the scoring
  ABT use the same lookups; the sample only selects closings.
- **Predictions** follow one contract that [model monitoring](model-monitoring.md) reads as is.

Closings, frequencies and lookups are the feature store's. See
[decision 0015](../decisions/0015-model-development-stages.md) for the reasons.

The [end-to-end model flow](model-flow.md) declares samples, a label, an exclusion cascade, bands and lookups at two grains, and runs every stage on the shared runner.

## Declare the development

The declaration is a plain mapping: inline, or a TOML or JSON file read with `load_development_file`.

```python
from bricksmith.mlops.stages import parse_development

development = parse_development(
    {
        "model": "churn",
        "entity_keys": ["account_id"],
        "closing": "closing_date",  # default
        "frequency": "monthly",  # default; any feature store frequency
        "as_of": "2024-12-31",  # the latest closing whose performance is observed
        "samples": {
            "dev": {"role": "train", "start": "2024-01-31", "end": "2024-04-30"},
            "holdout": {"role": "test", "start": "2024-03-31", "end": "2024-04-30"},
            "oot": {"role": "out_of_time", "start": "2024-05-31", "end": "2024-06-30"},
            "ttd": {"role": "through_the_door", "start": "2024-07-31", "end": "2024-09-30"},
            "live": {"role": "prediction", "start": "2024-10-31", "end": "2024-12-31"},
        },
        "label": {"name": "bad", "horizon": 3, "definition": "any severe delinquency in the next three closings"},
        "split": {"seed": 7, "test_fraction": 0.3},  # divides closings shared by train and test
        "stratify": {"seed": 11, "fractions": {"0": 0.5}},  # optional: keep half of the label-0 rows
        "exclusions": [  # ordered; the predicate grammar of model datasets
            {"name": "closed", "where": {"column": "status", "op": "eq", "value": "closed"}},
            {"name": "young", "where": {"column": "tenure", "op": "lt", "value": 3}},
        ],
        "bands": [{"name": "A", "upper": 0.1}, {"name": "B", "upper": 0.4}, {"name": "C"}],  # optional grades
        "lookups": [{"table": "main.features.account_balances"}],  # feature store lookups
    }
)
```

| Key | Meaning |
| --- | --- |
| `samples` | Name → `{role, start, end}`. `start` and `end` are closings of the frequency, both inclusive. Roles: `train`, `test`, `out_of_time`, `through_the_door`, `prediction`, `monitoring`. |
| `label` | `name`, `horizon` (the forward window, in periods, at least 1) and `definition` (how the label is computed). Required when a `train`, `test` or `out_of_time` sample exists. |
| `as_of` | The latest closing with observed performance. A labelled sample's last closing plus `horizon` periods must not be after it. |
| `split` | Required when a `test` window shares closings with a `train` window. On those closings a row goes to test when its seeded position is below `test_fraction`, else to train; never to both. |
| `stratify` | Optional. Keeps a training row when its seeded position is below the fraction of its label value (written as text: `true` and `1.0` read `1`). Values not listed keep every row. |
| `exclusions` | The ordered cascade. Each rule is a name and a predicate (`eq`, `ne`, `lt`, `le`, `gt`, `ge`, `in`, `is_null`, `not_null`, `all`, `any`, `not`). |
| `bands` | Optional grades from the score: each band takes scores up to its `upper` (inclusive); the last band is open. |
| `lookups` | Feature store lookups: `{table, features, keys, offset, prefix}`. |

Refused at parse time: an `out_of_time` window that starts on or before the last closing of a `train` window; a
labelled sample whose label matures after `as_of`; a start or end that is not a closing; overlapping train and test
windows without `split`; repeated names; and unknown keys.

Every seeded choice is a SHA-256 position of the entity keys and the closing. The same rows are selected on every
run and by both engines, whatever the row order.

## The exclusion cascade

`apply_cascade` adds three columns to each population row:

| Column | Value |
| --- | --- |
| `exclusion_rules` | Every rule the row meets, in cascade order (`array<string>`, empty when none). |
| `exclusion_first` | The first rule met, or null. |
| `excluded` | Whether any rule was met. |

`composition(rows, development, by=["sample"])` returns one dashboard-ready row per group, closing and rule:
`total`, `excluded`, `met` (rows meeting the rule), `first` (rows whose first rule it is) and `first_share`. Run it
on a population or on a prediction table to see the composition of each scored period.

## Build the ABTs

```python
from bricksmith.mlops.stages.spark import build_abt, prediction_frame

definitions = {"main.features.account_balances": balances_definition}  # feature store declarations
training = build_abt(population, development, "dev", definitions=definitions, spark=spark, labels=labels)
holdout = build_abt(population, development, "holdout", definitions=definitions, spark=spark, labels=labels)
scoring = build_abt(population, development, "live", purpose="scoring", definitions=definitions, spark=spark)
```

- `population` holds the entity keys, the closing and the columns the cascade reads. When it has no cascade columns,
  the cascade runs first.
- **Training** (`purpose="training"`, the default) needs a labelled sample. It keeps the sample's rows that are not
  excluded, attaches the label, applies `stratify` and joins the lookups.
- **Scoring** keeps every row of the sample, excluded ones included with their flags, and joins the same lookups.
- Both add `sample` (the sample name) and `sample_role`, and return the feature store `TrainingSet`: `data` plus
  the `reads` (Delta version per feature table) to log with the model.
- `labels` rows are keyed by the entity keys and the observation closing and carry the label computed over the
  forward window. Without them the population must carry the label column. An immature, missing or repeated label
  is refused with a count and examples; it is never dropped in silence.

To score development and out-of-time samples for monitoring references, build them with `purpose="scoring"`.

## Write predictions

```python
scored = scoring.data.withColumn("score", model_score)  # your model's score
predictions = prediction_frame(scored, development, model_version=3, model_alias="champion")
ctx.write("churn_predictions", predictions)
```

The prediction contract is: entity keys, closing, `model_name`, `model_version`, `model_alias`, `score`, `grade`
(from `bands`, else the row's own `grade`), `sample`, `sample_role`, `exclusion_rules`, `exclusion_first` and
`excluded`. A null key, closing or score, and the same entity, closing and model version twice, are refused.

Declare [model monitoring](model-monitoring.md) on the task that writes it, with the closing as period and the
sample column for the references:

```python
metadata = {
    "write": "overwrite",
    "model_monitoring": {
        "model": "churn",
        "period": "closing_date",
        "score": "score",
        "grade": "grade",
        "sample": "sample",
        "references": {"development": {"sample": "dev"}, "out_of_time": {"sample": "oot"}},
        "model_version_column": "model_version",
    },
}
```

## Engines

The row engine (`bricksmith.mlops.stages.rows`, plain mappings) is the reference. The Spark engine
(`bricksmith.mlops.stages.spark`) has the same functions over DataFrames and uses only built-in functions, so it
runs where Python UDFs are refused. Both are proved equal on the free workspace, and a task-written prediction table
is read by model monitoring ([evidence](../project/evidence/model-stages-free.md)). Importing
`bricksmith.mlops.stages` loads only the standard library.

Evaluate the scored samples and render the technical model document with
[model evaluation and technical documentation](model-evaluation.md).

The older [model datasets](model-datasets.md) API (snapshot dates, day-based maturity) is unchanged.
