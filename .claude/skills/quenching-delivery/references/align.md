---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*), AskUserQuestion, Write, Edit
---

# /quenching-delivery align — converge the delivery front

**Input**: `$ARGUMENTS` (an optional target repository root; omit to use the current repository).

This command owns the delivery front's bounded convergence pass. It probes the workflow tree,
classifies static pipeline findings, applies only authorized mechanical or structural shape
repairs, and leaves provider, environment, promotion, publication and permission choices with the
target owner. The finding bands live in
[delivery-align/bands.md](.agents/skills/quenching-delivery/references/delivery-align/bands.md); the common
front floor lives in [front-align/mold.md](.agents/quenching/references/front-align/mold.md).

Set `TARGET_ROOT` to the supplied argument, or to `.` when omitted, and pass that same root to
every `.agents/quenching/bin/cq delivery` call. Resolve `cq` per
[tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool. Branch on JSON and exit code: `0` is conformant, `1` carries findings, and
`2` is a refusal.

## Workflow

### 1. Probe before inventory

Run the verifier before interpreting the workflow tree:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" delivery doctor --json
```

On exit `0`, report the applicability state and "delivery conformant, nothing to align"; do not
read a second inventory or ask for authorization. On exit `2`, preserve the refusal exactly and
stop. On exit `1`, use the returned `inventory` and `findings` as the complete evidence for the
plan; do not rediscover workflow files.

**Done when:** the clean or refused result has stopped the pass, or the non-clean payload is in
hand as the sole source for the plan.

### 2. Classify one bounded plan

Group every finding using `delivery-align/bands.md`:

| Band | Finding codes | Disposition |
| --- | --- | --- |
| Mechanical | `delivery-generated-stale` | regenerate the owned deterministic artifact and rerun the doctor |
| Structural | `delivery-trigger-unreachable`, `delivery-stage-unreachable`, `delivery-checkout-missing`, `delivery-setup-missing`, `delivery-runtime-drift`, `delivery-release-unreachable` | apply one bounded pipeline-shape repair and rerun the doctor |
| Judgement | `delivery-provider-policy`, `delivery-environment-policy`, `delivery-publish-scope`, `delivery-permission-policy` | report evidence and the target-owner decision; never choose policy automatically |

Keep each finding's code, band, severity, affected workflow and closing command in the plan.
Missing delivery is not drift; an applicable but unmeasured provider is not a green result.

**Done when:** every finding has one band, evidence, affected workflow and permitted disposition.

### 3. Ask once and apply only the first two bands

Present the full plan, target files and closing verifier, then ask once for authorization of the
routine batch. Under that authorization, apply only deterministic or bounded structural edits
whose answer is already declared by the target. Never select a provider, environment, promotion
branch, publish scope, permission set or deployment command. Never run a release or deployment.

After authorized edits, run the same probe again:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" delivery doctor --json
```

If structural residue remains, report it rather than widening the plan. Judgement findings remain
report-only even when a routine batch was approved.

**Done when:** the authorized bounded changes are applied, the same verifier has run again, and
judgement findings remain untouched.

### 4. Report

Report the target root, applicability, workflow count, applied repairs, closing doctor result and
every residual finding with its evidence and the exact owner action from `delivery/bands.md`.
State explicitly when the front is not applicable, refused, not measured or already conformant.
This report is the run record; do not append a log file to the target.

**Done when:** the report distinguishes applicability, applied repairs, verifier state and every
residual owner action.

## Invariants

- Applicability is probed before inventory, classification and confirmation.
- `.agents/quenching/bin/cq delivery status` remains read-only; align never invents a workflow tree.
- Mechanical and bounded structural findings may be repaired only under one authorization.
- Provider, environment, promotion, publication, permission and deployment choices remain human-owned.
- The closing verifier is the same `.agents/quenching/bin/cq delivery doctor --json` program as the probe.
