---
okf_version: "0.2"
---

# `/docs/` — OKF knowledge bundle

The canonical knowledge tree of this repository, an **Open Knowledge Format (OKF v0.2)** bundle,
and the source from which its bounded documentation input is staged. Each **home** has a fixed name and a single purpose;
anyone moving between repositories that adopt this method finds the **same tree in the same
place**. This `index.md` is the bundle's front door and the site's home page — a reserved listing,
the only one that carries frontmatter, and only `okf_version`. Folder names and frontmatter keys
are canonical English kebab-case; all prose the agent authors follows the repo's declared
language — [standards/agents/communication.md](standards/agents/communication.md) owns that rule.

## Homes

* [standards/](standards/index.md) — how **WE** do it (current contracts/conventions), by subject; agreed-but-unproven rules sit here as `authority: background`
* [vision/](vision/index.md) — direction segmented by area, no deadline
* [decisions/](decisions/index.md) — the history behind a contract (measurements, dates, spec chain); standards keep only the rule
* [tutorials/](tutorials/index.md) — learning-oriented pages ("get it running")
* [how-to/](how-to/index.md) — task-oriented recipes for a reader with a goal
* [documentation/](documentation/index.md) — how and why, business rules included, for the site's reader (the Diátaxis explanation quadrant)
* [project/](project/index.md) — the manual for *this* repository: its commands, its automation, its layout
* [concepts/](concepts/index.md) — generic knowledge we hold (domain concepts, explanations, learnings); ships the fixed [glossary.md](glossary.md) term lookup
* [external/](external/index.md) — facts about what **WE CONSUME** (external, background)
* [catalog/](catalog/index.md) — our **data** / domain (`system/catalog/schema/table`)

## Boundaries (memorable summary)

- `standards/` = "how **WE** do it (current/active)"; an agreed-but-unproven rule sits here as `authority: background` (its history lives in `decisions/`).
- `concepts/` = "generic **understanding** we hold" (concepts/explanations; non-binding).
- `external/` = "facts about what **WE CONSUME** (external, background)".
- `catalog/` = "our **data** / domain".
- `tutorials/` `how-to/` `documentation/` `project/` = the **reader-facing** quadrants. A page you
  would publish for a human belongs to one of them; internal team understanding goes to
  `concepts/`, a current contract to `standards/`.
- The **task inbox** is the provider-owned `specs` front, **outside** this bundle.
- `patterns` **is not a silo** — it dissolves into `standards/architecture/`.

**Resolving a term.** Unfamiliar repo word, acronym, or codename? Look it up in the glossary
first — [glossary.md](glossary.md), the A–Z lookup (one entry per term, linked to its full doc
when one exists): `grep -i '<term>' docs/glossary.md`.

## This bundle feeds the site

The whole tree is the knowledge store, but it is not automatically a site. Zensical 0.0.57
processes every Markdown file below `docs_dir` and does not support `exclude_docs`, `draft_docs` or
`not_in_nav`. The plugin therefore stages the bounded `site-source/` sibling before each build;
only reader-facing and selected durable-knowledge homes enter it. `catalog/` and `external/` stay
in the bundle and never enter the Zensical source tree. The plugin ships a batteries-included
**Zensical** setup at the repo root (`zensical.toml`) — first installed by
`quenching-knowledge-align`, then owned by the
**documentation family**: `plan`, `write`, `review` and `build`, conducted end to end by
`produce`.

- `docs_dir = "site-source"` points at the generated sibling, kept at the repo root. Run
  `.agents/quenching/bin/cq knowledge site-source docs site-source --write` before a build and
  `.agents/quenching/bin/cq knowledge site-source docs site-source --check` to verify it. Never point Zensical at the
  complete bundle: nav omission is not an exclusion mechanism.
- Each reserved `index.md` doubles as its section's landing page (the theme's
  `navigation.indexes` feature) — no separate landing file needed.
- Navigation is the explicit `nav` list in `zensical.toml`, generated from the same publication
  allowlist by `.agents/quenching/bin/cq knowledge nav`; a title a human wrote there survives regeneration.
- The markdown stays **generator-neutral**: no theme components, no raw HTML with theme classes,
  no attribute lists. The bundle has to read as well on a Git host and in a `grep` as it does on
  the site.

The full contract (homes, types, migration doctrine, conformance) lives in the `quenching`
skills' `references/`.
