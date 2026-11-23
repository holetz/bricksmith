# Zensical capabilities — evidence-led enablement

Use this catalog before adding a plugin-like feature to the site layer. Zensical keeps the core
build small; a capability is enabled only when its prerequisite, decision, configuration and
rendered effect are all recorded in the documentation plan.

## Capability catalog

| Capability | Prerequisite | Decision | Configuration/dependency | Verifiable rendered effect |
| --- | --- | --- | --- | --- |
| Heading autorefs | stable heading IDs and a tested extension | enable only for cross-page heading links | `toc.permalink` plus a fixture anchor | link resolves to the expected `#anchor` |
| Python API (`mkdocstrings`) | importable package and supported handler | disabled by default for this generator-neutral bundle | optional pinned dependency and handler config | API signature appears in generated HTML |
| Internal preview | strict build and explicit human confirmation | opt in per run | loopback `python -m http.server` | URL binds to `127.0.0.1` and process exits |
| Tags / facet search | metadata source and search index support | prefer headings and native search until measured need | frontmatter plus a documented index | filter/tag route returns the tagged page |
| Provenance | source ledger and page-level source fields | required for strong claims and derived catalogs | `source`, `generated.at` (or legacy `timestamp`), `lineage` fields | provenance block is visible in HTML |

`mkdocstrings` and facet search are not silently installed: this repository has no proof that their
runtime plugins are supported by the current Zensical release. Record `source gap:` and leave them
disabled until a fixture demonstrates the effect. Preview is operational tooling, not a publication
capability.

## Agent contract

Input: one capability row plus its evidence. Output: either a minimal config/dependency diff and a
fixture that proves the effect, or an explicit `disabled` decision with the missing prerequisite.
Never infer support from a similarly named MkDocs plugin.
