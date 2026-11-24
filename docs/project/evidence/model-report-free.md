---
type: project
title: "Model monitoring report evidence: free environment"
description: "Remote test evidence for the model monitoring report and governance views (spec 83) on the free Databricks environment."
resource: "tests/remote/test_model_report.py"
timestamp: "2026-11-24T23:30:00-03:00"
audience: both
authority: current
---

# Model monitoring report evidence: free environment

- Date: 2026-11-24
- Specification: spec 83 (epic 85), monitoring report and governance views
- Environment: `free` (named profile, serverless compute). The workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`).
- Command: `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_model_report.py -q`
  (the free context selected explicitly)
- Result: 1 passed (about 1 minute)
- Data: synthetic only. Two models with six monthly closings and a score shift late in the series, one model one
  closing behind, one declared model with no run, one risk acceptance.

## Scenario states

| Scenario | State | Note |
|---|---|---|
| Coverage marks the lagging and the never-run models as not monitored for the latest closing | passed | declared = names given + models with a run |
| Coverage summary counts declared, monitored and the share | passed | the share is cast to double; Spark returns a decimal for `sum(x) * 1.0`, found by this run |
| Open alerts by severity, recurrence and the acceptance summary answer on Spark SQL | passed | |
| The report built from the Spark tables equals the one built from the same rows offline (HTML and Markdown) | passed | the accepted alert shows its state |

## Cleanup

The test wrote its tables and views in a unique test-owned schema. Teardown verified that nothing remained, and no
leftovers were reported.
