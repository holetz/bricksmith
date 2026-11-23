---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Write, Edit, AskUserQuestion
---

# /quenching-knowledge learn — capture generic knowledge, OKF-conformant

**Input**: `$ARGUMENTS` (the generic understanding to capture — a concept, mental model, explanation, or learning).

Files one piece of understanding the human gives you into the canonical OKF bundle's
[`concepts/`](.agents/quenching/assets/knowledge/concepts/index.md) home. Assumes the bundle already exists (run `/quenching-knowledge align` first if not). The home boundaries are shared with `/quenching-knowledge add`
([knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md)); the `type` vocabulary
and conformance rules with `/quenching-knowledge align`
([knowledge-align/taxonomy.md](.agents/quenching/references/knowledge-align/taxonomy.md),
[knowledge-align/conformance.md](.agents/quenching/references/knowledge-align/conformance.md)).

## Workflow

### 1. Confirm the home
Read the knowledge the human stated. Confirm it is **generic understanding** (not a contract /
decision / procedure / external-asset fact). If it belongs in another home, say so and hand off
to `/quenching-knowledge add`. Otherwise proceed under `concepts/`. **Done when:** the home is
confirmed or the item is routed away.

### 2. Search for what already says it
Grep `/docs/concepts/` and `/docs/standards/` for the concept's key terms and their synonyms, then
read each hit that could hold the same understanding. Decide one verdict and show it to the human
with the paths you read:

| Verdict | When | Then |
| --- | --- | --- |
| **New** | no doc carries it | continue at step 3 |
| **Absorb into `<path>`** | an existing doc carries the subject and lacks this piece | edit that doc in place — a section or a paragraph, never a duplicate file — and continue at step 7 |
| **Already captured** | an existing doc already says it | stop and cite the doc |
| **Route away** | the hit is a standard and this piece changes what it binds | hand off to `/quenching-knowledge add` |

A doc that holds the subject but contradicts the new piece is not an absorb: report the
contradiction and let the human decide. **Done when:** the verdict and the paths it rests on are
stated, and the human confirmed any absorb before the edit.

### 3. Determine identity (path)
Pick (or create) the `concepts/<subject>/` subfolder for the concept — e.g. `domain/`,
`glossary/`, `concepts/`. Concept ID = the path without `.md`; one concept per file; kebab-case;
**English slug**. If the subject already carries a cluster subfolder, nest inside it. If the
concept creates a **new** subfolder, that subfolder needs its own `index.md`. **Done when:** the
canonical concept path and any new subject index are fixed.

### 4. Fill the mold
Copy `.agents/quenching/assets/templates/concept-front.md` and complete the frontmatter:
`type: concept` + the OKF recommended fields (`title`/`description`/`resource`/`generated`)
+ the method labels (`audience` — usually `both`; `authority` — usually `background`;
`source` — who supplied it; `maintainer`). Derive `resource`; set `generated` to
`{ by: human:<id>, at: <ISO 8601 UTC> }` (or a `process:`/`<producer>/<version>` actor when
that is what produced it).
**Done when:** the complete stamp is evidence-backed.

### 5. Write the concept doc
Write the file with `Write`. Favor structural markdown (headings, lists, tables). Cross-home
links absolute (`/docs/...`); within-home links relative. Body prose MAY be the repo's language.
**Done when:** the concept body is written without overwriting filled content.

### 6. Update `concepts/index.md`
Add a bullet-link with the doc's `description`
(`* [<title>](<rel-path>.md) — <description>`) under the right subject. If you created a new
subfolder, create its frontmatter-free `index.md` and link it from `concepts/index.md`. Never
add frontmatter to an `index.md`. **Done when:** the concept and all required index links are in
place.

### 7. Enrich the glossary
If the concept introduced a **repo-specific term**, add or sharpen its entry in
`glossary.md` per **Enriching the glossary** in
[knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md) (the tail step
every capture runs; on-demand counterpart `/quenching-knowledge define`, bulk counterpart
`/quenching-knowledge glossary-backfill`). **Done when:** every repo-specific term is linked or
explicitly left for a glossary command.

### 8. Self-check against the conformance core
Verify every file you touched against
[knowledge-align/conformance.md](.agents/quenching/references/knowledge-align/conformance.md) —
the same checks `.agents/quenching/bin/cq knowledge validate` machine-verifies. **Done when:** validation and the touched
file list are reported.

## Invariants to never violate

- Never write a second doc for understanding an existing doc already carries — absorb it there.
- Never file a **contract / decision / procedure / external-asset fact** under `concepts/` —
  route it to its home via `/quenching-knowledge add`.
- Never write an empty or self-pointing `resource`, and never fabricate a `source`.
- Never add frontmatter to an `index.md`; never leave a new subfolder without one.
- Never overwrite a filled key on merge; never leave the index un-updated.
- Never skip the glossary tail step when the concept names a repo-specific term — but never
  clobber a filled glossary entry, and never fold a term's full explanation into the glossary
  (it holds the one-liner + the link; the depth stays in the concept doc).
