# Reject nonfinite freshness thresholds before evaluation

- Date: 2026-10-14
- Status: paused at human request; no implementation started
- CQ: not created; local proposed/stopped candidate only
- Owner: [Epic 0001](../epics/0001-bricksmith-library.md)

## Problem

Independently reproduced on 2026-10-14: checks.py:52 rejects negative thresholds but accepts NaN. A timestamp from 2000-01-01 against 2026-01-01 with max_age_seconds=float("nan") passes at age_seconds=820540800.0. NaN comparisons fail open, silently disabling freshness enforcement. Medium severity.

Reproduce with `.venv/bin/python` using this stdin (synthetic only):

```python
import datetime as dt
from bricksmith.quality.checks import Check, run_check
r = run_check(Check.freshness('t', max_age_seconds=float('nan')), [{'t': dt.datetime(2000, 1, 1, tzinfo=dt.UTC)}], now=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
print(r.to_dict())
# Observed: status passed; age_seconds 820540800.0
```

## Proposal

Validate finite nonnegative numeric freshness thresholds at construction, including from_dict, so invalid configuration fails usefully before evaluating any adapter.

## Out of Scope

New threshold defaults, remote execution, dependencies and unrelated metric/statistical checks.

## Impact

src/bricksmith/quality/checks.py; tests/unit/quality/; docs/guide/quality.md if numeric validation needs clarification.

## Validation

Direct Check.freshness and Check.from_dict reject NaN, positive/negative infinity and invalid numeric input with stable QualityError; finite zero/positive thresholds retain valid boundary behavior across rows/pandas/Spark. Run gate.

## Design

Stop override: the dated delegation below is historical and cannot resume this paused run.

Use one constructor validation path and standard-library finite checks; preserve existing error type/code. Decide booleans explicitly as invalid numeric configuration and cover them. All adapters receive a validated Check; no adapter-specific NaN exception.

Autonomy delegated by the human invocation on 2026-10-14: ordinary product implementation, verification, commit, push and PR are authorized after verifier PASS; merge additionally requires green CI. Executable grants/surfaces, generated CI/hooks/scripts and plugin runtime expansion are never forced and belong to the human-decision epic. No publishing or visibility changes.

## Alternatives Considered

Treating NaN as pass/unavailable would still accept an invalid policy; reject configuration centrally.

## Open Decisions

- none — ordinary product design is delegated; any executable-surface decision goes to the human epic.

## Risks

from_dict conversion must not bypass the finite check or raise an inconsistent built-in exception. Do not change serialization of valid checks.

## Handoff

Store discrepancy: CQ 6.4.0 resolves the legacy GitHub default because .agents/quenching.json is absent; the human expects the quenching branch store. Existing CQ issues #1-#4 are retained, and local specs are durable. No migration, configuration change, issue creation or protocol/store-design choice is authorized by this paused handoff. Resolve the store decision with the human before any resumed tracker writes; do not modify the quenching branch automatically.

User requested a stop on 2026-10-14. This run is paused; do not start development, remote diagnosis, push, PR or merge until an explicit resume. Branch: epic-0001; committed recovery d72b46e; persisted-artifact hardening 01f828e. No merge into main or publication occurred. All tasks below remain unimplemented.

Queued next-round finding; execute after bs-08-null-required-match to serialize shared quality tests/docs.

Local contract: docs/specs/bs-08-nonfinite-freshness.md

## Tasks

### 1. Implementation and acceptance

- [ ] 1.1 Reject nonfinite thresholds through direct and dictionary construction
  files: src/bricksmith/quality/checks.py tests/unit/quality/
  verify: uv run pytest tests/unit/quality -q
- [ ] 1.2 Verify finite boundaries and preserve adapter behavior
  files: tests/unit/quality/ docs/guide/quality.md
  verify: uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest -q
