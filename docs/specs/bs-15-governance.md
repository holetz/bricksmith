# BS-15. Governance declarations and verifiable state

Implements BS-15.1 to BS-15.4 of [Epic 0001](../epics/0001-bricksmith-library.md). Stdlib only;
Spark is used only by the adapter, through an injectable SQL runner.

## Decisions closed

- Config is `governance.toml` (`version = 1`): `categories` (project-defined; `unclassified` is
  implicit and the default), `tag_keys`, `retention` (name to days), `principals` (logical name to
  per-environment name, optionally `{name, expires}`), `[[resource]]`, `[[policy]]`, `[severity]`.
- Only resources with `managed = true` get controls. Controls: tags, grants, comment, table
  properties. Classification, owner and retention are written as tags; the owner tag holds the
  logical name, never a real principal.
- Policy: declarative `[[policy]]` rules (classification to tags/grants) and Python hooks
  `Callable[[ResourceDecl], PolicyOutput | None]`. Conflicting values for one control fail.
- Reconcile is additive: it sets missing/different controls and never revokes, unsets or drops.
  Unexpected live tags (except `system.*`), grants, masks and row filters are reported as external.
- Four states per control: `desired` (declared, not live), `applied` (statement succeeded, not
  visible live), `verified` (live matches), `unavailable` (cannot apply or read, with a reason).
- Masks and row filters can be declared but are always `unavailable` (no mutation adapter); live
  ones are detected read-only. Classification is `metadata-only`; `effective_masking` is always false.
- Findings reuse the registry `Finding`/`CheckReport` with ids `<rule>:<subject>`. Rules:
  `governance.unclassified`, `governance.drift.{missing,different,unverified}`,
  `governance.unavailable`, `governance.external.{tag,grant,policy}`, `governance.metadata_only`.
  Defaults: drift error, unclassified/unavailable/external warning, metadata_only off.

## Public contract

`load_config`/`parse_config` -> `GovernanceConfig`; `desired_state` -> `DesiredState`;
`plan_governance(desired, live)` -> `GovernancePlan` (`to_dict/from_dict`); `read_live`,
`apply_plan`, `spark_runner`; `audit_governance` -> `GovernanceAudit`; `governance_report` (dict
for catalog docs); `governance_findings` -> `CheckReport`.
CLI: `bricksmith governance plan|audit [--config --var --hook --live --today]`,
`apply [--confirm]` (without it only the plan is shown). Exit 4 when checks fail or the platform
refuses a statement.

## Deviations

None from the guardrails. Registry column sensitivity is not synced to column tags in this release.
