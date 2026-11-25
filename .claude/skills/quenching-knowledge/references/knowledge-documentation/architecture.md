# Documentation architecture — intent-led structure and output contracts

The information architecture that turns source files into a navigable Diátaxis site, plus the
seven intermediate contracts that make a documentation run reproducible.

## Contents

`.agents/quenching/bin/cq components read .agents/skills/quenching-knowledge/references/knowledge-documentation/architecture.md`
returns the heading index; `--sections <name>` addresses one.

<!-- rules -->

## Diagnose first

Before proposing structure, inventory the sources read-only:

```bash
rg --files -g '*.md'
rg -n '^#{1,3} ' docs
rg -n '\]\(' docs | rg -v 'http'
```

Record, in two to five lines, audience and intent, site state, and faults. Use this fault table:

| Fault | Signal | Fix |
| --- | --- | --- |
| Orphan page | `.md` not in nav (or in nav, no file) | wire it or report it |
| Duplication | same fact in two or more pages | one home, other pages link |
| Weak title | filename-as-title or `README` | rename to an intent |
| Wall of text | screens of prose without headings/lists/callouts | split and structure |
| Buried comparison | options described in prose | table or card grid |
| Buried process | steps described in prose | numbered list or Mermaid |
| Averaged page | beginner, expert and agent in one page | split or layer |

## Design by reader intent

Each page has exactly one intent. The usual top-level shape is:

| Section | Reader intent | Holds |
| --- | --- | --- |
| Get started | “Show me it works, fast” | landing, install, quick start |
| Guides / How-to | “Help me do a specific task” | task walkthroughs |
| Concepts | “Help me understand the model” | mental models, architecture |
| Reference | “Give me the exact detail” | APIs, options, catalogs |
| Maintain / Operate | “Keep it healthy” | operations, evolution, troubleshooting |

The narrative spine is landing → problem → solution → mental model → quick start → workflow →
reference → maintain. Use `navigation.footer` so the reader always has a next step.

## Reader journeys

Verify these three journeys end to end, without a dead end or forced backtracking:

- **5-minute evaluator:** landing → problem → quick start → verdict.
- **Working implementer:** quick start → guide → reference → troubleshooting.
- **Agent / LLM:** entry → matching heading → copyable contract or example.

## Navigation and routing table

Use `navigation.indexes` and relative links. Every section index carries a routing table:

| If you want to… | Go to |
| --- | --- |
| See it run in 30 seconds | [Quick start](../../../../assets/references/how-to/quick-start.md) |
| Understand why it is shaped this way | [Documentation](../../../../assets/references/documentation/index.md) |
| Look up an exact option | [Reference](../../../../assets/references/reference/index.md) |

<!-- rationale -->

The navigation answers a reader's question instead of mirroring storage. A short diagnosis catches
orphans and duplicated claims before prose makes them expensive to move.

## The seven output contracts

These are mandatory intermediate outputs. Produce them in order, show them once, and write pages
only after the plan is accepted. Store them in `.quenching/documentation/plan.md`, never as site
pages. If a contract cannot be filled, write `source gap: <what's missing>` rather than guessing.

### 1. Diagnosis

```markdown
### Diagnosis
- Sources found:      <files/dirs; who each is for>
- Target audience:    <primary readers + technical maturity>
- Current site state: <greenfield | existing nav shape | toolchain>
- Main problems:      <orphans · duplication · weak titles · walls · dumps>
- Truth/source risks: <claims with no obvious source; stats to verify>
- Visual opportunities: <where a flow/table/map would unlock a wall>
```

### 2. Reader journeys

```markdown
### Reader journeys
- 5-min evaluator:    landing → ? → ? → verdict (no full read needed)
- Working implementer: quickstart → guide → reference → troubleshooting
- Agent / LLM:        entry → matching heading → copy contract/example
```

### 3. Information architecture proposal

```markdown
### IA proposal
- New nav tree:       <intent-based sections & pages>
- Source → dest map:  <source.md → dest.md (+ split/merge)>
- Page intents:       <one intent per destination page>
- Out of scope:       <what is dropped and why>
- Becomes reference:  <which sources → reference pages>
- Becomes guide:      <which sources → how-to pages>
- Becomes concept:    <which sources → explanation pages>
```

### 4. Visual plan

```markdown
### Visual plan
- Cards:      <where "choose your path" applies>
- Tabs:       <where a concept has platform/profile/scenario variants>
- Mermaid:    <which flows/architectures/maps>
- Tables:     <which comparisons/params/decisions>
- Hero:       <landing(s) only>
- Badges:     <which pages get an info bar>
- Screenshots/assets: <need + strategy — proposed, see visual.md>
- NO visual:  <pages/sections that must stay plain text>
```

### 5. LLM-readability plan

```markdown
### LLM-readability plan
- TL;DR for agents:  <which pages carry a contract block>
- Input/output contracts: <which pages state them explicitly>
- Stable headings:   <headings that must not be renamed>
- Key anchors:       <#anchors agents will deep-link>
- Glossary/concept map: <exists? which terms defined once>
- Copyable examples: <which real commands/snippets>
```

### 6. Execution plan

```markdown
### Execution plan
- Files to edit:     <path → change>
- Markdown extensions needed: <attr_list, md_in_html, tabbed, emoji, mermaid, …>
- Risk per change:   <low | med | high — and why>
- Validation:        <strict build + visual QA + rubric threshold>
```

### 7. Publication map

```markdown
### Publication map
| Home | Decision | Motive | Audience | Published route |
| --- | --- | --- | --- | --- |
| documentation/ | publish | <motive> | <readers> | <route> |
| standards/ | publish derived | <motive> | <readers> | <route or index> |
| concepts/ | do not publish | <verifiable motive> | — | — |
```

Inventory `documentation/`, `standards/`, `concepts/`, `external/`, `catalog/` and `vision/`, plus
the root `glossary.md`. Each row decides **publish**, **publish derived** or **do not publish**; no
home gets a route by implication of its name. `publish derived` keeps the source outside
`docs_dir` and declares the page, index or projection that will be served. A decision without
evidence stays a `source gap:` in the plan, never content of the published page.

The map is also the coverage contract for the complete knowledge tree. A populated root glossary
is `publish` in the bounded Zensical source by default at `glossary.md`; only an explicit accepted
`do not publish` row changes that default. Its abbreviation snippet is generated by `.agents/quenching/bin/cq knowledge
project` from the canonical source, with `source` and `source_sha256` stamps, then copied into
`site-source/` by `.agents/quenching/bin/cq knowledge site-source`. Raw `catalog/` and `external/` homes are always
`do not publish` to this Zensical site: if their facts are needed, a reader-facing page must be
curated under an allowlisted home and carry its own source/provenance. Coverage is measured against
the publishable staged files, not against raw data or external-reference files.

## Catalog contract outside the site

Catalogs kept in `catalog/` stay in the knowledge bundle, but never enter `site-source/` or the
Zensical `nav`. When an editorial decision needs to expose derived understanding, it must point to a
curated page in a published home; the raw source stays outside the site. The retention contract is:

| Part | Contract |
| --- | --- |
| Entry | a versioned inventory with a stable identifier, layer and original source |
| Layer/schema index | the internal inventory groups first by layer and then by schema |
| Detail route | internal routes are not copied into `site-source/` |
| Lineage | each item points to its source file, date/version and the transformation applied |
| Editorial boundary | only fields approved in the map are published; secrets, dumps and unsourced data stay out |

The catalog gets no entry point in the `nav`; detail pages stay outside the site. A missing layer,
schema or source is a `source gap:` and blocks any derived curation.

## Capability registry

Each generator-specific capability gets one row in the documentation plan with five fields:
`capability`, prerequisite, decision (`enabled` or `disabled`), configuration/version evidence and
expected HTML effect. The registry also notes the risk, any `source gap:` and the test that proves
the effect. A capability with no prerequisite or fixture stays disabled; the absence is an auditable
decision, not an implicit success.

## Incremental cut and handoff

The incremental cut is an optimization, not a new source of truth: `ref` must resolve to an existing
commit, `generated.at` (or the legacy `timestamp`) must be UTC and each source must point to exactly
one destination. An invalid ref, a missing plan, a rename/removal or a dependency with no mapping
falls back to a full review and enters the report as a gap. Operational artifacts (plans, ledgers,
scorecards and reports) stay outside `docs_dir` and end in an explicit handoff for a human commit.

## Invariants

- One idea and one reader intent per destination page.
- Claims without a source become `source gap:` entries in the ledger.
- Contracts are planning artifacts and stay outside `docs_dir`.
- Every published section needs non-empty content and a reachable route; contracts, TODOs and
  gaps stay in the internal ledger, never on the served page.
- An unpublished home is an explicit editorial decision with a motive; it is not a silent limitation of the generator.
- Internal site links are relative and every page has a next step.
