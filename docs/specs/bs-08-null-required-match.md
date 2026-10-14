# Fail required cardinality matches for null data keys

- Date: 2026-10-14
- Status: paused at human request; no implementation started
- CQ: not created; local proposed/stopped candidate only
- Owner: [Epic 0001](../epics/0001-bricksmith-library.md)

## Problem

Independently reproduced on 2026-10-14: require_match=True with one data row {id: None} and other row {id: 1} passes, reporting data_invalid_groups=1 and unmatched_rows=0. quality/checks_rows.py:51 and checks_spark.py:70 remove null keys before counting unmatched rows; checks.py:72 promises every data key exists in the other relation. High severity: a required match silently accepts an unjoinable input.

Reproduce with `.venv/bin/python` using this stdin (synthetic only):

```python
from bricksmith.quality.checks import Check, run_check
r = run_check(Check.cardinality('id', expect='many_to_one', require_match=True), [{'id': None}], other=[{'id': 1}])
print(r.to_dict())
# Observed: status passed; data_invalid_groups 1; unmatched_rows 0
```

## Proposal

Count null-bearing data keys as required-match violations using one explicit policy across rows/pandas/Spark; preserve cardinality-only null behavior when require_match=False.

## Out of Scope

Changing join execution, remote authentication, grants, scripts or unrelated quality semantics.

## Impact

src/bricksmith/quality/checks_rows.py; src/bricksmith/quality/checks_spark.py; tests/unit/quality/; docs/guide/quality.md if clarification is needed.

## Validation

Single/composite null keys, mixed valid/null inputs, empty other relation, nullable other keys, valid matching rows and require_match=False. Compare rows/pandas/Spark semantics via existing adapter tests; unavailable adapters must be recorded as not run. Run gate.

## Design

Stop override: the dated delegation below is historical and cannot resume this paused run.

SQL-style null-bearing keys cannot satisfy a required relation match, including null on both sides. Report unmatched_rows and failed status consistently; samples remain bounded and no raw rows enter reports. Inspect existing invalid-group reporting and avoid double-counting valid unmatched keys.

Autonomy delegated by the human invocation on 2026-10-14: ordinary product implementation, verification, commit, push and PR are authorized after verifier PASS; merge additionally requires green CI. Executable grants/surfaces, generated CI/hooks/scripts and plugin runtime expansion are never forced and belong to the human-decision epic. No publishing or visibility changes.

## Alternatives Considered

Null-safe equality would alter the established SQL join policy; use explicit required-match failure instead.

## Open Decisions

- none — ordinary product design is delegated; any executable-surface decision goes to the human epic.

## Risks

Composite null keys and samples can diverge between adapters; verify consistent counts and retained cardinality-only behavior.

## Handoff

Store discrepancy: CQ 6.4.0 resolves the legacy GitHub default because .agents/quenching.json is absent; the human expects the quenching branch store. Existing CQ issues #1-#4 are retained, and local specs are durable. No migration, configuration change, issue creation or protocol/store-design choice is authorized by this paused handoff. Resolve the store decision with the human before any resumed tracker writes; do not modify the quenching branch automatically.

User requested a stop on 2026-10-14. This run is paused; do not start development, remote diagnosis, push, PR or merge until an explicit resume. Branch: epic-0001; committed recovery d72b46e; persisted-artifact hardening 01f828e. No merge into main or publication occurred. All tasks below remain unimplemented.

Queued next-round finding. Execute before nonfinite-freshness spec because both share tests/unit/quality and guide; no parallel file ownership.

Local contract: docs/specs/bs-08-null-required-match.md

## Tasks

### 1. Implementation and acceptance

- [ ] 1.1 Add regression and align required-match null policy across adapters
  files: src/bricksmith/quality/checks_rows.py src/bricksmith/quality/checks_spark.py tests/unit/quality/
  verify: uv run pytest tests/unit/quality -q
- [ ] 1.2 Confirm compatibility, bounded samples and documentation
  files: tests/unit/quality/ docs/guide/quality.md
  verify: uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest -q
