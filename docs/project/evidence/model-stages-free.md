---
type: project
title: "Model development stages evidence: free environment"
description: "Remote test evidence for model development stages (spec 79) on the free Databricks environment."
resource: "tests/remote/**"
timestamp: "2026-11-23T09:27:03-03:00"
audience: both
authority: current
---

# Model development stages evidence: free environment

- Date: 2026-11-23
- Specification: spec 79 (epic 85), model development stages
- Environment: `free` (named profile, serverless compute). The workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`).
- Command: `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_model_stages.py -q`
- Result: 2 passed on the final code (about 2.5 minutes)
- Versions: databricks-connect 17.3 line (pyspark 4.0), Python 3.12, serverless Delta (see [BS-00](bs-00-free.md))

## Scenario states

| Scenario | State | Note |
|---|---|---|
| Spark `apply_cascade` equals the row engine (every rule met, first rule, excluded flag) | passed | built-in `filter` over an array of `when` hits; no Python UDF |
| Spark `composition` equals the row engine per closing and rule | passed | |
| Spark `select_sample` equals the row engine for train, test, out-of-time and through-the-door windows | passed | the seeded split of shared train/test closings matches row for row |
| Spark `build_abt` for training equals the row engine (excluded rows filtered, labels attached, stratified) | passed | train, test and out-of-time samples; boolean labels; one lookup with a default for a missing entity |
| Spark `build_abt` for scoring equals the row engine (excluded rows kept and flagged) | passed | out-of-time and through-the-door samples |
| Spark `prediction_frame` equals `prediction_rows` column for column | passed | grades from declared bands |
| An immature label, a repeated label key and an unaligned closing are refused | passed | |
| A task builds scoring ABTs, writes the prediction contract and declares model monitoring | passed | the runner's monitoring run succeeded; latest period 2024-07-31 |
| Monitoring on the stored prediction table equals monitoring on the row-engine predictions | passed | same outcome; `composition` by sample runs on the stored table |

The first task run failed once for a reason fixed before the final run: the synthetic task declared no write
strategy. The task now declares `write: overwrite`.

## Cleanup

The task test used a unique test-owned schema. Teardown verified that none remained, and no leftovers were
reported. The parity test built DataFrames in memory and wrote no table. Nothing else was created in the workspace.
