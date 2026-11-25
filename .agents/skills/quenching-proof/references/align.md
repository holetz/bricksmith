---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*), AskUserQuestion, Write, Edit
---

# /quenching-proof align — converge the verification surface

**Input**: `$ARGUMENTS` (an optional target repository root; omit to use the current repository).

This command owns one convergence pass over a target's declared proof surface. It reads the
static test inventory and gate, applies only mechanical and bounded structural repairs, and leaves
layer taxonomy, measured-surface scope, CI installation, and operations coverage with the target
owner. The disposition rules live in
[proof-align/bands.md](.agents/skills/quenching-proof/references/proof-align/bands.md); the gate and
layer contracts live in
[proof-align/gate-contract.md](.agents/skills/quenching-proof/references/proof-align/gate-contract.md)
and [proof-align/layer-contract.md](.agents/skills/quenching-proof/references/proof-align/layer-contract.md).
The common front contract lives in
[front-align/mold.md](.agents/quenching/references/front-align/mold.md); these references
keep the layer, fixture, measurement and gate evidence separate from the shared mold.

Set `TARGET_ROOT` to the supplied argument, or to `.` when the argument is omitted, and pass that
same root to every `.agents/quenching/bin/cq proof` call. Resolve `cq` per
[tool-resolution.md](.agents/quenching/references/align/tool-resolution.md) and branch
on exit code and JSON: `0` is clean, `1` carries findings, and `2` is a refusal.

## Workflow

### 1. Probe before the inventory

Run the verifier before reading the proof tree:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" proof doctor --json
```

When it exits `0` with no findings, report "proof conformant, nothing to align" and stop. Do not
run `proof inventory`, ask for confirmation, or write anything. When it exits `2`, report the
refusal exactly as returned — including a missing configured proof root — and stop. The align never
invents a proof root, a layer, a floor, or a runner.

**Done when:** the clean case or a refusal has stopped without an inventory, or a non-clean probe
has authorized the inventory stage below.

### 2. Read one inventory and the contracts

Only after exit `1`, run the single inventory pass:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" proof inventory --json
```

Read `proof-align/bands.md`, `proof-align/gate-contract.md`, `proof-align/layer-contract.md`, and
the target structure reference as the contracts for classifying the returned evidence. Use the
doctor payload as the finding source and the inventory payload as the test, fixture, layer, gate,
CI, and measured-surface source; do not rediscover the tree with another walk.

**Done when:** the declared proof root, layers, test modules, fixtures, gate, CI definitions,
measured roots, and finding evidence are in hand.

### 3. Classify the findings

Build one plan grouped by the three bands in `bands.md`:

| Band | Finding codes | Disposition |
| --- | --- | --- |
| Mechanical | none in the current contract | report that no generated proof artifact is owned by this band |
| Structural | `pf-unmarked`, `pf-stop-first`, `pf-loose-fixture`, `pf-fat-conftest`, `pf-no-floor`, and conditionally `pf-order-unproven` | make bounded gate or fixture-ownership edits; use `.agents/quenching/bin/cq proof ratchet --raise --json` only when the measured surface is unchanged and an artifact exists |
| Judgement | `pf-unlayered`, `pf-empty-layer`, `pf-unmeasured-surface`, `pf-no-ci`, `pf-untested-entrypoint`, `pf-cost-undeclared`, `pf-temp-shared`, `pf-scope-wide`, `pf-task-wide`, and conditional `pf-order-unproven` | report evidence and the command that closes it; never drive the change |

When no layers are declared, keep every unlayered test as judgement residue and do not infer a
taxonomy or create placeholder directories. When `pf-unmeasured-surface` is open, defer every
floor write in this pass, even when `pf-no-floor` is structural. When `pf-order-unproven` cannot
be closed by a bounded non-blocking edit to an existing gate, report it as judgement.

**Done when:** every finding has one disposition, affected paths, evidence, and either a bounded
closure or a named target-owned closing command.

### 4. Present one plan and gate once

Show the complete plan: every bounded edit, the floor deferral if applicable, every judgement
finding and its closing command, the files touched, and the closing verifier. Ask once for
authorization of the routine batch. A code-coupled target edit remains an individual confirmation
under the shared convergence contract. If the plan is rejected, write nothing and stop.

**Done when:** the complete plan has one approval, or rejection has ended the run with no writes.

### 5. Apply structural closures only

Under the authorization, apply `pf-unmarked`, `pf-stop-first`, `pf-loose-fixture`, and
`pf-fat-conftest` repairs only within the declared gate and proof tree. Add an order experiment
only when `bands.md`'s non-blocking condition is satisfied. Raise a floor only after the surface
scope is unchanged and the target has produced the artifact the ratchet reads:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" proof ratchet --raise --json
```

Read the result by the codes in `findings`, not by the exit code alone:

- Exit `2` is a refusal (`pf-coverage-missing`, `pf-coverage-invalid`, `pf-coverage-no-measured-root`,
  `pf-floor-invalid`): nothing was written.
- Exit `1` with `pf-floor-regressed` or `pf-floor-unmeasured` in `findings` is blocked: nothing was
  written. In both cases the floor was not raised: report the code, the root and the message as
  judgement or refusal residue per the gate contract, never call the floor raised, and never edit
  the floor file or the measured roots to make it pass.
- Exit `1` with only `pf-no-floor` is the first raise: the floor file was written for the new roots
  (`ratchetPath` in the JSON). Report it as raised, naming that file and the roots, and leave the
  written file in the working tree for the plan's review.

Never create a layer, choose a measured surface, install CI, classify an empty layer as required,
arm or waive an operations entry point, or run a target test suite. If the floor is deferred, do
not call the ratchet; state the reason in the report.

After edits, run the same probe again:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" proof doctor --json
```

Distinguish remaining judgement residue from a failed structural convergence. Do not loop an empty
pass or broaden the plan.

**Done when:** authorized bounded changes are applied, no judgement command has run, no target
suite has run, and the closing probe has been executed.

### 6. Report the cycle

Report the target root, proof root, layer and test-module counts, measured roots, CI evidence,
applied changes, floor deferral or ratchet result, and the closing probe. List every judgement
finding with its evidence and the exact command from `bands.md` that closes it. State explicitly
that the suite was not run. The run's account belongs in this report; do not append a log entry to
the target.

**Done when:** the report distinguishes applied closures from deferred decisions and names the
next target-owned action for every judgement finding.

## Invariants

- The verifier probe precedes every inventory and every confirmation.
- A refusal never becomes an invented proof root, layer, floor, runner, or CI gate.
- One plan and one routine confirmation cover safe bounded edits; code-coupled target changes keep
  their own confirmation.
- `pf-unmeasured-surface` defers `pf-no-floor`'s write in the same pass.
- Judgement findings are report-only, and no subcommand runs the target suite.
- The closing verifier is the same `.agents/quenching/bin/cq proof doctor --json` program as the probe.
- The proof align's report is the only run record; no sweep log is written.
