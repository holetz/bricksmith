# Front mold — executor reference

<!-- rules -->

This file is the executor-facing projection of the binding architecture standard at
`docs/standards/architecture/front-mold.md`. The standard owns the contract; this reference gives
front command authors the compact reader surface and must not add a second minimum or a different
disposition rule.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## The common minimum

Every local front carries the same outer shape:

| Surface | Required shape | Front-owned delta |
| --- | --- | --- |
| Declaration | owned tree and boundary, namespaced configuration home, applicability, read model, verifier, align, status and finding owner | tree, keys, payload and domain boundaries |
| `cq` route | normalized front entry point, `--help`, `--json`, exits `0`/`1`/`2`, explicit refusal, `doctor` and `status` | inventory, registry, build, render, readme, ratchet and other extra verbs |
| Align | probe, applicability, one plan/authorization, bounded writes, closing verifier and residual report | domain arbitration and repair decisions |
| Status | read-only payload, missing/refused/not-applicable states, stable finding codes/bands and closing owner | authoritative evidence rows |
| Translation | generated Codex skills and references checked against the Claude source | front-specific vocabulary |

Do not turn this table into a generic runtime module or a second configuration schema. If a field is
owned by only one front, it remains in that front's contract.

## Route and applicability floor

The normalized route must keep `--help`, data-only `--json` stdout, stderr diagnostics, typed exits,
and explicit refusal for missing or invalid configuration. `doctor` verifies the declared surface;
`status` reads it without writing. Extra verbs are front-owned deltas and never weaken this floor.

Applicability is named before inventory. No signal means not applicable, an explicit scope means
skipped, and a present front with no findings is conformant. A refusal is reported as a refusal;
never invent a root, router, layer or namespace to make the front applicable.

## Disposition bands

Each front's `bands.md` maps every finding exactly once. The bands identify who may decide closure,
not the finding severity:

| Band | Decider | Action |
| --- | --- | --- |
| Mechanical | generated artifact and deterministic source | regenerate and re-run the verifier |
| Structural | contract plus one run authorization | apply a bounded repair and re-run the verifier |
| Judgement | human who owns target risk | report evidence and the closing command; do not edit automatically |

A front with no mechanical finding says so explicitly. Codes, evidence, severity, disposition and
closing command remain front-owned. A judgement command in a report is a hand-off, never an
invocation by the align or status body.

## Domain deltas

The mold does not erase domain meaning:

- design keeps its DTCG source, projections, optional Impeccable input, source arbitration and
  genre destinations;
- ops keeps its namespaced `opsRoot`, router, registry, inventory, lifecycle and `op-*` findings;
- proof keeps its namespaced proof root, layers, fixture reach, measured roots, floors, CI, order
  evidence and `pf-*` findings;
- toolchain keeps its declared scope, manifests, locks, language pins, tool configuration and
  `tc-*` findings while build policy remains target-owned;
- delivery keeps its provider-equivalent workflow inventory, reachability and `delivery-*`
  findings while provider, environment, publication and permission policy remains target-owned;
- security remains a read-only pillar over workflow permissions, secret-ignore coverage, advisory
  configuration and access ownership, with no converging tree and no exposed values.

Read the binding architecture standard for the measured footprint, host decision and full delta
contract. This projection is updated only when that host changes; it never becomes an independent source of truth.
