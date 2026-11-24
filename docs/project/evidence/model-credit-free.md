---
type: project
title: "Credit-model performance checks evidence: free environment"
description: "Remote test evidence for the credit-model performance checks of model monitoring (spec 82) on the free Databricks environment."
resource: "tests/remote/test_model_credit.py"
timestamp: "2026-11-24T22:30:00-03:00"
audience: both
authority: current
---

# Credit-model performance checks evidence: free environment

- Date: 2026-11-24
- Specification: spec 82 (epic 85), credit-model performance checks
- Environment: `free` (named profile, serverless compute). The workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`).
- Command: `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_model_credit.py -q`
  (the free context selected explicitly)
- Result: 1 passed (about 6 minutes)
- Data: synthetic only. There are four grades with fixed probabilities. Event rates move by month, one grade has
  too few expected events, a 3-period early-warning label is drawn at the rescaled probability, and the
  first-exclusion-reason mix shifts in the last month.

## Scenario states

| Scenario | State | Note |
|---|---|---|
| Spark results equal the pure engine for every result row (model, segment, column, metric, period) | passed | relative tolerance 1e-9 on floats |
| Spark details equal the pure engine (per-grade calibration table, exclusion shares) | passed | |
| Every credit metric is measured on Spark, the adjusted binomial test included | passed | `Frame.sums` uses built-in functions only (no Python UDF) |
| Lights include green, red and `insufficient_volume` (the expected-events gate) | passed | |
| The run is `success` | passed | no family raised |

## Cleanup

The test wrote one table in a unique test-owned schema. Teardown verified that nothing remained, and no leftovers
were reported.
