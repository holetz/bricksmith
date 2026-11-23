---
type: decision
title: "0016. Model monitoring judges with relative and control-chart limits, trend and persistence"
description: "Why model metrics are colored through limit kinds of the shared core and replayed into persistence and trend alerts stored as additive columns."
resource: "src/bricksmith/monitor/limits.py, src/bricksmith/monitor/model/**"
timestamp: "2026-11-23T18:00:00-03:00"
audience: both
authority: current
---

# 0016. Model monitoring judges with relative and control-chart limits, trend and persistence

- Status: accepted
- Date: 2026-11-23
- Extends: [0012](0012-model-monitoring-declared-on-tasks.md)

## Context

Model monitoring colored each metric with fixed green and red boundaries, while data stability already used the
shared trend check and alert persistence. Model governance also judges metrics in ways a fixed boundary cannot
express: a deterioration relative to development (a discrimination metric falling more than a given share), a
control chart over the metric's own history (a standardized distance from its baseline mean), and a calibration
ratio band around one.

## Decision

- A limit has a kind, owned by the shared core (`bricksmith.monitor.limits`): `absolute` judges the value,
  `relative` the change `(value - baseline) / |baseline|` against a named reference, `control` the distance
  `(value - mean) / sd`. A missing or zero baseline is `no_baseline`, never a color. Absolute limits serialize as
  before, so existing fingerprints do not change. Data stability accepts absolute limits only.
- A relative limit compares with the metric measured on the `development` reference, or on `out_of_time` when it
  names it.
- A control-chart baseline is the mean and sample standard deviation of the metric measured per observed period
  over the development **and** out-of-time references; development alone underestimates the variance. Fewer than
  `control_min_points` periods give no baseline.
- `calibration_ratio` is colored by default: green within 0.7–1.3, yellow within 0.5–1.5.
- Each model version, segment, column and metric is a series. Its compared values are checked against their trend
  and its lights are replayed into persistence alerts with the shared core; the trend statuses are replayed into
  trend alerts. The gate may fail on an open critical (`critical`) or any open (`alert`) span alert.
- The new facts are additive columns of the existing results, alerts and runs tables; tables created before them
  gain them through the `evolution` write option. Stored baselines let alerts be replayed and recolored without
  measuring again.

## Consequences

- A declaration expresses governance limits directly, and the stored compared value shows what each light judged.
- Control baselines measure the control metrics once per reference period, a cost paid only by metrics declared
  with control limits.
- Upgrading colors `calibration_ratio` where it was `no_rule`; a `fail_on: red` gate may start failing on it.
  Declaring `"calibration_ratio": null` restores the previous behavior.
- Alerts keep one run-keyed shape: light alerts and span alerts differ by `kind`, and the open alerts view filters
  on `state`.
