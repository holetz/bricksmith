---
type: decision
title: "0022. Credit-model performance checks are opt-in model monitoring families"
description: "Why per-grade calibration tests, grade inversions, early-warning horizons, the exclusion mix and the bad-rate chart are model monitoring families gated on expected events."
resource: "src/bricksmith/monitor/credit.py, src/bricksmith/monitor/model/**"
timestamp: "2026-11-24T22:30:00-03:00"
audience: both
authority: current
---

# 0022. Credit-model performance checks are opt-in model monitoring families

- Status: accepted
- Date: 2026-11-24
- Extends: [0012](0012-model-monitoring-declared-on-tasks.md), [0016](0016-model-monitoring-limit-kinds.md) and
  [0021](0021-limit-rules-selectors.md)

## Context

Rating-model governance looks for failures that discrimination and a global calibration ratio do not show. A grade
can leave its predicted band. Grades can fall out of order. Discrimination can decay on early horizons before the
full target matures. The exclusion mix of the scored population can change. A count test is also misleading on a
grade with few expected events, whatever it observed.

## Decision

- Five families join model monitoring: `grade_calibration`, `grade_order`, `early_warning`, `exclusion_mix` and
  `bad_rate_chart`. They run when the declaration carries a `credit` block, or when `metrics` lists them.
  Declarations without either keep their families, rows, lights and serialized form, so existing gates do not
  start failing.
- Their results are new `metric` values in the existing results and details tables. They use the shared limits,
  rules, trend, persistence, alerts and gate, so no table changes. A per-grade result's `column` is
  `<grade column>=<grade>`, so the existing `column` selector of a limit rule targets a grade.
- Per-grade tests are one-sided against underestimation: Jeffreys (the Beta(D + 0.5, N - D + 0.5) CDF at the grade's
  mean probability) and the exact binomial tail. Both come from one standard-library regularized incomplete beta. A
  declared `correlation` adds the one-factor (Vasicek) large-portfolio test. scipy is not a dependency.
- The volume gate counts **expected** events. A grade under `min_expected_events` (default 5) is
  `insufficient_volume` whatever its observed count. `min_rows` does not apply to per-grade results, and inversions
  compare only the grades that pass the gate.
- Probabilities rescale to a shorter label as `1 - (1 - p)^(k/h)`. Each early-warning horizon names its label
  column, its periods and its lag (the lag defaults to its periods), and its results are their own series.
- `exclusion_psi` reads the stage cascade's first exclusion reason. Kept rows are a category of their own.
- `bad_rate` is a control chart centred on the median of the development per-period bad rates. Its spread is their
  standard deviation, from development only. The other control limits (decision 0016) still use development and
  out-of-time, centred on the mean.
- Each family reduces a frame to per-grade or per-horizon sums with built-in Spark functions or the row engine. The
  statistics run on the driver.

## Consequences

- A credit model gets governance-grade calibration and order checks from its declaration alone, on the same
  dashboards and alerts as the other metrics.
- Declaring `credit` adds default limits for the new metrics, so a `fail_on: red` gate can fail on them. Limits
  can be relaxed per metric or per grade.
- The adjusted binomial test is an approximation for large grades. Small grades are usually stopped by the
  expected-events gate before it matters.
- Performance estimation before labels mature stays out of scope. Early horizons are measured only once their
  own labels mature.
