# 0008. `bricksmith-monitor` succeeds `psi_spark`

- Status: accepted
- Date: 2026-10-11

## Context

`psi_spark` is a standalone repository for binning and population stability on Spark, stopped
mid-refactor. Its planned design persisted its own metadata tables, which would hide those
tables from bricksmith's dependency inference. Its scope also outgrew the name once
discrimination, calibration and alert rules joined.

## Decision

- `psi_spark` becomes `bricksmith-monitor` (`bricksmith.monitor`), imported into
  `packages/bricksmith-monitor` with its history. Author emails are rewritten to personal
  addresses during the import (`git filter-repo --mailmap`).
- The core is pure: DataFrames in, DataFrames out. Persistence is an optional layer for use
  outside bricksmith; inside a bricksmith project, results are tables written by tasks, so the
  dependency graph stays visible.
- It never depends on the bricksmith core. `bricksmith-mlops` uses it through an optional extra.
- Its functional tests run on Free Edition like every other distribution.
- The old repository is archived with a pointer to this one once phase 4 closes.
