---
type: standard          # OKF concept type — always `standard`, non-empty (subject is carried by the file PATH)
title: <short title of the standard>
description: <one sentence — WHAT this standard covers; the "Covers" cell the derived index renders>
resource: <repo scope this standard governs — path / glob / FQN of the code it describes, DERIVED from the doc's file:line anchors; never empty or self-pointing>
tags: [<...>]
generated: { by: human:<author>, at: <ISO 8601 UTC — e.g. 2026-07-06T00:00:00Z> }
audience: both          # both (human+LLM) | agent (LLM-first norm) — standards are an agent-facing surface
authority: current      # current (de-facto proven contract) | background (proposal/gap, not yet followed)
source: <author/origin>
maintainer: <owner>
---

<Standard body. Anchor every rule to `file:line` evidence — that is what makes `resource`
derivable and non-fabricated. Favor structural markdown (headings, lists, tables).>

<!-- MOLD (quenching · standards frontmatter — do NOT copy this note into the doc):
     Every field above is required for a `/docs/standards/**` doc (stricter than the generic
     concept-front). `resource` is the OKF "underlying asset" = the repo scope the standard
     governs, DERIVED from the doc's file:line anchors, NEVER invented. An empty or
     self-pointing `resource` is conformance theater (a standard that governs nothing is not a
     standard). STAMP = MERGE, NEVER CLOBBER; enum values stay canonical English
     (`authority: current`, never `vigente`).

     Bundle-level OKF constructs live at the standards ROOT, not per doc:
       • standards/index.md — the reserved listing (its "Current docs" tables are DERIVED
         between the BEGIN/END GENERATED markers). -->
