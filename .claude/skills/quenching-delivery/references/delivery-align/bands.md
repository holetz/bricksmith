# Delivery finding disposition bands

<!-- rules -->

This file owns delivery finding codes, their disposition bands and the pipeline-specific closure
shapes. The common axis is defined in
[`front-align/mold.md`](.agents/quenching/references/front-align/mold.md) §Disposition bands.

## Mechanical findings

The delivery front may close a mechanical finding when the source and generated artifact make the
same answer. A future generated workflow matrix or manifest can use this band; no mechanical code
is claimed until such an artifact is part of the route's measured inventory.

| Code | Closure |
| --- | --- |
| `delivery-generated-stale` | Regenerate the owned workflow or release artifact from its deterministic source, then rerun `.agents/quenching/bin/cq delivery doctor --json`. |

## Structural findings

Structural findings describe a pipeline relationship that is statically decidable and has a
bounded shape repair. They do not authorize a deployment or a release.

| Code | Closure |
| --- | --- |
| `delivery-trigger-unreachable` | Connect the declared trigger to an intended job or stage, then rerun the verifier. |
| `delivery-stage-unreachable` | Repair the stage dependency or remove the stale declared stage under the target owner's review, then rerun the verifier. |
| `delivery-checkout-missing` | Add the checkout provenance required by the job's commands, then rerun the verifier. |
| `delivery-setup-missing` | Add the declared runtime or dependency setup required by the job, then rerun the verifier. |
| `delivery-runtime-drift` | Align the workflow runtime with the toolchain declaration, or hand the version choice to the toolchain owner when none is declared. |
| `delivery-release-unreachable` | Repair the release path's reachability from the pipeline without choosing its promotion policy, then rerun the verifier. |

These repairs establish a pipeline shape. They do not select a provider, environment, branch
promotion rule, publish scope or deployment command.

## Judgement findings

The delivery align reports each judgement finding with its evidence and the owner action that can
close it. It never invokes that action and never turns a target-specific policy into an automatic
edit.

| Code | Evidence to report | Closing decision to name |
| --- | --- | --- |
| `delivery-provider-policy` | Provider or provider-equivalent syntax and the unsupported or ambiguous choice | The target owner's provider decision, then the corresponding delivery alignment. |
| `delivery-environment-policy` | Environment names, gates or promotion targets whose intended policy is not statically inferable | The target owner's environment and promotion decision. |
| `delivery-publish-scope` | Release or publish path and the scope it would expose | The target owner's reviewed release and publication decision. |
| `delivery-permission-policy` | Workflow or job permissions that require a security risk decision | The read-only security audit and the target owner's reviewed permission change. |

Permission observations are handed to the security pillar; the delivery front does not weaken,
grant or otherwise choose permissions while reporting pipeline shape.

## Non-finding states

An absent delivery signal is **not applicable**, not a finding. A present but unreadable or
contradictory workflow declaration is a refusal that preserves the exact provider error. A
readable tree with no finding is conformant. Unsupported provider syntax is **not measured** until
an adapter or an evidence-backed equivalent is available; it must not be reported as green.

## The complete mapping

Each delivery finding has one and only one disposition:

| Band | Codes |
| --- | --- |
| Mechanical | `delivery-generated-stale` |
| Structural | `delivery-trigger-unreachable`, `delivery-stage-unreachable`, `delivery-checkout-missing`, `delivery-setup-missing`, `delivery-runtime-drift`, `delivery-release-unreachable` |
| Judgement | `delivery-provider-policy`, `delivery-environment-policy`, `delivery-publish-scope`, `delivery-permission-policy` |

<!-- rationale -->

The split keeps delivery useful without making it a deployment authority. Generated artifacts can
be regenerated mechanically; reachability and provenance are bounded structural facts; and
provider, environment, promotion, publication and permission choices remain with the owners who
carry their risk.
