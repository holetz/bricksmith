---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*), Write, Edit, AskUserQuestion
---

# /quenching-knowledge define — add/refine one glossary term

**Input**: `$ARGUMENTS` (the term to add or refine, and optionally its one-sentence definition).

Files ONE term into the canonical OKF bundle's fixed glossary,
[`/docs/glossary.md`](/docs/glossary.md). For a whole-bundle bulk
backfill instead of one term, see `/quenching-knowledge glossary-backfill`.

## Workflow

### 1. Locate the glossary
Find `/docs/glossary.md` — the bundle root is the fixed `/docs/` convention. If the
`concepts/` home or the glossary seed is **missing**, stop and offer
`/quenching-knowledge align` to install the skeleton, then stop; do not resume in the same run. Read the
current list so you can place the entry and detect an existing one. **Done when:** the glossary path
and baseline are resolved, or the missing-bundle handoff is reported.

### 2. Confirm the term belongs
Confirm the term is **repo-specific** (not generic English) and not already listed. If it is
already present, this becomes a **refine**. If it needs a full explanation rather
than a one-liner, say so and hand off to `/quenching-knowledge learn` for the concept doc — then add the
entry here pointing at it. **Done when:** the term is classified as add or refine, or routed away.

### 3. Derive the link
Look for the concept doc that defines the term (`Grep`/`Glob` `/docs/**`). If one exists, link it
(`/docs/<path>.md`). If none exists, leave the entry unlinked — never invent a target; optionally
note that a `/quenching-knowledge learn` capture would give the term a home. **Done when:** the link
is existing, or the entry is explicitly unlinked.

### 4. Write the entry (MERGE, alphabetical)
Edit `glossary.md`: insert `* [<Term>](<path>.md) — <one-sentence meaning in this
repo's sense>` (or `* **<Term>** — <one-sentence meaning>` if unlinked) in its **alphabetical**
position under `## Terms`. If an entry for the term exists, add a missing link or sharpen the
definition — never clobber a filled definition or link. Keep the list sorted. Definitions may
follow the repo's language; the term itself is verbatim as the repo writes it. **Done when:** the
entry is written or the run stopped with its reason.

### 5. Self-check against the conformance core
Verify every file you touched against
[knowledge-align/conformance.md](.agents/quenching/references/knowledge-align/conformance.md),
plus this skill's own gate: the list is still sorted and its links resolve. **Done when:** the
entry is written or the run stopped with its reason.

## Invariants to never violate

- Never fold a full explanation into a glossary entry — one sentence + a link; depth goes to
  a `concepts/` concept doc.
- Never invent a link target; link only a doc that exists, else leave the entry unlinked.
- Never clobber a filled definition or link on merge; never leave the list unsorted.
- Never create a per-term file or add a term outside `glossary.md`; never give the
  glossary frontmatter beyond its shipped stamp, and never add frontmatter to `concepts/index.md`.
