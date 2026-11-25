---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*)
---

# /quenching-delivery status — read the delivery front

This command is the read-only view of a target repository's delivery surface. It reports the
applicability signal, provider-equivalent workflow artifacts, triggers, jobs, stages, setup and
runtime evidence, release reachability and findings. It never edits a workflow, selects a
provider or invokes a deployment.

The delivery finding map is in
[delivery-align/bands.md](.agents/skills/quenching-delivery/references/delivery-align/bands.md); the common
front floor is in [front-align/mold.md](.agents/quenching/references/front-align/mold.md).
Resolve `cq` per
[tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool. Set `TARGET_ROOT` to `$ARGUMENTS`, or `.` when omitted, and run:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" delivery status --json
```

Branch on the JSON and exit code: `0` is clean, `1` carries findings, and `2` is a refusal. On
exit `2`, preserve the refusal and the affected configuration exactly; do not guess a provider or
workflow tree. On exit `0` or `1`, use the returned payload as the complete read model and do not
invoke a write-capable command.

## Workflow

### 1. Read the status payload

Re-read the status payload using the `.agents/quenching/bin/cq delivery status --json` invocation above. Branch on its
exit code and preserve its applicability, refusal or findings.

**Done when:** the payload is captured, or the refusal is preserved with its reason.

### 2. Classify and report the front

Report one compact table:

| Band | Evidence |
| --- | --- |
| Applicability | target root, applicable/not-applicable state, signal, provider and artifacts |
| Workflow | path, provider, kind, parse state, triggers, jobs, stages and job provenance |
| Structural | `delivery-trigger-unreachable`, `delivery-stage-unreachable`, `delivery-checkout-missing`, `delivery-setup-missing`, `delivery-runtime-drift`, `delivery-release-unreachable` |
| Judgement | `delivery-provider-policy`, `delivery-environment-policy`, `delivery-publish-scope`, `delivery-permission-policy` |
| Counts | errors, warnings and overall `ok` state |
| Catalog controls | the `controls` block — each catalog control id, its requirement ids with `present`, `missing`, `not-measured`, `refused` or `retired`, and the evidence paths. `present` is candidate evidence, never conformance; the block never changes `ok` or the exit code |

Preserve every finding's code, band, severity, message and path. Distinguish no delivery signal
(`not-applicable`) from an applicable provider whose workflow shape was not measured. For an
actionable mechanical or structural finding, name `/quenching-delivery align`; for a judgement
finding, retain the evidence and name the target owner's closing decision from `delivery-align/bands.md`.

**Done when:** every returned field has an evidence row, every finding keeps its code and band,
and no missing state is presented as healthy.

### 3. Hand back control

State that this command wrote nothing and that it did not run a workflow, test suite, release or
deployment.

**Done when:** the report states its read-only boundary and observed exit code.

## Invariants

- Read only: no workflow, configuration, permission, environment or release edit.
- Findings retain the delivery route's stable code, band, severity and evidence.
- Provider and target policy are never inferred from a workflow's mere presence.
- A missing or unsupported workflow shape remains explicit and is never reported as conformant.
