# Canonical taxonomy — the tree, homes, `type` vocabulary, boundaries

The single source of the tree `/quenching-knowledge align` installs and `/quenching-knowledge add` files into. Every repo
**converges to this same tree of identical names**; a variant name is a non-convergence smell
(→ [migration.md](.agents/quenching/references/knowledge-align/migration.md)).

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## The canonical tree (locked)

```
knowledge/                     # OKF bundle root
  index.md                     # ONLY index.md with frontmatter — okf_version: "0.2" + home listing
  glossary.md                  # the repo's A–Z term lookup — bundle-root file, not inside a home
  standards/                   # "how WE do it" (current) — a conformant sub-bundle
    index.md                   # reserved listing + DERIVED "Current docs" tables (BEGIN/END GENERATED)
    AGENTS.md                  # thin agent-facing pointer (auto-loaded; harness file, not a concept)
    agents/ architecture/ code/ naming/ data-modeling/ ci-cd/ workflows/ mlops/ quality/ platform/
                               #   each: index.md + one standard per file (type: standard)
                               #   agents/ = how we INSTRUCT agents, not agent definitions
  catalog/                     # our data — <system>/{index.md, access.md} · <catalog>/<schema>.md · <schema>/<table>.md
  vision/                      # direction by area — <area>.md (type: vision)
  tutorials/                   # learning-oriented pages (type: tutorial)
  how-to/                      # task recipes (type: how-to)
  documentation/               # why it works this way, for the site's reader (type: documentation)
  communications/              # notices already sent to an audience — <year>/<date>-<slug>.md (type: communication)
  project/                     # the manual for THIS repository (type: project)
  concepts/                    # generic knowledge we hold — subject subfolders (type: concept)
  external/                    # what we consume — tools/ libraries/ regulations/ (type: external; PDFs via sidecar)
```

## The `type` vocabulary (the greppable signature)

`type` is mandatory and non-empty on every concept doc. Fixed per home:

| Home | `type` | Identity (path) |
| --- | --- | --- |
| `standards/**` | `standard` | `standards/<subject>/<concept>.md` |
| `catalog/<system>/access.md` | `system` | the access card |
| `catalog/**/<schema>.md` | `schema` | consolidated index |
| `catalog/**/<schema>/<table>.md` | `table` | detailed page |
| `vision/` | `vision` | `<area>.md` |
| `tutorials/` | `tutorial` | learning-oriented pages |
| `how-to/` | `how-to` | one recipe per file |
| `documentation/` | `documentation` | the site's reader-facing why |
| `communications/` | `communication` | the notice already sent, dated — `<year>/<aaaa-mm-dd>-<slug>.md` |
| `project/` | `project` | this repo's own manual (commands, automation, layout) |
| `concepts/` | `concept` | subject subfolders |
| `external/` | `external` | `tools/`·`libraries/`·`regulations/` |
| `external/regulations/` (extracts) | `sidecar` | one per binary |

Reserved `index.md` carries **no** `type`; `AGENTS.md` (and legacy `AGENTS.md`) is exempt. `log.md` is
reserved too but **retired** — the tree above no longer grows one, and nothing checks one that
survived an earlier alignment. The v0.2 `Attested Computation` type lives under `concepts/` (or a
subject subfolder of it) and is the one fixed type outside the per-home table, because a
computation is generic knowledge with an executable contract, not a home of its own.

## Boundary rules (memorable summary)

- An **agreed-but-unproven rule** for how we build is a `standard` with `authority: background`;
  it graduates to `authority: current` once proven (there is no separate decision home).
- **`patterns` is not a silo** — it dissolves into `standards/architecture/`.

## The homes, one by one

- **`standards/`** — current/active contracts, one standard per file, subject subfolders.
  `type: standard` + a `resource:` **derived as a glob set of what the doc governs** (never
  invented).
  Each subject carries a **candidate sub-standards** catalog and a **coverage/deferral
  ledger** — a consideration checklist, evidence-gated generation, recorded deferral.
  Internal boundary: `code/` governs **symbols**, `naming/` governs **data**.
- **`vision/`** — direction segmented by area (`<area>.md`, `type: vision`), **no deadline**.
  A raw unit of work toward it → a spec in `specs/plans/` (**outside** this OKF bundle —
  see `/quenching-specs create`); what became reality → `standards/`.
- **The reader-facing quadrants** — `tutorials/` (`type: tutorial`), `how-to/` (`type: how-to`,
  absorbs the former `guides/`), `documentation/` (`type: documentation`) and `project/`
  (`type: project`, this repository's own manual — commands, automation, layout).
  `documentation/` covers the Diátaxis **explanation** quadrant — the reader's why, business
  rules included. **Declared divergence from OKF v0.2**, which names this home `explanation/`: the
  name is the one readers outside engineering look for (2026-11-04), and `explanation/` is now a
  retired home name `okf-legacy-home` reports. They are homes
  at the bundle root, not subfolders of a wrapper. The Zensical input is the generated bounded
  `site-source/` sibling; the complete bundle is never its `docs_dir`. `reference/`
  is deliberately NOT among them — it is a retired home name that `okf-legacy-home` still claims.
  Boundary: a published-site page → the fitting quadrant; internal team understanding →
  `concepts/`; a current contract → `standards/`; a notice already sent to an audience →
  `communications/`. `audience: human`, `authority: current` by default.
- **`communications/`** — what the team told an audience, archived (`type: communication`): one
  page per notice, dated in the path (`<year>/<aaaa-mm-dd>-<slug>.md`) and in the frontmatter, the
  body exactly as it was sent, and a reserved `index.md` per folder — the home and each year — so
  the notices stay listable. It is a **record**, not `documentation/` (the rule explained) and not
  a report kept in a work dossier. `resource` is optional here, the one reader-facing exception:
  an operational notice often has no asset behind it, and where a dossier or a spec originated the
  notice that origin is the resource. `publicacao` — not `status`, which OKF reserves for
  `draft|stable|deprecated` — carries the publication state. A correction is a **new** notice,
  never an edit to one already sent. `audience: human`, `authority: current` by default.
- **`concepts/`** — generic, cross-cutting understanding the team holds (`type: concept`):
  domain concepts, glossaries, mental models, explanations, learnings — the Diátaxis
  **explanation** quadrant raised to a home, subject subfolders welcome. Non-binding and
  usually `authority: background`. Boundary: it is **not** a contract (→ `standards/`), **not**
  a fact about a named external asset we consume (→ `external/`), and **not** a procedure
  (→ `how-to/`). If understanding hardens into a rule for how we build, it distills into
  `standards/` and leaves. Ships **one fixed file** — the bundle-root `glossary.md`, the repo's
  A–Z term lookup (a flat, alphabetically sorted bullet list in the same syntax every `index.md`
  uses — the one deliberate exception to "one concept per file", and the one place an
  unlinked entry is a valid permanent state). `/quenching-knowledge align` installs the seed.
- **`external/`** — external facts we consume (`type: external`, usually
  `authority: background`); a regulation's PDF lives here via a **sidecar**, while *our*
  implementation of it lives in `standards/`.
- **`catalog/`** — the data: `system → catalog → schema → table`. `<system>/index.md` is a
  listing; `<system>/access.md` is the access card (`type: system`, secret by reference).
  Generated × curated kept separate ("X defines Y").

## Authoring conventions (all homes)

- **The folder carries the subject — the filename does not repeat it.** In `naming/`, the doc
  is `columns.md`, not `naming-columns.md`. Kebab-case, no accents, one concept per file. **The
  one exception is the bundle-root `glossary.md`** — a glossary is inherently a multi-term aggregate.
- **Folders over prefix-clusters — favor a folder when it earns its keep.** A run of sibling
  files sharing a subject prefix (`nomenclatura-classes.md`, `nomenclatura-funcoes.md`,
  `nomenclatura-modulos.md`, …) is the same "filename repeats the subject" smell one level up:
  the prefix is the folder. Fold the cluster into a subfolder named for the subject
  (`symbol-naming/{classes,functions,modules,…}.md`) with its own `index.md`. Keep files flat
  when the folder would be ceremony. Rule of thumb: **≥3 siblings on a coherent axis → folder;
  ≤2 → judge by whether the axis is real and expected to grow.**
- **Structure is canonical English; content may be local.** Folder names **and file slugs**,
  frontmatter keys, enum values, and the `type` vocabulary are English (cross-repo greppable) —
  `nomenclatura-variaveis.md` → `naming/variables.md`. Which language the body prose is
  written in is owned by `standards/agents/communication.md` in this same
  bundle. **Identifier-derived slugs are verbatim, never translated:**
  a catalog `<schema>`/`<table>` mirrors the real object, `external/repositories/<repo>` the
  real repo — translating them would break the greppable tie to the asset.
- **Links:** relative **within** a home; absolute from the bundle root (`/docs/...`) when
  leaving for another home — so cross-links survive a home move/migration.
- A directory that holds concept docs carries a reserved, frontmatter-free `index.md` listing
  its real children (the validator's `dir-no-index`/`index-broken-link`/`index-orphan` checks
  enforce this deterministically).
- **Content × home:** homes are by **subject** (subject-first); content **within** a home
  follows **Diátaxis** (tutorial / how-to / reference / explanation).
