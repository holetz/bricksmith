---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*)
---

# /quenching-opensource status — read the open source front

**Input**: `$ARGUMENTS` (an optional target repository root; omit to use the current repository).

This command is the read-only view of the open source front. The common front contract is in
[front-align/mold.md](.agents/quenching/references/front-align/mold.md), the catalog
contract is in
[opensource-controls/catalog.md](.agents/skills/quenching-opensource/references/opensource-controls/catalog.md),
and the finding-to-owner map is in
[opensource-align/bands.md](.agents/skills/quenching-opensource/references/opensource-align/bands.md).
It aggregates what each owning front already answers; it never probes another owner's control and
never writes an artifact.

Resolve `cq` per
[tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool. Set `TARGET_ROOT` to the supplied argument, or to `.` when omitted, and run:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" opensource status --json
```

Branch on the JSON and exit code: `0` is a clean report, `1` carries findings, and `2` is a
refusal. On exit `2`, preserve the refusal exactly; never guess a root, license, maintainer or
owner. On `0` or `1`, use the returned payload as the complete read model and do not invoke a
write-capable command.

## Workflow

### 1. Read the status payload

Re-read the status payload using the `.agents/quenching/bin/cq opensource status --json` invocation above. Branch on its
exit code and preserve its baseline, owner blocks, findings and advisories.

**Done when:** the payload is captured, or the refusal is preserved with its reason.

### 2. Classify and report the front

Report one compact table:

| Band | Evidence |
| --- | --- |
| Baseline | `baseline.version`, the attained `level`, and per level its `met`/`unmet`/`unknown` state with the `blocking` requirement ids |
| Owners | one row per `owners[]` block — owner, `summary`, and the `command` that closes its residue |
| Opensource controls | each control of the `opensource` block, its requirement ids with `present`, `missing`, `not-measured`, `refused` or `retired`, and the evidence paths |
| Artifacts | each `artifacts[]` row — artifact, target path, present or absent |
| Findings | every `os-*` finding with its control, requirements and severity |
| Advisories | every optional or artifact-only `os-*` code; advisories never change `ok` or the exit code |

`present` is candidate evidence, never conformance: keep `humanRequired` as returned. A level that
is `unknown` stays unknown — a requirement only a provider answers is `not-measured`, never met.
A requirement of another owner appears in its owner row with that owner's command, never as an
`os-*` finding. Preserve every code, path and message returned by the route.

**Done when:** every returned field has an evidence row, every finding keeps its code and
disposition, and an unmeasured requirement remains unmeasured.

### 3. Hand back control

State that this command wrote nothing. For an `os-*` finding or advisory, name
`/quenching-opensource align` as the closing command. For another owner's residue, name
the `command` its owner block returned; status never runs it.

**Done when:** the report states its read-only boundary and observed exit code.

## Invariants

- Read only: no artifact written, no license, maintainer, contact or funding chosen.
- The aggregate never re-measures another owner's control; it reports that owner's own answer.
- The baseline level is the route's; it is never rounded up past an `unknown` level.
- Findings retain their stable `os-*` code, severity, evidence and disposition band.
