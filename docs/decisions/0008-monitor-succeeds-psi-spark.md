# 0008. bricksmith monitoring succeeds `psi_spark`

- Status: accepted; packaging, dependency boundaries and persistence amended by
  [0009](0009-one-library-shared-task-scopes.md) on 2026-10-13
- Source adoption and archive plan superseded by
  [0010](0010-behavior-derived-independent-development.md) on 2026-10-13
- Date: 2026-10-11

## Context

`psi_spark` is a standalone repository for binning and population stability on Spark, stopped
mid-refactor. Its planned design persisted its own metadata tables, which would hide those
tables from bricksmith's dependency inference. Its scope also outgrew the name once
discrimination, calibration and alert rules joined.

## Decision

- Monitoring lives in `bricksmith.monitor` and is implemented from independent behavioral and
  statistical contracts. No predecessor code or Git history is imported under this plan.
- The core is pure: DataFrames in, DataFrames out. Persistence is an optional layer for use
  outside bricksmith; inside a bricksmith project, results are tables written by tasks, so the
  dependency graph stays visible.
- It uses shared framework and MLOps abstractions where useful; external integrations may have
  optional extras within the same `bricksmith` distribution.
- Its functional tests run primarily on Free Edition where supported, with explicit supplementary
  corporate proof under decision 0004 when needed.
- Development and release do not require migrating or archiving a predecessor repository.
