---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Write, Edit, AskUserQuestion
---

# /quenching-knowledge add — add new knowledge, OKF-conformant

**Input**: `$ARGUMENTS` (the piece of information to file — a standard, catalog table, announcement, external asset, …).

Files one new piece of knowledge into the canonical OKF bundle so it lands in the right home
with a complete stamp. Assumes the bundle already exists (run `/quenching-knowledge align` first if not).
The routing table and procedure are in [knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md). The home boundaries, `type`
vocabulary, and conformance rules are shared with `/quenching-knowledge align`
(`.agents/quenching/references/knowledge-align/taxonomy.md`,
`.agents/quenching/references/knowledge-align/conformance.md`).

## Doctrine

- **One concept per file; the path is the identity.** The folder carries the subject, so the
  filename does not repeat it (`naming/columns.md`, not `naming/naming-columns.md`).
  Kebab-case, no accents. If the subject already holds a **cluster** of siblings, file the new
  concept **inside that subfolder** (`code/symbol-naming/enums.md`), never as a `symbol-naming-*`
  flat file — extend the folder the aligner would have folded.
- **English slug on technical homes; identifier slugs verbatim.** The file slug is canonical
  English on `standards/`·`vision/`·`documentation/`·`external/` (as are
  folder names, frontmatter keys, and enum values). **Exception:** an identifier-derived name is
  verbatim — a catalog `<schema>`/`<table>` mirrors the real object, `external/repositories/<repo>`
  the real repo. Body prose may be the repo's language.
- **`type` mandatory; `resource` derived, never invented.** For a standard, `resource` is the
  **glob set** naming what the doc governs — comma-separated, `*`/`**` only, repo-root-relative;
  for catalog/external, the asset URI. Empty is disallowed, and so is self-pointing
  (`resource-self`) — except a bundle-level aggregate like `glossary.md`. A glob says
  *what this doc governs* and is what the staleness check reads; a `file:line` says only where a
  rule was written, and rots on the next insertion above it.
- **Anti-fabrication.** A standard that is **not yet proven** in the code enters as
  `authority: background` (a proposal), never `authority: current`. Never invent evidence.
- **MERGE, never clobber.** If the target file exists, fill missing keys, preserve filled and
  third-party ones.
- **Keep the listing honest.** Every insert updates the folder's `index.md`.
- **Feed the glossary.** A capture that introduces a repo-specific term ends by adding its entry
  to `glossary.md`.

## Workflow

### 1. Classify → home + `type` + mold
Apply the boundary rules ([knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md)):
`standards` = "how **we** do it" · `external` = "what we **consume**" · `catalog` = "our
**data**" · `documentation` = a product-facing explanation (the reader's why, business rules included) or announcement. Pick the home, its
`type`, and the matching mold. **Done when:** the home, type, and mold are named.

### 2. Determine identity (path)
Concept ID = the path without `.md`. Place it under the home's subject folder; one concept per
file; kebab-case; **English slug on the technical homes** (identifier-derived names verbatim, see
Doctrine). If the subject already carries a **cluster subfolder**, nest the new doc inside it. For
homes with a fixed shape, follow it
(`catalog/<system>/<catalog>/<schema>/<table>.md`). **Done when:** the canonical path is fixed.

### 3. Fill the mold
Copy the matching mold from `.agents/quenching/assets/templates/` and complete the frontmatter:
non-empty `type` + the OKF recommended fields (`title`/`description`/`resource`/`generated`) +
the method labels (`audience`/`authority`/`source`/`maintainer`). Derive `resource`; an
unproven standard is `authority: background`. **Done when:** the stamp is complete and
evidence-backed.

### 4. Write the concept doc
Write the file with `Write`. Favor structural markdown (headings, lists, tables). Cross-home
links absolute (`/docs/...`); within-home links relative. **Done when:** the concept file is
written without overwriting filled content.

### 5. Update the folder's `index.md`
Add a bullet-link with the doc's `description` (`* [<title>](<rel-path>.md) — <description>`).
For `standards/**`, the layer index's **Current docs** tables are a DERIVED zone — run
`.agents/quenching/bin/cq knowledge listing --write` to regenerate only what is between `<!-- BEGIN GENERATED -->` /
`<!-- END GENERATED -->` from disk (`--check` proves it fresh); never hand-edit inside the
markers. Never add frontmatter to an `index.md`. **Done when:** every touched
index lists the new file and remains frontmatter-free.

### 6. Enrich the glossary
If the new concept introduced a **repo-specific term**, add or sharpen its entry in
`glossary.md` per **Enriching the glossary** in [knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md)
(`/quenching-knowledge define` handles one term; `/quenching-knowledge glossary-backfill` handles a bulk sweep).
**Done when:** every introduced repo-specific term is linked or explicitly left for a glossary command.

### 7. Self-check against the conformance core
Verify every file you touched against
[knowledge-align/conformance.md](.agents/quenching/references/knowledge-align/conformance.md).
**Done when:** the conformance result and touched-file list are reported.

## Special cases
- **Standard** → mold `standard-front.md`; anchor rules to the code they govern; derive
  `resource` as a glob set;
  consider the subject's **candidate sub-standards** and record any deferral in that subject's
  coverage ledger. An **agreed-but-not-yet-proven** rule enters as `authority: background`
  (a proposal), graduating to `authority: current` once proven — there is no separate ADR home.
- **Catalog table with descriptions** → detailed `table.md`; without → keep it a row in the
  consolidated `<schema>.md`. Never create an empty detailed page.
- **Binary (a regulation PDF we consume)** → write a `sidecar.md` extract under
  `external/regulations/` (`type: sidecar`), never ingest the binary.

## Invariants

- One requested knowledge item gets one canonical path and one owning home.
- Existing frontmatter and authored content are merged; filled values are never clobbered.
- `type`, `resource`, `generated`, authority and source remain evidence-backed and are never invented.
- Every new document is listed by its owning index, and introduced repository terms are handed to
  the glossary workflow.
- The conformance core is the closing check; this command does not call a bulk align or import path.
