---
type: project
title: "Model evaluation evidence: free environment"
description: "Remote test evidence for model evaluation and technical model documentation (spec 80) on the free Databricks environment."
resource: "tests/remote/**"
timestamp: "2026-11-24T12:00:00-03:00"
audience: both
authority: current
---

# Model evaluation evidence: free environment

- Date: 2026-11-24
- Specification: spec 80 (epic 85), model evaluation and technical model documentation
- Environment: `free` (named profile, serverless compute). The workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`).
- Command: `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_model_evaluation.py -q`
- Result: 2 passed on the final code (about 9.5 minutes)
- Versions: databricks-connect 17.3 line, MLflow 3.17, scikit-learn 1.9, Python 3.12 (see [BS-00](bs-00-free.md))

## Scenario states

| Scenario | State | Note |
|---|---|---|
| Spark `evaluate` equals the row engine: results, score and grade distributions, grade tables | passed | train, out-of-time and through-the-door samples, whole sample and per closing; built-in functions only |
| CSI of a shifted feature is larger on the shifted sample | passed | |
| The document's artifacts are logged to a real tracking run and collected back unchanged | passed | development, composition, selection, evaluation, specification and narrative |
| The Markdown and HTML documents are logged to the run and read back byte for byte | passed | each file is downloaded on its own |
| Regenerating from the run with the same `generated_at` gives the same bytes | passed | |
| The registered version is tagged with the document location | Unsupported | the free workspace cannot register a version from a run: its artifact listing is empty (spec 88). Proven by unit tests over a fake store |

## Related fixes

A supplementary validation run on Unity Catalog found three publication defects. They were fixed test first,
before this proof:

- a nested artifact may download by its bare file name;
- a missing artifact may be reported only as a failed download;
- tag keys may not contain `.`.

That run also proved the registered-version tag, which the free tier cannot prove.

## Cleanup

The document test used a unique test-owned schema and an experiment named after it, and registered no model.
Teardown deleted the experiment and verified that the schema left nothing behind; no leftovers were reported. The
parity test built DataFrames in memory and wrote no table. Nothing else was created in the workspace.
