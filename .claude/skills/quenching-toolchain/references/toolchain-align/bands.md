# Toolchain finding disposition bands

<!-- rules -->

This file owns only the toolchain finding codes, their disposition bands and the manifest, lock,
runtime and shared-key-specific closure shapes. The common axis is defined in
[`front-align/mold.md`](.agents/quenching/references/front-align/mold.md) §Disposition bands. This reference currently has
no judgement finding; the boundary is explicit below.

## Mechanical findings

The align may close mechanical findings in the same pass. It regenerates from the declared source,
preserves authored text outside a generated block, and re-runs the verifier.

| Code | Severity | Closure |
| --- | --- | --- |
| `tc-lock-stale` | `error` | Regenerate the lock from the declared ecosystem source and re-run the verifier. |
| `tc-config-duplicate` | `error` | Apply the declared arbiter and remove the duplicate source; never choose an owner from file order. |
| `tc-generated-stale` | `error` | Regenerate the tool-configuration block from its deterministic source while preserving authored text outside the generated zone. |

No per-artifact policy question belongs in this band when the source and generated result make the
same answer.

## Structural findings

The align may apply structural findings after the one plan authorization. It makes one bounded
shape repair per affected declaration and re-runs the verifier before reporting the result.

| Code | Severity | Closure |
| --- | --- | --- |
| `tc-runtime-drift` | `error` | Align the language declarations under the target's chosen version source, or hand the version choice to the target owner when none is declared. |
| `tc-tool-unpinned` | `error` | Add the reproducible pin in the declared toolchain home; selecting a version where none is evidenced remains a target-owner decision. |
| `tc-build-backend-missing` | `error` | Add or repair the backend only when the target's build contract identifies it; otherwise report the missing decision. |
| `tc-key-unarbitered` | `error` | Apply the shared arbiter-by-key contract; report ambiguous ownership rather than selecting it by the front. |

These repairs establish a published shape. They do not select a language version, a tool version,
a build backend or a configuration owner when the target has not declared one.

## The judgement boundary

There are no judgement-band `tc-*` findings in this initial vocabulary. Policy choices remain
outside the verifier until a target contract makes the decision statically decidable:

- formatter and ruleset selection;
- upgrade cadence and dependency policy;
- adopting a lock where none is required by the target ecosystem;
- selecting a language version, tool pin or build backend where no source is declared.

The align reports evidence and an executable closing action for such a choice when it encounters
one, but it never invokes the policy change or turns it into a finding by guessing the owner's
answer. A future judgement code must be added to the standard and to this mapping together.

## The complete mapping

The seven codes have one and only one disposition:

| Band | Codes |
| --- | --- |
| Mechanical | `tc-lock-stale`, `tc-config-duplicate`, `tc-generated-stale` |
| Structural | `tc-runtime-drift`, `tc-tool-unpinned`, `tc-build-backend-missing`, `tc-key-unarbitered` |
| Judgement | none |

The remedy named for a finding is capability-shaped. A future toolchain command must prove the
concrete regeneration, bounded repair or handoff named by the closure before presenting that
finding; "fix the lock" without an available action is not a complete closure.

<!-- rationale -->

The split keeps a toolchain align useful without making it a build-policy authority. Deterministic
lock and generated-block changes have mechanical repairs; cross-file declaration repairs are
bounded structural changes; and an absent target policy remains with the human who owns the build
risk. Severity stays independent so an `error` does not silently grant the align permission to
choose the remedy.
