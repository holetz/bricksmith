# Proof finding disposition bands

<!-- rules -->

This file owns only proof finding codes, their disposition bands and the layer, fixture,
measurement, floor, CI and order-specific closures. The common axis is defined in
[`front-align/mold.md`](.agents/quenching/references/front-align/mold.md) §Disposition bands.

## Mechanical findings

The proof align has no mechanical finding in the first contract. This band remains explicit so a
future generated proof artifact can be added without quietly changing the meaning of structural
or judgement work.

## Structural findings

The align may apply structural findings after the one plan authorization. It makes one bounded
edit per affected declaration, then re-runs `.agents/quenching/bin/cq proof doctor --json` before reporting the result.

| Code | Closure |
| --- | --- |
| `pf-unmarked` | Register the derived pytest marker and retain strict marker checking. |
| `pf-stop-first` | Remove the authoritative stop-first option or wrapper that discards later failures. |
| `pf-loose-fixture` | Move the duplicated fixture into the declared fixture library. |
| `pf-fat-conftest` | Move shared fixtures out of a non-library `conftest.py` into the declared fixture library. |
| `pf-no-floor` | Write the floor at the achieved value, but only when the measured surface is unchanged in this run. |
| `pf-order-unproven` | Add the declared, observable order experiment to the existing gate when that is a bounded non-blocking edit. |

These changes repair the published proof shape. They do not decide which layer a test proves,
whether a source surface should enter the measurement, whether CI should be installed, or whether
an operations entry point is intentionally outside the test surface.

## Judgement findings

The align reports each judgement finding with its evidence and the command that can close it for
the target. It never invokes that command and never converts the finding into an automatic edit.
The command named in the table is a hand-off, not an instruction for the align to execute.

| Code | Evidence to report | Closing command to name |
| --- | --- | --- |
| `pf-unlayered` | Test modules outside every declared layer, with their paths and counts. | The target owner's layer declaration, then `/quenching-proof layer-new` for each new layer. |
| `pf-empty-layer` | The declared layer, its reach, budget, and absence of collected tests. | The target owner's layer or test change, or `/quenching-proof layer-new` when the layer is being reminted. |
| `pf-unmeasured-surface` | Each product surface outside measured roots or exclusions, with the consequence for the coverage number. | The target owner's reviewed measurement or exclusion change, then `/quenching-proof align`. |
| `pf-no-ci` | The CI providers searched and the missing invocation of the proof gate. | The target owner's reviewed CI workflow, then `/quenching-proof align`. |
| `pf-untested-entrypoint` | The active operations entry point, its path, and the missing test or explicit not-applicable record. | The target owner's reviewed test or applicability declaration, then `/quenching-proof align`. |
| `pf-cost-undeclared` | The cost contract as declared (whole or absent, the missing keys named), and the measured cost a run pays without it. | The target owner's `proof.cost` declaration in `.agents/quenching.json`, guided by `/docs/standards/ci-cd/test-cost.md`, then `/quenching-proof align`. |
| `pf-temp-shared` | The declared `tempRoot: system` and where the target's runner writes its temporary files. | The target owner's runner change to a root outside the repository, removed when the run ends; then the declaration and `cq proof readme --write`. |
| `pf-scope-wide` | The close scope and the CI scope as declared, and the consequence for a one-line change. | The target owner's narrower local tier, keeping the full run in CI; then the declaration and `cq proof readme --write`. |
| `pf-task-wide` | The task scope and the close scope as declared, and the consequence for every task of the spec. | The target owner's narrower `scopes.task` — the file or directory the task touches — keeping the gate at the close; then the declaration and `cq proof readme --write`. |

## The floor-deferral rule

The bands answer **who decides**; they do not answer **in what order**. The align never sets a
floor in the same run that changes the measured surface. `pf-no-floor` is structural and normally
closes by writing the achieved value, but where `pf-unmeasured-surface` is also open the floor is
deferred. The report names the unmeasured surface, says that no floor was written, and names the
next `/quenching-proof align` run after the human closes the surface.

A floor is a promise about a measurement. Changing what is measured in the same run makes that
promise about something else, so the two findings cannot be silently closed under one OK.

## Resolved decisions

`pf-order-unproven` is structural only when an existing proof-gate invocation can gain a bounded,
non-blocking order experiment. If the only honest repair would install or materially change a
blocking CI gate, the finding is judgement for that run: the align reports the risk and names the
target owner's CI change. This keeps the disposition tied to the evidence available in the target,
not to a promise that every CI edit is harmless.

A target that declares no layers at all is not offered inferred layers or placeholder directories.
The align reports its unlayered modules and any layer findings as judgement residue, names
`/quenching-proof layer-new` as the command that can establish a layer deliberately, and lets the
rest of the report reduce to the gate findings. This follows the sweep's evidence-gating rule:
absence of a declaration is not evidence for a taxonomy.

## The complete mapping

The fifteen codes have one and only one disposition:

| Band | Codes |
| --- | --- |
| Mechanical | none — no proof artifact has a deterministic repair in this contract |
| Structural | `pf-unmarked`, `pf-stop-first`, `pf-loose-fixture`, `pf-fat-conftest`, `pf-no-floor`, `pf-order-unproven` when its non-blocking condition is met |
| Judgement | `pf-unlayered`, `pf-empty-layer`, `pf-unmeasured-surface`, `pf-no-ci`, `pf-untested-entrypoint`, `pf-cost-undeclared`, `pf-temp-shared`, `pf-scope-wide`, `pf-task-wide`, and `pf-order-unproven` when its structural condition is not met |

<!-- rationale -->

The split keeps an align useful without making it a taxonomy or verification authority. Marker,
failure-collection, fixture-ownership, order-evidence, and floor edits have bounded repairs. Layer
membership, measured-surface scope, CI installation, and active-entry-point coverage change what
the repository claims to prove and therefore stay with the human who owns that claim.
