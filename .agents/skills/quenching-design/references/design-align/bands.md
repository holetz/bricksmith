# Design finding disposition bands

<!-- rules -->

This file owns design's finding codes and closures. The common disposition axis is defined in
[`front-align/mold.md`](.agents/quenching/references/front-align/mold.md) §Disposition bands.

## Mechanical findings

`design-generated-missing` is mechanical only after the source arbitration gate is settled and the
DTCG source remains the winner. The bounded closure is `.agents/quenching/bin/cq design build`; the align then runs
`.agents/quenching/bin/cq design doctor --json`. A missing projection must not overwrite an external `DESIGN.md` before
the owner chooses `build` or `import`.

## Structural findings

There are **none** in the current design contract. Correcting a token, product fact, visual value,
asset declaration, contrast policy, non-web scope or source winner changes a design decision or its
meaning. Until a bounded repair is evidenced, those findings stay judgement instead of being
forced into this band.

## Judgement findings

The following design findings require the owner command named in the table:

| Code(s) | Evidence to report | Closing command to name |
| --- | --- | --- |
| `design-front-absent`, `design-source-unreadable`, `design-build-refusal` | missing, unreadable or unbuildable DTCG source and the exact refusal | the owner's reviewed source installation or `/quenching-design align` |
| `design-generated-drift` | projection path and byte-level difference, plus whether an external proposal exists | `/quenching-design align` with the owner's `--design-winner import` or `--design-winner build` choice |
| `design-import-diff`, `design-import-skipped` | portable values that differ and richer values that cannot round-trip | the owner's reviewed import decision, then `.agents/quenching/bin/cq design import` when accepted |
| `design-dtcg-schema`, `design-extension-missing`, `design-extension-field`, `design-external-version`, `design-product-version`, `design-dtcg-shape`, `design-token-empty`, `design-token-value`, `design-token-reference` | source path and invalid DTCG or extension field | the owner's reviewed `.design/tokens.json` change, then `/quenching-design align` |
| `design-components-shape`, `design-component-shape`, `design-component-property`, `design-component-reference` | component name/property and the unsupported or missing reference | the owner's reviewed component extension or projection change |
| `design-asset-orphan`, `design-asset-manifest`, `design-asset-variant-missing` | asset or manifest path, role/orientation evidence and the missing reference/variant | the owner's reviewed asset or manifest change, then `.agents/quenching/bin/cq design doctor --json` |
| `design-font-metadata`, `design-font-unresolved` | font token, metadata and unresolved asset path | the owner's reviewed font declaration or asset change |
| `design-contrast-policy`, `design-contrast-unmeasurable`, `design-contrast-failure` | contrast policy, pair, measured ratio and required level | the owner's reviewed token or policy change, then `.agents/quenching/bin/cq design doctor --json` |
| `design-nonweb-scope`, `design-nonweb-literal` | non-web adapter path, literal and declared scope | the owner's reviewed adapter or scope declaration |

The align may report `design-align-blocker` or `design-refusal` as a refusal state, not as a
finding to auto-close. It does not invent product truth, select a source winner, install an
optional detector, or run a target's screen-review command.

## The complete mapping

Every current design finding has one disposition:

| Band | Codes |
| --- | --- |
| Mechanical | `design-generated-missing` after source arbitration |
| Structural | none |
| Judgement | every other `design-*` finding listed above |

<!-- rationale -->

The empty structural band is deliberate. The design front's deterministic work is projection
generation once an owner has chosen the source; all other repairs change what the design means or
which artifact is authoritative. The band map keeps that boundary visible instead of granting the
align authority over taste.
