---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*), AskUserQuestion, Write, Edit
---

# /quenching-ops align — converge the operations surface

**Input**: `$ARGUMENTS` (an optional target repository root; omit to use the current repository).

This command owns the operations front's convergence pass. It reads the target's declared
operations root and canonical router, applies only deterministic mechanical and bounded structural
repairs, and leaves production-risk decisions with the target owner. The disposition rules live in
[ops-align/bands.md](.agents/skills/quenching-ops/references/ops-align/bands.md); the target tree and
router contract live in [ops-align/target-structure.md](.agents/skills/quenching-ops/references/ops-align/target-structure.md)
and [ops-align/entrypoint-contract.md](.agents/skills/quenching-ops/references/ops-align/entrypoint-contract.md).
The common front contract lives in
[front-align/mold.md](.agents/quenching/references/front-align/mold.md); these references
keep the router, registry and entry-point-specific evidence separate from the shared mold.

Set `TARGET_ROOT` to the supplied argument, or to `.` when the argument is omitted, and pass that
same root to every `.agents/quenching/bin/cq ops` call.

Resolve `cq` per
[tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool. Use the resolved path literally and branch on exit code and JSON:
`0` is clean, `1` reports findings, and `2` is a refusal.

## Workflow

### 1. Probe before the inventory

Resolve the tool, then run the verifier before reading the operations tree:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" ops doctor --json
```

When it exits `0` with no findings, report "ops conformant, nothing to align" and stop. Do not
run `ops inventory`, ask for confirmation, or write anything. When it exits `2` with
`op-config-missing`, report the missing `opsRoot` and/or `router` exactly as returned and stop;
the align never guesses `scripts/`, a router, or an inventory. The probe rule is the operations
application of [sweep-doctrine.md](.agents/quenching/references/align/sweep-doctrine.md)
§1. Probe before the inventory.

**Done when:** the clean case or a configuration refusal has stopped without an inventory, or a
non-clean verifier result has authorized the inventory stage below.

### 2. Read one inventory and the shared contracts

Only after exit `1`, read the verifier's JSON and run the single inventory pass:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" ops inventory --json
```

Read `ops-align/bands.md`, `ops-align/entrypoint-contract.md`, `ops-align/lifecycle.md`, and
`ops-align/target-structure.md` as the contracts for classifying the returned entry points and
findings. Use the verifier payload as the finding source and the inventory payload as the entry
point source; do not rediscover files with a second walk.

**Done when:** the declared root, router, entry points, lifecycle states, and finding evidence are
in hand.

### 3. Classify the findings

Build one plan grouped by the three bands in `bands.md`:

| Band | Finding codes | Disposition |
| --- | --- | --- |
| Mechanical | `op-undocumented`, `op-registry-stale` | regenerate the registry under the run's authorization |
| Structural | `op-adhoc-root`, `op-untyped-exit`, `op-no-router` | make one bounded edit per affected entry point, then re-probe |
| Judgement | `op-unarmed-write`, `op-disabled-check`, `op-orphan`, `op-registry-malformed` | report evidence and the command that closes each; never run it |

Keep `op-config-missing` as a refusal from step 1, never as an inventory finding. Do not turn a
judgement finding into a mechanical or structural repair, and do not infer an operational policy
from a missing optional file.

**Done when:** every finding has one band, affected paths, evidence, and either a bounded closure
or a named human-owned closing command.

### 4. Present one plan and gate once

Show the complete plan: registry regeneration, each structural edit, each judgement finding and
its closing command, the files touched, and the closing verifier. Ask once for authorization of
the routine batch. A code-coupled target edit or an irreversible action remains an individual
confirmation item under the shared sweep contract; the batch confirmation never absorbs either.

If the plan is rejected, write nothing and stop.

**Done when:** the complete plan has one approval, or rejection has ended the run with no writes.

### 5. Apply only the first two bands

Under the authorization, apply the mechanical closures and the bounded structural repairs. Use
`.agents/quenching/bin/cq ops registry --write` only for a mechanical registry finding, and edit only the declared
operations root or registry path for a structural finding. Never arm a write-capable entry point,
archive an entry point, re-enable a disabled check, or select a router the target did not declare.

After the edits, run the same probe again:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" ops doctor --json
```

If the probe still reports findings, distinguish remaining judgement residue from failed
mechanical or structural convergence; do not loop an empty pass or broaden the plan.

**Done when:** authorized mechanical and structural changes are applied, no judgement command has
run, and the closing probe has been executed.

### 6. Report the cycle

Report the target root, router, entry-point count, applied changes, the closing probe result, and
every judgement finding with its evidence and exact command that closes it. State explicitly when
the target was already conformant, configuration refused the run, or residue remains. The run's
account belongs in this report; do not append a log entry to the target.

**Done when:** the report distinguishes applied closures from reported residue and names the next
human-owned action for every judgement finding.

## Invariants

- The verifier probe precedes every inventory and every confirmation.
- `op-config-missing` stops the run; no default root, router, or inventory is invented.
- One plan and one routine confirmation cover the pass; code-coupled and irreversible actions keep
  their own confirmation.
- Mechanical and structural findings may be applied; judgement findings are report-only.
- The closing verifier is the same `.agents/quenching/bin/cq ops doctor --json` program as the probe.
- The operations front's own report is the only run record; no sweep log is written.
