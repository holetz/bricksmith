---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*), AskUserQuestion, Write, Edit
---

# /quenching-toolchain align — converge the toolchain front

**Input**: `$ARGUMENTS` (an optional target repository root; omit to use the current repository).

This command owns the toolchain front's convergence pass. It reads manifests, locks, language pins
and tool configuration, applies only deterministic or contract-bounded repairs, and leaves build
policy with the target owner. The common front floor is in
[front-align/mold.md](.agents/quenching/references/front-align/mold.md), and the
toolchain finding bands and closure boundary are in
[toolchain-align/bands.md](.agents/skills/quenching-toolchain/references/toolchain-align/bands.md).

Set `TARGET_ROOT` to the supplied argument, or to `.` when the argument is omitted. Resolve `cq`
per [tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool, pass the same root to every call, and branch on the JSON and exit code:
`0` is a clean answer, `1` carries findings, and `2` is a refusal.

## Workflow

### 1. Probe applicability before interpreting artifacts

Run the route before reading or editing a target:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" toolchain doctor --json
```

The probe establishes applicability from recognized toolchain artifacts. `not-applicable` is an
explicit capability state, not drift; report it and stop. An applicable result with no findings is
conformant; report it and stop. On exit `2`, report the refusal exactly and do not invent a
manifest, lock, language version, tool pin or configuration namespace.

**Done when:** applicability and the first finding payload are fixed, or a clean/not-applicable
result has stopped the pass without an inventory read or a write.

### 2. Read the one inventory and its contracts

Only after exit `1`, use the `inventory`, `manifests`, `locks`, `languagePins` and
`toolConfigurations` rows already returned by the doctor payload. Do not walk the target again or
launch a package manager. Read the front contract and the bands reference before classifying a
row; `.agents/quenching.json` remains configuration, never a toolchain artifact.

**Done when:** the applicable artifacts, parsed declarations, configuration blocks and findings are
in hand from one read model.

### 3. Classify every finding by disposition

Keep severity separate from who may close the finding:

| Band | Finding codes | Disposition |
| --- | --- | --- |
| Mechanical | `tc-lock-stale`, `tc-config-duplicate`, `tc-generated-stale` | regenerate or normalize from the deterministic source, then re-run the doctor |
| Structural | `tc-runtime-drift`, `tc-tool-unpinned`, `tc-build-backend-missing`, `tc-key-unarbitered` | apply one bounded contract repair only when the target declaration identifies the answer, then re-run the doctor |
| Judgement | none in the initial vocabulary | report the evidence and the target owner's closing action; never choose a version, tool, backend, lock policy or owner |

Never turn an absent policy into a finding by guessing. A lock that the ecosystem does not require,
a language version with no chosen source, and a tool with no target-owned pin remain decisions for
the owner of the build.

**Done when:** each finding has its code, band, evidence, affected artifact and a permitted closing
shape.

### 4. Present one plan and ask once

Show the complete plan: every mechanical regeneration, every structural edit, every residual
judgement hand-off, the exact target files and the closing `toolchain doctor` command. Ask once for
authorization of the routine batch. A version choice, tool choice, backend choice, lock adoption or
ownership arbitration remains an individual target-owner decision and is not absorbed by the batch
authorization.

If authorization is rejected, write nothing and report the findings unchanged.

**Done when:** one complete plan has been presented and either authorized or rejected without a
target edit.

### 5. Apply only authorized mechanical and structural closures

Under the authorization, edit only the affected manifest, lock, pin or tool-configuration block
named by the evidence. Preserve authored text outside generated regions. Do not run a package
manager, upgrade a dependency, select a language version, add a pin whose value is unknown, or
choose an arbiter from file order. Those are target-owner decisions unless the target contract has
already made them deterministic.

Re-run the same closing verifier:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" toolchain doctor --json
```

If findings remain, distinguish unresolved judgement residue from a failed mechanical or structural
repair. Do not repeat an empty pass or broaden the target surface.

**Done when:** authorized closures are applied, no target policy was selected, and the closing
verifier has been run.

### 6. Report the front

Report the target root, applicability state and signal, every manifest/lock/pin/tool-configuration
row, applied changes, the closing verifier result and all residual findings with their evidence and
owner action. State explicitly when the front was not applicable, already conformant, refused
configuration, or remains non-conformant. This command's report is the run record; do not append a
log file to the target.

**Done when:** the report distinguishes applicability, evidence, applied closures, verifier state
and every residual owner action.

## Invariants

- Applicability is probed before inventory and before any confirmation.
- `not-applicable` and refusal are explicit states, never guessed healthy configuration.
- The doctor payload is the authoritative finding and inventory source for this pass.
- Mechanical and bounded structural findings may be applied; build policy and judgement remain report-only.
- The closing verifier is the same `.agents/quenching/bin/cq toolchain doctor --json` program as the probe.
- The command never rewrites `.agents/quenching.json` into a toolchain artifact or creates a second configuration home.
