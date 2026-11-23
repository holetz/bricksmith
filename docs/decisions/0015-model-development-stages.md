---
type: decision
title: "0015. Model development stages on one spine"
description: "Why named sample windows, a kept exclusion cascade and the feature store spine give training and scoring one code path."
resource: "src/bricksmith/mlops/stages/**"
timestamp: "2026-11-23T09:27:03-03:00"
audience: both
authority: current
---

# 0015. Model development stages on one spine

- Status: accepted
- Date: 2026-11-23
- Extends: [0013](0013-closing-based-feature-store.md) and [0012](0012-model-monitoring-declared-on-tasks.md)

## Context

A model's population, training ABT, scoring ABT, label and predictions must behave the same in development and in
production. Some failures are common:

- out-of-time windows that overlap the training window;
- excluded rows dropped in silence, so the exclusion composition cannot be monitored;
- training that reads frozen copies while scoring reads live sources through branching in each task;
- labels computed without a declared forward window or maturity.

The existing model dataset API works on snapshot dates and day-based maturity. It moves excluded rows to a separate
list and has its own feature join. The closing-based feature store (0013) already provides one builder for training
and scoring.

## Decision

- A `model_development` declaration names samples: inclusive windows of closings, each with a role (`train`, `test`,
  `out_of_time`, `through_the_door`, `prediction`, `monitoring`). Parsing refuses an out-of-time window that starts
  on or before the last training closing. It also refuses a labelled sample whose last closing plus the label
  horizon is after the declared `as_of`.
- The label declares a forward window in periods and a definition. Labels join by exact closing. An immature,
  missing or repeated label is refused with counts; it is never dropped.
- The exclusion cascade is ordered. Every row keeps all the rules it meets, the first one and an `excluded` flag.
  Excluded rows stay in the population and in scoring. Only the training ABT filters them. The composition is
  computed the same way on populations and on prediction tables.
- The population is the feature store spine. Training and scoring call the same builder with the same lookups; the
  sample only selects closings. Train and test windows that share closings are divided by a declared seeded split.
  Stratified sampling by label is seeded too. All seeded choices hash the entity keys and the closing, so engines
  and reruns agree.
- Predictions have one contract: keys, closing, model name, version and alias, score, grade, sample, sample role
  and the cascade columns. Model monitoring reads it with the sample column as its reference selector.
- A row engine is the reference. A Spark engine built only from built-in functions is proved equal to it.

## Consequences

- Development and monitoring share one vocabulary for samples and exclusions; references are sample names.
- The model dataset API is unchanged. Consolidating it onto these stages is a possible follow-up.
- `as_of` is declared, not derived from the label table, so parsing reads no data.
- Excluded rows are scored. A project that scores only eligible rows filters `excluded` itself.
