---
type: decision
title: "0012. Model monitoring is declared on tasks and stored in trusted run tables"
description: "Why model monitoring runs in the shared runner from a task declaration and writes its own run-keyed tables."
resource: "src/bricksmith/monitor/model/**, src/bricksmith/runner/**"
timestamp: "2026-11-23T12:00:00-03:00"
audience: both
authority: current
---

# 0012. Model monitoring is declared on tasks and stored in trusted run tables

- Status: accepted
- Date: 2026-11-23
- Extends: [0008](0008-monitor-succeeds-psi-spark.md) and [0009](0009-one-library-shared-task-scopes.md)

## Context

The monitoring capability measured references, drift and model quality from tables, but a project had to call it
from its own task and store the results itself. Data stability already showed a better shape: a declaration on the
task that writes the data, a post-write check in the shared runner, dashboard tables and a gate. Model monitoring
adds needs that data stability does not have: labels mature after the prediction, the comparison is against a
development sample (and an out-of-time one) rather than the previous period, and a partially computed run must not
look complete on a dashboard.

## Decision

- A task that writes predictions declares `metadata={"model_monitoring": {...}}`. The plan lists the model
  monitoring tables as declared `merge` targets of that task; after a successful write, the shared runner runs data
  stability (when declared) and then model monitoring, and the declared gate may fail the task.
- bricksmith computes and stores its own monitoring tables. Databricks data profiling is not a dependency.
- The metrics come from the shared monitoring core, so rows and Spark DataFrames give identical results, and feature
  CSI may reuse the stored bins of a data-stability binning instead of fitting its own.
- Every result row carries a `monitoring_period` and a `data_period`. Label metrics read the period `label_lag`
  observed periods earlier; the lag counts periods present in the data, not calendar steps.
- Results, details and alerts are keyed with the run id, and the run record is written last with status `success`,
  `partial` or `failed`. Each metric family is computed in isolation: a failing family marks the run `partial`.
  Dashboard views read only finished runs (`success` or `partial`) and, per model, version and period, the latest
  one.
- Alerts can be replayed from stored result lights without recomputing metrics.

## Consequences

- Monitoring a model needs no project code beyond the declaration, and the plan shows its effects before a run.
- Reruns append run-keyed rows instead of overwriting history; the views pick the latest trusted run, so storage
  grows with runs and retention is a project choice.
- A run that dies before its record is invisible to the views, at the cost of leaving unread rows behind.
- Persistence, alert lifecycle, per-metric lags and per-grade calibration build on these tables in later
  specifications.
