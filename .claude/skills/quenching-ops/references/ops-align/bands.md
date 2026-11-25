# Ops finding disposition bands

<!-- rules -->

This file owns the ops finding codes, their disposition bands and the router, registry and
entry-point-specific closures. The common axis is defined in
[`front-align/mold.md`](.agents/quenching/references/front-align/mold.md) §Disposition bands.

## Mechanical findings

The align may close mechanical findings in the same pass. It regenerates the registry from the
inventory, preserving authored text outside the generated block, and re-runs the verifier.

| Code | Closure |
| --- | --- |
| `op-undocumented` | `.agents/quenching/bin/cq ops registry --write` |
| `op-registry-stale` | `.agents/quenching/bin/cq ops registry --write` |

No per-entry-point confirmation belongs in this band: the source and the generated registry make
the same answer.

## Structural findings

The align may apply structural findings after the one plan authorization. It makes one bounded
edit per affected entry point, then re-runs `.agents/quenching/bin/cq ops doctor --json` before reporting the result.

| Code | Closure |
| --- | --- |
| `op-adhoc-root` | Move root resolution to the shared bootstrap module. |
| `op-untyped-exit` | Return the entry point's typed exit status from its `__main__` path. |
| `op-no-router` | Establish the one declared canonical router, after both configuration paths resolve. |

These changes repair the published shape. They do not arm a write, decide that code is dead, or
choose a target-specific operational policy.

## Unconfigured operations roots

Configuration is a prerequisite for the inventory, not a default the align may invent. When either
`opsRoot` or `router` is absent from `.agents/quenching.json` (or the legacy `.agents/quenching.json`), `.agents/quenching/bin/cq ops doctor --json` refuses with
`op-config-missing`, names both required keys and exits `2`. The align reports that refusal and
stops after the probe; it does not guess `scripts/`, select a router, or turn the refusal into an
`op-no-router` finding.

Once both paths are declared and readable, an absent canonical router is an `op-no-router`
structural finding. Its repair establishes exactly one router under the run's authorization and
then re-runs the probe. The two cases stay separate so an unconfigured target never receives an
invented inventory or a plan based on a root it did not declare.

## Judgement findings

The align reports each judgement finding with its evidence and the command that can close it for
the target. It never invokes that command and never converts the finding into an automatic edit.
The command is part of the report so the owner can act without guessing what the finding requires.

| Code | Evidence to report | Closing command to name |
| --- | --- | --- |
| `op-unarmed-write` | The entry point reaches a write-capable session or client without a preview and explicit arming decision. | The target owner's reviewed entry-point change, or `/quenching-ops entrypoint-new` when the entry point is being reminted. |
| `op-disabled-check` | The commented verification call, its entry-point control-flow location, and the choice to re-enable or explicitly declare it off. | The target owner's reviewed verification change, or `/quenching-ops entrypoint-new` when the entry point is being reminted. |
| `op-orphan` | The entry point's path, lifecycle declaration, and why it is not reachable from the canonical router. | The target owner's reviewed router, lifecycle, or archive change, or `/quenching-ops entrypoint-new` when the entry point is being reminted. |
| `op-registry-malformed` | The registry path and the refusal message (duplicated generated zone or invalid UTF-8). `.agents/quenching/bin/cq ops registry --write` refuses it, so no regeneration closes it. | The target owner's reviewed repair of the registry file, then `.agents/quenching/bin/cq ops registry --write`. |

The command named in this table is a hand-off, not an instruction for the align to execute. A
judgement finding remains open until its owner has made and verified the operational decision.

## The complete mapping

The nine codes have one and only one disposition:

| Band | Codes |
| --- | --- |
| Mechanical | `op-undocumented`, `op-registry-stale` |
| Structural | `op-adhoc-root`, `op-untyped-exit`, `op-no-router` |
| Judgement | `op-unarmed-write`, `op-disabled-check`, `op-orphan`, `op-registry-malformed` |

<!-- rationale -->

The split keeps an align useful without making it a production-change authority. Registry rows
and shared bootstrap wiring have deterministic repairs. Arming a write, restoring a disabled
check, or declaring an entry point dead changes operational risk and therefore stays with the
human who owns that risk.
