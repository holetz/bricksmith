---
type: decision
title: "0020. Model evaluation on the monitor core, documentation from persisted artifacts"
description: "Why development samples are evaluated with the monitoring core and the technical model document is rendered only from a run's persisted artifacts."
resource: "src/bricksmith/mlops/evaluation/**, src/bricksmith/mlops/documentation/**"
timestamp: "2026-11-24T12:00:00-03:00"
audience: both
authority: current
---

# 0020. Model evaluation on the monitor core, documentation from persisted artifacts

- Status: accepted
- Date: 2026-11-24
- Extends: [0015](0015-model-development-stages.md) and [0008](0008-monitor-succeeds-psi-spark.md)

## Context

A model must be evaluated on every development sample (train, test, out-of-time, through-the-door) and closing.
It also needs a technical document that a reviewer can trust. This often goes wrong in a few ways:

- development metrics are computed by notebook code that differs from the code used in production monitoring,
  so a KS or PSI in development and in production do not mean the same thing;
- the document is written by hand or from live data, so numbers drift from what the run recorded and cannot be
  reproduced;
- numbers are typed into narrative text, where nothing checks them;
- documents are branded as meeting a named regulation, a claim that dates as regulations are replaced.

The monitoring core (0008) already computes discrimination, stability and calibration with fitted references.
Model development stages (0015) already name the samples and the grade bands.

## Decision

- **Evaluation runs on the monitor core.** `evaluate` fits the score, grade and feature bins on a reference
  sample (train by default) and measures each sample and closing with `bricksmith.monitor`: KS, AUC, Gini,
  PSI of the score, CSI of the selected features, grade PSI, Brier and ECE. It adds only what monitoring lacks:
  the per-grade calibration table and the count of inversions along the declared grade order. A metric that
  cannot be computed is `unavailable` with a reason, never a guessed value.
- **One row engine, one Spark engine.** The row engine is the reference. The Spark engine reads counts with
  built-in functions only and folds them with the same code. A remote test proves the two equal.
- **Evaluation persists three merge-keyed tables** (results, distribution, grades) and an MLflow logging of
  params, whole-sample metrics and JSON artifacts.
- **The document is rendered only from persisted artifacts and a run id.** The development declaration and
  composition, the selection ledger and WoE tables, the evaluation tables, the model specification, the model
  metadata and a declared narrative. Nothing is read from live tables. A missing artifact renders "Not
  recorded." The only value that is not an artifact is an explicit `generated_at`, so the same run gives the
  same Markdown and HTML bytes.
- **Narrative is declared text without digits.** Numbers come from artifacts only. No language model writes any
  part of the document.
- **Section names describe content and name no regulation.** The document makes no conformity claim.
- **Publication is logged and tagged.** The Markdown and the self-contained HTML are logged to the same run.
  Given a registered version, the tag `bricksmith_documentation` points to them. The tag key avoids the
  characters Unity Catalog refuses in tag keys.

## Consequences

- A value from development and a value from production monitoring are produced by the same code and the same
  fitted bins.
- A document can be regenerated for review from its run alone. A change in the document is a change in the
  artifacts or in bricksmith, never in hand-edited text.
- Collecting a run must cope with artifact stores that report a missing file or lay out downloads differently.
  The collector handles both behaviours observed on a Databricks workspace.
- The free workspace cannot register a model version, so the version tag is proven with unit tests there.
  Corporate proof of the tag comes from a separate validation run.
- Performance estimation before labels mature, scorecard search and model-family search stay out of scope.
