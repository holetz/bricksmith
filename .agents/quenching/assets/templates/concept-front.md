---
type: <REQUIRED — the concept kind; pick the home's type from references/homes.md, e.g. standard|vision|documentation|knowledge|reference|system|schema|table|sidecar>
title: <short title of the doc>
description: <one sentence — WHAT this doc covers; the "Covers" cell an index renders>
resource: <URI / glob / FQN of the asset this concept describes — never empty/self-pointing>
tags: [<cross-cutting-tag>, <...>]
generated: { by: human:<author>, at: <ISO 8601 UTC — e.g. 2026-07-06T00:00:00Z> }
audience: both          # both (human+LLM) | agent (LLM-first) | human (human-only) — CANONICAL English enum, never localized
authority: current      # current (source-of-truth/contract) | background (background/historical) — CANONICAL English enum, never localized
source: <origin/author — person, team, external standard>
maintainer: <owner who keeps it alive (may = source)>
---

<Doc body. Favor STRUCTURAL markdown — headings, lists, tables (OKF SHOULD).>

<!-- MOLD (quenching · generic concept frontmatter — do NOT copy this note into the doc):
     The OKF core requires `type` non-empty on every concept doc; `title`/`description`/
     `resource`/`generated` are OKF-recommended; `audience`/`authority`/`source`/`maintainer`
     are the method's extra keys (canonical English enums — a single `grep 'authority: current'`
     finds every contract across every repo).

     OKF v0.2 optional families, when the evidence exists: `verified: { by, at }` (who confirmed
     the content), `status: draft|stable|deprecated` (absent = stable), `stale_after: <ISO 8601>`,
     and `sources:` (a list of `{ id, resource, ... }` for provenance; a body `# Citations` list
     is the v0.1 form). The actor convention is `human:<id>`, `process:<id>` or
     `<producer>/<version>`.

     STAMP = MERGE, NEVER CLOBBER: fill a MISSING key, preserve a filled one and any third-party
     key (OKF consumers, a legacy `status:`, site generators). Field migration when aligning a
     legacy doc: `summary:` → `description:`, `updated:` → `generated.at` when the actor is known
     (otherwise keep a legacy `timestamp` — a v0.2 consumer reads it as the fallback). Identity is
     the file PATH (kebab-case, one concept per file); the folder carries the subject, so the
     filename does not repeat it. -->
