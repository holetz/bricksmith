---
type: decision
title: "0017. Alert actions are an append-only table folded into alerts at an evaluation date"
description: "Why human actions on model monitoring alerts live in their own table and are applied to rebuilt alerts, with time-boxed risk acceptance."
resource: "src/bricksmith/monitor/model/**"
timestamp: "2026-11-23T20:00:00-03:00"
audience: both
authority: current
---

# 0017. Alert actions are an append-only table folded into alerts at an evaluation date

- Status: accepted
- Date: 2026-11-23
- Extends: [0016](0016-model-monitoring-limit-kinds.md)

## Context

Model monitoring alerts open and close from replayed results, but teams also need to record what they did: an
alert under analysis, closed with a reason, or a risk accepted until a date. Without a record the same alert is
discussed again every run, and governance cannot measure how fast alerts are answered. Alerts are rebuilt on every
run, so an edit in place would be overwritten and would leave no history.

## Decision

- People append to a sixth table, `<prefix>actions`: `action_id`, `model`, `alert_id`, `action` (`analysis`,
  `closed`, `risk_accepted`), `reason`, `author`, `valid_until`, `created_at`. It is keyed by the action, not by a
  run, because an action outlives the run that raised the alert. Runs create it empty and never write rows to it.
  The id hashes the content, so writing a decision twice is one row.
- A run or a replay folds the latest action of each alert into its state at an evaluation date (the run date, or
  `--as-of`): `closed` gives state `closed`, an active `risk_accepted` gives `accepted` through `valid_until`
  inclusive, after which the alert returns to its automatic state and the acceptance shows as expired; `analysis`
  leaves it open. Alert states are `open`, `closed` and `accepted`.
- An accepted or closed span stops counting for the `critical` and `alert` gates. An accepted light alert is removed
  from the `red` and `yellow` counts; closing a light alert is bookkeeping and does not hide its result.
- Governance reads views over trusted rows: acceptances, coverage and timeliness, per-alert response times and
  their means. The `act` verb validates a row and prints the `INSERT` statement; nothing here needs a service.

## Consequences

- Every decision is auditable: who, why, until when, and what superseded it.
- An acceptance cannot hide a problem forever: it needs an end date, expired acceptances are listed, and the run
  message counts accepted alerts.
- The view of an alert's state is as of its run; an acceptance that expires between runs shows as accepted until the
  next run, while the acceptances view evaluates the date when queried.
- Data stability alerts keep their own incremental lifecycle; sharing the fold rule is a later decision.
