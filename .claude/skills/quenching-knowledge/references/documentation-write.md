---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Write, Edit, Task, Bash(python3:*), Bash(py:*), AskUserQuestion
---

# /quenching-knowledge documentation-write — draft pages from the accepted plan

**Input**: `$ARGUMENTS` (an optional page slice or plan path; omit to use
`./.quenching/documentation/plan.md` and write every assigned page).

Reads the accepted plan and writes only the assigned pages in the bundle's **reader-facing
quadrants** — `./docs/{tutorials,how-to,documentation,project}/` and the root
`index.md`. `standards/`, `concepts/`, `external/`, `catalog/`, `vision/` and `glossary.md` belong
to the knowledge front and are written under its contract, never here. The Zensical site stages
only the allowlisted homes plus the root glossary; raw `external/` and `catalog/` content is never
published by this site. Apply
[knowledge-documentation/craft.md](.agents/skills/quenching-knowledge/references/knowledge-documentation/craft.md),
[knowledge-documentation/visual.md](.agents/skills/quenching-knowledge/references/knowledge-documentation/visual.md)
and
[knowledge-documentation/agent-readability.md](.agents/skills/quenching-knowledge/references/knowledge-documentation/agent-readability.md)
in one pass per page.

## Doctrine

- **Plan is the boundary.** Write only destinations assigned in the accepted plan; keep one reader intent per page.
- **Craft serves truth.** Use hooks, fast paths, progressive disclosure, tables, cards, tabs or Mermaid only when their function is clear.
- **Agent-ready is explicit.** Stable headings, relative links, copyable real examples, expected output and `TL;DR for agents` blocks carry the contract in text.
- **Extensions precede syntax.** Read `zensical.toml` and confirm each required extension before adding its syntax; report a missing extension to `build`.
- **Glossary links point at the glossary.** The canonical file is staged into `site-source/` and
  publishes as `glossary.md`; link it relatively (`../glossary.md`) from a section page. Terms stay
  canonical in the root file, and authors never hand-maintain abbreviation definitions. Run
  `.agents/quenching/bin/cq knowledge project docs --write`, then `.agents/quenching/bin/cq knowledge site-source docs site-source --write` to
  materialize the snippet and bounded input from their source hashes; rerunning either must be a
  byte-identical no-op.
- **Derived knowledge is curated, not copied wholesale.** `standards/`, `concepts/` and `vision/`
  may enter the allowlisted site source according to the accepted map. Raw `external/` and
  `catalog/` homes never do; if a reader-facing page uses facts from either, preserve source origin,
  hash and transformation in the ledger and publish only that curated page.
- **Source ledger travels with prose.** Record every strong claim, confidence and `source gap:` beside the page set.
- **Catalog pages carry lineage beside prose.** For a derived catalog, write the source ledger next
  to the page set and preserve `id`, `layer`, `schema`, source path and transformation on each
  detail page; the index is generated from those records rather than hand-maintained.
- **Ledger and TODOs stay internal.** Store the page ledger at `.quenching/documentation/ledger.md`,
  reject `TODO`, `FIXME`, prompt markers and unresolved contract text in published pages, and end
  each page with a relative next step. If the page is a terminal reference, its next step must leave
  the page (section index, guide or troubleshooting route), not merely repeat the footer.
- **Fan-out is bounded.** When more than roughly six pages are assigned, use one `Task` per page slice, pin each to the session model (never `haiku`), and merge only their summaries.

## Workflow

### 1. Load the plan and page assignments

Read `.quenching/documentation/plan.md`, confirm the accepted execution order, target pages,
source origins, whole-bundle mandatory surfaces, glossary route and open gaps. **Done when:** every
page or derived projection to write has one intent, one destination and a source set.

### 2. Check the site extensions

Read `zensical.toml` (or record its absence for `build`) and map every planned component to
`attr_list`, `md_in_html`, `pymdownx.tabbed`, `pymdownx.emoji`, the Mermaid fence or another
enabled extension. **Done when:** each syntax choice is enabled or is recorded as a site-layer
finding, before it appears in prose.

### 3. Draft the pages

Write each page with a payoff hook, fast path, structured depth and next step. Use relative links,
real commands and expected output; put unsupported claims in visible `source gap:` notes. For a
large batch, delegate page slices with `Task` and merge the returned summaries, never an opaque
full rewrite. **Done when:** every assigned page exists with its intended frontmatter, headings,
examples, useful visuals and agent contract.

### 4. Write the source ledger

Add a ledger entry for each page's strong claims, using the five allowed origins and confidence
levels from `quality.md`. Carry open gaps verbatim into the run report. **Done when:** every strong
claim has a source row or an explicit `source gap:`.

Write the ledger under `.quenching/documentation/ledger.md` and scan the destination pages for
internal markers before accepting them. Count mapped sections with substantive content and record
`covered / mapped × 100`, where `mapped` is every mandatory publication-map row across the whole
bundle; an intentional exclusion is a plan decision, not a blank page.

### 5. Self-check the written set

Check page paths, frontmatter, relative links (including the published glossary route), stable headings, component syntax and next-step
links. Run `.agents/quenching/bin/cq knowledge project ... --check` and compare every mandatory map row with a non-empty
route. Report pages intentionally left for a later slice, but do not call the whole-bundle gate
green when a mandatory surface is missing.
**Done when:** the diff contains only assigned documentation pages and ledger changes, with no
invented fact and no unplanned destination.

## Invariants to never violate

- Never change `zensical.toml` or any other site-layer file; route those findings to `build`.
- Never overwrite an unassigned page or discard human prose; merge only the planned gap.
- Never use `haiku` for a writing sub-agent.
- Never hide a critical fact only in an image or diagram.
