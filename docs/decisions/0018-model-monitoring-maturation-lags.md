---
type: decision
title: "0018. Model monitoring separates the monitoring period from the data period with declared lags"
description: "Why a metric may read a data period earlier than the monitoring period it is shown under, in observed periods or calendar months, and how that is stored."
resource: "src/bricksmith/monitor/model/**"
timestamp: "2026-11-23T20:00:00-03:00"
audience: both
authority: current
---

# 0018. Model monitoring separates the monitoring period from the data period with declared lags

- Status: accepted
- Date: 2026-11-23
- Extends: [0012](0012-model-monitoring-declared-on-tasks.md)

## Context

Some metrics are only meaningful once outcomes mature, and a loaded period mixes mature and immature data if every
metric reads the period it is monitored under. Decision 0012 gave label metrics one `label_lag` counted in observed
periods. Projects also need a lag per metric, and a lag in calendar months, because a missing month must not make a
neighbouring period stand in for the one that matured.

## Decision

- `monitoring_period` stays the key of series, trend, persistence, alerts and reprocessing. `data_period` is the
  period whose rows a result measured; the lag only moves what is read, never the row's identity.
- `lags` maps a metric or a family to a lag: an integer or `{periods: n}` (observed periods, as `label_lag`), or
  `{months: n}` (calendar months). The metric wins over its family, the family over the default; the default is
  `label_lag` for label families and no lag otherwise, so earlier declarations read the same.
- A calendar lag subtracts whole months from the monitoring period and renders the result in the same text format
  (`YYYY-MM`, `YYYY-MM-DD`, `YYYYMM`, `YYYYMMDD`; the day clamps to the month's last day). When that period is
  not in the monitored rows, or the text is not one of those formats, the result has no value and the reason
  `data_period_unavailable`. It is never shifted to the nearest observed period, which would present immature data
  as mature.
- Metrics of a family that resolve to the same data period are measured in one pass; each distinct lag costs one
  more pass over that family only.
- Results carry `lag` and `lag_unit` beside `data_period`, as additive columns migrated through the `evolution`
  write option. The dashboard views select every result column, so they show both. Lags never trigger a binning
  refit.

## Consequences

- A dashboard can show, per metric, which data a result measured and how far behind the monitoring period it was.
- A calendar lag needs date-like period text; other period text keeps the observed lag.
- A gap in the loads shows as an explicit unavailable result for a calendar lag, where an observed lag silently
  reads the previous loaded period.
