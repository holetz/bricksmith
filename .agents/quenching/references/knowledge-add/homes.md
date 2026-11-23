# Homes — routing table + the index procedure

**This file is the single owner of the insert procedure** — stamp (§The frontmatter stamp) →
index (§Updating `index.md`) → glossary (§Enriching the glossary) → self-check (§Self-check).
The full home boundaries and tree live in
`.agents/quenching/references/knowledge-align/taxonomy.md`.

## Contents

`.agents/quenching/bin/cq components read .agents/quenching/references/knowledge-add/homes.md` returns the heading
index; `--sections <name>` addresses one.

## Classification — one question decides the home

<!-- rules -->

Ask **"what IS this, relative to us?"**:

| The information is… | Home | `type` | Mold (`.agents/quenching/assets/templates/…`) | Path shape |
| --- | --- | --- | --- | --- |
| a rule for **how WE build** (proven, or agreed-but-unproven) | `standards/<subject>/` | `standard` | `standard-front.md` | `<subject>/<concept>.md` |
| **direction** for an area (no deadline) | `vision/` | `vision` | `vision/area.md` | `<area>.md` |
| the **history** behind a rule (measurement, dated evidence, spec chain) | `decisions/` | `decision` | `concept-front.md` | `NNNN-<slug>.md` |
| a **how-to / task recipe** (product usage) | `how-to/` | `how-to` | `concept-front.md` | `how-to/<slug>.md` |
| a **tutorial** (learning-oriented) | `tutorials/` | `tutorial` | `concept-front.md` | `tutorials/<slug>.md` |
| **documentation** (site page, the reader's why — business rules included; the Diátaxis explanation quadrant) | `documentation/` | `documentation` | `concept-front.md` | `<slug>.md` |
| a **notice already sent** to an audience (incident, change, release) — a record, not a rule | `communications/<year>/` | `communication` | — (the notice's own flow owns the body) | `<aaaa-mm-dd>-<slug>.md` |
| **this repository's own manual** (commands, automation, layout) | `project/` | `project` | `concept-front.md` | `<slug>.md` |
| **generic understanding we hold** (concept / explanation / learning) | `concepts/<subject>/` | `concept` | `concept-front.md` | `<subject>/<slug>.md` |
| a **fact about an external** tool/lib/regulation | `external/{tools,libraries,regulations}/` | `external` | `concept-front.md` | `<slug>.md` |
| **our data** — a system / schema / table | `catalog/<system>/…` | `system`/`schema`/`table` | `catalog/{system,schema,table}.md` | see below |
| a **regulation PDF / binary we consume** (extract) | `external/regulations/` | `sidecar` | `sidecar.md` | `<slug>.md` (+ `binary:`) |

### Boundary tie-breakers
- **standards vs external:** "how **WE** do it" (standards) vs "a fact about what **WE
  CONSUME**" (external). Our implementation of a regulation is a `standard`; the regulation
  itself is an `external` sidecar.
- **concepts vs its neighbors:** `concepts/` is **generic understanding** (a concept,
  glossary, mental model, learning) — non-binding. A binding rule for how we build is a
  `standard`; a fact about a **named** external dependency is `external`; a set of steps for using the
  product is a `documentation` how-to. When understanding hardens into a rule, it distills
  into `standards/` and leaves
  `concepts/`.
- **documentation vs concepts:** a **published-site page** (narrative, for a human reading
  the docs) is a reader-facing quadrant; **internal team understanding** (mental model, learning,
  glossary) is `concepts/`.
- **documentation vs communications:** a page that explains a rule or a why the reader keeps
  coming back to is `documentation/`; a notice that was **sent once** and is kept as the record of
  what the audience was told is `communications/`. The notice is not edited afterwards — a
  correction is a new notice.
- **standards & agreed rules:** a **proven, current** rule for how we build is a `standard`
  (`authority: current`); an **agreed-but-not-yet-proven** rule is a `standard` with
  `authority: background` until proven. There is **no separate decision home** — a decision's
  rationale and considered alternatives are captured in a plan's `design.md` while
  the change is active, and its durable outcome lands as a `standard` at archive time.
- **spec vs vision:** a **parked unit of work** is a **spec** and does **not** belong in this
  bundle at all — it lives at `specs/plans/`, outside it, so route the capture to
  `/quenching-specs create`. A **settled direction** with no deadline is a `vision` and stays here. The
  full `specs/` ↔ `knowledge/` boundary is owned once by
  [`specs-develop/spec-driven.md`](.agents/quenching/references/specs-develop/spec-driven.md)
  §Boundary.
- **standards vs catalog:** the *rule* for modeling data (grain/keys) is a `standard`
  (`data-modeling/`); the *actual tables* are `catalog/`.
- **code vs naming (inside standards):** `code/` governs **symbols**; `naming/` governs
  **data** (tables/columns).

### Catalog path shapes
- System access card → `catalog/<system>/access.md` (`type: system`); its sibling
  `catalog/<system>/index.md` is the reserved listing.
- Consolidated schema → `catalog/<system>/<catalog>/<schema>.md` (`type: schema`).
- Detailed table → `catalog/<system>/<catalog>/<schema>/<table>.md` (`type: table`) — only when
  descriptions exist; otherwise leave the table as a row in the consolidated `<schema>.md`.

## The frontmatter stamp

<!-- rules -->

Fill from the mold; every concept gets:

```yaml
type: <from the table above — non-empty>
title: <short name>
description: <one sentence — the "Covers" cell an index renders>
resource: <derived — never invented; standards a comma-separated GLOB SET of what the doc governs (`*`/`**` only, repo-root-relative), catalog/reference the asset URI>
tags: [<...>]
generated: { by: human:<id>, at: <today, ISO 8601 UTC> }   # or process:/<producer>/<version>
audience: both | agent | human       # canonical English enum
authority: current | background      # unproven standard ⇒ background (anti-fabrication)
source: <origin/author>
maintainer: <owner>
```

**Not in the mold, deliberately:** `source_uri:` — written by `/quenching-knowledge import` alone, never invented
by the commands citing this block, and simply absent on a doc with no external origin. Its
contract is [sources.md](.agents/quenching/references/knowledge-import/sources.md)
§Attribution.

## Updating `index.md` (the listing)

<!-- rules -->

- Add `* [<title>](<relative-path>.md) — <description>` under the right section. Keep links
  relative within the home, absolute (`/docs/...`) across homes.
- If the doc **creates a new folder**, create that folder's `index.md` too (a frontmatter-free
  listing) and link it from the parent — a folder of concepts without one is a `dir-no-index`
  gap, and an unlisted doc is an `index-orphan`.
- **Never** add frontmatter to an `index.md` (the root's `okf_version` is the only exception,
  and `/quenching-knowledge align` owns it).
- **`standards/index.md`** has a DERIVED zone: rebuild only what is between
  `<!-- BEGIN GENERATED -->` / `<!-- END GENERATED -->` by scanning `standards/**/*.md`
  (`title`/`description`/`generated.at` or legacy `timestamp`/`type`), grouped by subject
  subfolder. Never hand-edit
  inside the markers.

## Enriching the glossary (tail step, every capture)

<!-- rules -->

The bundle root ships one fixed file, [`glossary.md`](.agents/quenching/assets/knowledge/glossary.md):
the repo's A–Z term lookup, a flat alphabetical bullet list in the same syntax every
`index.md` uses (the one deliberate exception to "one concept per file") — `* [<Term>]
(<path>.md) — <one-sentence definition>` when a concept doc exists, or `* **<Term>** —
<one-sentence definition>` when it doesn't (an **unlinked entry is a valid, permanent
state**, not a defect). After a capture lands, **check whether the concept introduced a
term that belongs in the glossary** and, if so, enrich it:

- **When to add an entry.** The doc names a repo-specific word, acronym, or piece of
  jargon a newcomer would not know (a domain entity, an internal codename, a term of
  art). Skip generic English and terms already listed.
- **How to add it (MERGE, never clobber).** Insert the term in **alphabetical** position,
  in the linked bullet form, pointing at the concept doc you just wrote (`/docs/<path>.md`,
  absolute across homes). If an entry for the term already exists, sharpen its definition
  or add the link — never overwrite a filled definition or a filled link.
- **Only the entry.** The depth stays in the concept doc; the glossary points to it. Do not
  touch `concepts/index.md` for this — the glossary is already listed there.

## Self-check before finishing

<!-- rules -->

Apply `.agents/quenching/references/knowledge-align/conformance.md`: the new concept has parseable frontmatter +
a non-empty `type`; every `index.md` you touched is still frontmatter-free; the new doc's folder
has an `index.md` that **links** it (no `dir-no-index`, no `index-orphan`, no
`index-broken-link`).
