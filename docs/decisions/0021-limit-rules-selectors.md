---
type: decision
title: "0021. Limit rules pick limits by selector and specificity and mark segments informational"
description: "Why monitors read reviewable limit rules with selectors, validity dates and informational segments, and report the rule on every result row."
resource: "src/bricksmith/monitor/**"
timestamp: "2026-11-24T16:20:00-03:00"
audience: both
authority: current
---

# 0021. Limit rules pick limits by selector and specificity and mark segments informational

- Status: accepted
- Date: 2026-11-24
- Extends: [0016](0016-model-monitoring-limit-kinds.md)

## Context

One set of limits per metric is too coarse. A segment can be informational only, a column can be naturally volatile
and one dataset can need stricter bounds. Overrides spread through code are hard to audit, and a result row did not
say which limits had judged it.

## Decision

- `monitor/rules.py` holds one resolver for data stability and model monitoring. A rule has an `id`, a `version`,
  optional selectors (`dataset`, `column`, `segment`, `metric`), optional inclusive validity dates and one body:
  `limits` (or `null` to disable), `informational_segments` or `controlled_segments`.
- The most specific matching rule wins: `dataset` scores 4, `segment` 2, `column` 1; ties go to the highest `version`,
  then the smallest `id`. Limits rules and informational rules are resolved independently. Selectors are exact, not
  patterns, so the additive weights stay unambiguous.
- Rules are reviewable data: an inline list or a TOML or JSON project file read when the declaration is parsed. Git
  carries the history; a table reader can be added without changing the resolver.
- An informational segment is computed and stored with the suppressed status `informational` (rank -1) and empty
  limits, so trend, persistence, alerts and the gate skip it like any suppressed row. The whole population is never
  informational.
- Results carry `rule_id` and `rule_version` as additive columns migrated through the `evolution` write option.
  Declarations without rules produce the same rows and fingerprints as before; `rules` joins the stability judgment
  fingerprint only when present.
- Stability rules use absolute limits (decision 0016). Model rules may use every kind, and replay recolors stored
  rows with the current rules.

## Consequences

- Audits read the rule from the row. A rule edit rewrites past lights on replay, as any limits change does.
- Exact selectors mean a rule per segment value; a pattern syntax is a later decision if projects need it.
