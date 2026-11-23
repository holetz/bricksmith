# `concepts/` — generic knowledge we hold

**Cross-cutting understanding the team carries** — domain concepts, glossaries,
mental models, explanations, learnings and background insight that inform the work
but are not a contract, a procedure, or a fact about a specific external asset. The
**Diátaxis "explanation" quadrant** raised to a home. Each doc carries `type: concept`.

**Boundary** (what lands here vs. its neighbors):

- vs. [standards/](/docs/standards/index.md) — standards are "how **WE** do it" (a
  current, code-derived **contract**); knowledge is "what we **understand**"
  (explanatory, non-binding). If it binds how code is written, it is a standard.
- vs. [external/](/docs/external/index.md) — reference is facts about a **specific
  external asset WE CONSUME** (a named tool / library / regulation); knowledge is
  **generic** understanding not tied to one consumed asset (a domain concept, a
  learning). If it documents a named dependency, it is reference.
- vs. the reader-facing quadrants [tutorials/](../tutorials/index.md),
  [how-to/](../how-to/index.md), [documentation/](../documentation/index.md) and
  [project/](../project/index.md) — those are the published, human-facing pages; knowledge is
  internal team understanding. If it is a page for the docs site, it belongs to a quadrant.
- vs. [vision/](/docs/vision/index.md) — vision is future **direction**; knowledge is
  present **understanding**.

## Fixed doc

* [Glossary](../glossary.md) — the repo's A–Z term lookup. **Resolve any unfamiliar
  repo term here first** (Ctrl-F / `grep`); it is the one file that ships with the
  bundle and the one deliberate exception to "one concept per file" (a flat, sorted
  bullet list — the same syntax every `index.md` uses — of term → one-line meaning
  → link to the full doc, or unlinked when none exists yet). Enriched by
  `quenching-knowledge-define` on demand, `quenching-knowledge-glossary-backfill` in bulk, and, as a tail
  step, by the other knowledge skills.

## How to organize

Which *other* knowledge exists and how it is grouped is **this repo's** decision —
beyond the glossary the skeleton ships no ready-made doc.

- **Prefer folder/subfolder structure by subject** (subject-first) — e.g. `domain/`,
  `glossary/`, `concepts/`. Standalone docs live as `.md` files directly here; each
  subfolder gets its own `index.md` listing.
- Subfolders by **subject** are welcome; a subfolder by **audience** is not.
- One concept per file, kebab-case English slug; the folder carries the subject, so
  the filename does not repeat it. `resource:` points to what the knowledge concerns
  (a domain, a code glob, a URL, an origin) — never empty or self-pointing.
- Default `authority: background` (explanatory, not a current contract); foundational
  understanding the team treats as settled may be `authority: current`.
