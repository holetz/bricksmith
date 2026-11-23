---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*), Bash(py:*), Bash(rg:*), Write, AskUserQuestion
---

# /quenching-knowledge documentation-plan — diagnose sources and record the documentation plan

**Input**: `$ARGUMENTS` (optional source paths or a scope; omit to inspect the target repository's
README, legacy docs, code, specs and `/docs/standards/`).

This planning pass reads sources across the bundle and writes only the plan-of-record at
`./.quenching/documentation/plan.md`. The seven contracts, the architecture rules
and reader journeys live in
[knowledge-documentation/architecture.md](.agents/skills/quenching-knowledge/references/knowledge-documentation/architecture.md);
source provenance is defined by
[knowledge-documentation/quality.md](.agents/skills/quenching-knowledge/references/knowledge-documentation/quality.md).

## Doctrine

- **Sources are evidence.** Inventory before interpreting; cite a file, code location, provided official document, user link or marked inference.
- **Contracts precede prose.** Produce diagnosis, journeys, IA, visual, LLM-readability and execution contracts in that order.
- **Gaps stay visible.** Every unsupported claim is `source gap: <what's missing>`, never an invention.
- **The plan is outside `docs_dir`.** `.quenching/` is never a documentation page; if the target
  does not ignore it yet, say so and route the entry to `build` (`site-scratch-tracked`).
- **One OK gates the plan.** Show the complete table and wait for confirmation before writing the record.

## Workflow

### 1. Confirm the target and inventory sources

Resolve the repository root and verify `/docs/index.md`, then inventory `tutorials/`, `how-to/`,
`documentation/`, `project/`, `standards/`, `concepts/`, `external/`, `catalog/`, `vision/` and root
`glossary.md` — **listing every `.md`**, because the coverage denominator is documents, not homes. Collect source
paths, heading skeletons, links, existing nav/config and relevant code/specs with read-only tools.
The inventory is the whole bundle even when the requested editorial slice is small. **Done when:**
the source inventory names each home, its paths and audience, or records the missing bundle as a
handoff to `/quenching-knowledge align`.

### 2. Diagnose faults and truth risks

Classify audience, site state, orphan/duplicate/weak-title/wall/dump faults, claims without an
origin and visual opportunities. Keep evidence as file paths or line references. **Done when:**
the Diagnosis contract is filled and every uncertain claim is a `source gap:`.

### 3. Compose the seven contracts

Fill the exact templates in `architecture.md`: three end-to-end journeys, an intent-based nav and
source→destination map, a visual plan, an agent-readability plan and an execution plan listing
extensions, risk and validation. Then produce the **Publication map**: one decision
per home — `publish`, `publish derived` or `do not publish` — with motive, audience and route.
Treat a missing basis as `source gap:`, never as a default exclusion; the fixed Zensical boundary
still keeps raw `catalog/` and `external/` outside `site-source/`. **Done when:** all seven
contract headings exist in order and no placeholder is silently guessed.

The root `glossary.md` is staged into `site-source/` and publishes as `glossary.md` there. The
plan still records the generated abbreviation snippet the site layer consumes, its source
hash/provenance, and the known term used by rendered QA; it never assigns authors a second
editable term list. Raw `catalog/` and `external/` homes are always `do not publish` to this
Zensical site; inventory them by metadata and source counts, without loading their full bodies
merely to plan the site. Facts needed by readers must be curated into an allowlisted reader-facing
page with lineage.

The publication map is a closed coverage contract **at document granularity**. Every publishable
`.md` is either published or carries its own explicit `do not publish` row with a reason — a home-level
row cannot stand in for the documents inside it. The denominator for coverage is every publishable
document, not the map's row count and not the pages selected for this pass. Every derived route must state its source and
transformation, and every internal `/docs/` link in the published projection must resolve
to a mapped published route or be removed from the projection.

For `catalog/`, the inventory records the source count and provenance, but the raw home receives no
Zensical route or `nav` entry. If a reader-facing derivation is proposed, record its selected fields,
stable identifiers and source-ledger link; record secrets, operational dumps and entries without
lineage as explicit gaps or out-of-scope rows. The plan must state that `catalog/` and `external/`
are excluded from `site-source/`.

For generator capabilities, add a **Capability register** after the execution contract. Inventory
autorefs, API extraction, preview, metadata/facet search and provenance; for each row record its
prerequisite, enabled/disabled decision, evidence, risk, source gap and rendered test. Only rows
marked `enabled` are forwarded to `build`; disabled or unsupported capabilities remain documented
decisions and do not add dependencies.

An incremental run accepts `--desde <ref>` only after reading the previous plan and validating the
ref with Git. Record `ref`, UTC `timestamp`, source path, destination route and transformation for
each changed item. Without a readable previous plan, use the full run and report the fallback;
never infer deletions, renames or dependencies from a partial diff.

Safety rules: an invalid ref or missing prior plan falls back to a full inventory; a rename or
removal requires an explicit source→destination decision; and an unmapped dependency is retained as
`source gap:` rather than deleted. The plan must show these findings before any page or site-layer
write is authorized.

### 4. Present the plan and obtain one OK

Show the inventory findings, seven contracts, proposed files, page intents, extensions, the
editorial publication map, mandatory-surface denominator and source gaps as one reviewable plan.
Wait for the user's confirmation; revise the plan if it is declined.
**Done when:** one explicit OK is recorded, or the run stops with no file written.

### 5. Write the plan of record

Create `.quenching/documentation/plan.md` with the contracts, source ledger seeds, page assignments,
editorial publication map, open gaps and the accepted execution order. **Done when:** the plan exists
outside the docs home, contains all seven contracts and can be handed to `write` without re-reading
the raw inventory.

### 6. Self-check the record

Re-read the written plan, check that every destination has one intent and that every claim has an
allowed origin or `source gap:`. **Done when:** the record is internally consistent and the report
lists its path, assignments and remaining gaps.

## Invariants to never violate

- Never write a documentation page in this pass.
- Never fabricate a fact, source, statistic, quote, title or destination.
- Never infer that a home is internal or public from its name; the editorial map is the only publication boundary.
- Keep the plan at `.quenching/documentation/plan.md`, never under `/docs/`.
- A declined OK leaves the target unchanged.
