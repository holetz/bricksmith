---
type: decision
title: "0023. The monitoring report is rendered from stored rows, governance views stay SQL"
description: "Why the per-model monitoring report is a self-contained HTML and Markdown file built from the stored tables, with axes derived from data, and why coverage and response measures are SQL views."
resource: "src/bricksmith/monitor/model/report.py, src/bricksmith/monitor/model/tables.py"
timestamp: "2026-11-24T23:30:00-03:00"
audience: both
authority: current
---

# 0023. The monitoring report is rendered from stored rows, governance views stay SQL

- Status: accepted
- Date: 2026-11-24
- Extends: [0017](0017-model-monitoring-alert-actions.md) and [0022](0022-credit-model-performance-checks.md)

## Context

Monitoring results live in tables. A reviewer needs one report per model, and governance needs coverage and response
measures across models. A report with a hard-coded period axis misleads when a period is missing. A chart without
the development reference misleads about what normal looked like.

## Decision

- The report is built from stored rows only (`results`, `runs`, `actions`), reading the latest finished run per model
  version and period, the rule the dashboard views use. It can render offline from an exported file or from the
  tables through the selected context. A model with no finished-run result is an error, never an empty report.
- The period axis is the sorted union of the periods found in the data. One section per metric family, one series
  per metric, column and segment. Each series carries the development and out-of-time values beside the periods.
- The HTML is one file: CSS tabs, inline SVG, no script and no external asset, every value escaped. The Markdown
  carries the same sections as tables. No plotting library is added.
- Open alerts and their human status come from the same replay the `alerts` verb runs, at an evaluation date.
- Governance measures are SQL views over the existing tables, so any BI tool can read them. No hosted dashboard and
  no table schema change. A model is declared when it is named at view generation or has a run of any status; no
  registry table is introduced. The latest closing is the greatest monitoring period of any model.

## Consequences

- The report works without Spark, and its output can be attached to a review.
- A model that never ran counts for coverage only when listed with `--declared`.
- Charts are simple line charts; richer interaction needs a BI tool over the views.
- `share_monitored` is cast to double in SQL: Spark returns a decimal for `sum(x) * 1.0`.
