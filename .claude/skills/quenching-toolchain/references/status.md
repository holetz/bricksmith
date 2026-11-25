---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*)
---

# /quenching-toolchain status — read the toolchain front

**Input**: `$ARGUMENTS` (an optional target repository root; omit to use the current repository).

This command is the read-only view of the toolchain front. The common front contract is in
[front-align/mold.md](.agents/quenching/references/front-align/mold.md), and the
finding-to-owner map is in
[toolchain-align/bands.md](.agents/skills/quenching-toolchain/references/toolchain-align/bands.md).
It reports capability and evidence without repairing a manifest, lock, pin, tool configuration or
target policy.

Resolve `cq` per
[tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool. Set `TARGET_ROOT` to the supplied argument, or to `.` when omitted, and run:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" toolchain status --json
```

Branch on the JSON and exit code: `0` is a clean report, `1` carries findings, and `2` is a
refusal. On exit `2`, preserve the refusal and every missing or invalid declaration exactly; never
guess a root, ecosystem, lock requirement, language version or tool owner. On `0` or `1`, use the
returned payload as the complete read model and do not invoke a write-capable command.

## Workflow

### 1. Read the status payload

Re-read the status payload using the `.agents/quenching/bin/cq toolchain status --json` invocation above. Branch on its
exit code and preserve its applicability, refusal or findings.

**Done when:** the payload is captured, or the refusal is preserved with its reason.

### 2. Classify and report the front

Report one compact table:

| Band | Evidence |
| --- | --- |
| Applicability | target root, `applicable`/`not-applicable`, signal and recognized artifacts |
| Manifests | path, ecosystem, parse state, runtime/build declarations and dependency names |
| Locks | path, parse state, lock format/version and package/dependency count |
| Language pins | path, parse state and declared value; absent remains absent |
| Tool configuration | manifest path, configuration kind and declared keys |
| Mechanical | `tc-lock-stale`, `tc-config-duplicate`, `tc-generated-stale` counts and evidence |
| Structural | `tc-runtime-drift`, `tc-tool-unpinned`, `tc-build-backend-missing`, `tc-key-unarbitered` counts and evidence |
| Judgement | no initial `tc-*` code; state the target-owner boundary when a policy is unmeasured |
| Catalog controls | the `controls` block — each catalog control id, its requirement ids with `present`, `missing`, `not-measured`, `refused` or `retired`, and the evidence paths. `present` is candidate evidence, never conformance; the block never changes `ok` or the exit code |

Keep severity and disposition separate. A clean applicable front is conformant; a missing
recognized artifact is not a defect when it is outside the target's declared ecosystem. A target
with no recognized artifact is `not-applicable`, not silently clean. Preserve every code, path and
message returned by the route.

**Done when:** every returned field has an evidence row, every finding keeps its code and
disposition, and absent policy remains unmeasured.

### 3. Hand back control

State that this command wrote nothing. For mechanical or bounded structural residue, name
`/quenching-toolchain align` as the closing command. For a target-owned build decision, retain the
evidence and name the owner action from `toolchain-align/bands.md`; status never runs it.

**Done when:** the report states its read-only boundary and observed exit code.

## Invariants

- Read only: no lock regeneration, dependency upgrade, pin selection, manifest edit or configuration migration.
- Applicability, absent artifacts and refusal remain explicit states.
- Findings retain their stable `tc-*` code, severity, evidence and disposition band.
- The target's build policy is never inferred from the presence or absence of one artifact.
