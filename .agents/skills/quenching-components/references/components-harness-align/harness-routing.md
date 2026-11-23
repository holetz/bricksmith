# Harness routing — from an AGENTS.md unit to its OKF home

How `/quenching-components harness-align` decides, for each unit of a harness file, whether it **stays** (harness-
operational) or **moves** into an OKF home (durable knowledge). Once a unit is routed to a home it
is filed exactly as `/quenching-knowledge add` would — see
[knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md) for the
`type`/mold/path shape and the index/log procedure, and
[knowledge-align/taxonomy.md](.agents/quenching/references/knowledge-align/taxonomy.md) for the
home boundaries.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## 1. What a harness file is

<!-- rules -->
`AGENTS.md` is the navigation pointer every harness auto-loads — the root `AGENTS.md`
repo-wide, a subfolder `AGENTS.md` only when working under its folder. the host 2.1.277+
reads `AGENTS.md` directly; `CLAUDE.md` is the legacy fallback of older hosts and is removed by
the migration, never created.
OKF-strict point 7 ([knowledge-align/okf-spec.md](.agents/quenching/references/knowledge-align/okf-spec.md))
says they are **not** OKF concepts, and the validator (`.agents/quenching/bin/cq knowledge validate`) **skips them entirely**
— including link checks (`index-broken-link` runs only on `index.md`). The target shape is the
shipped exemplar [docs/standards/AGENTS.md](.agents/quenching/assets/knowledge/standards/AGENTS.md)
("thin pointer, never a copy").

<!-- rationale -->
So an AGENTS.md that inlines a rule or lies about a link is **machine-invisible**: it escapes
validation, is invisible to anyone browsing `knowledge/`, and drifts from the real doc.

## 2. Content units

Parse each harness file into **units**, one verdict each:

- a **heading section** (a `##`/`###` and its body),
- a **fenced command block** (```` ``` ````),
- a **bullet run** (a contiguous list),
- a **paragraph**.

Capture each unit's text, its anchor (heading slug), and any links it carries. **Split a mixed
unit** — a section that pairs a build command (KEEP) with an architecture note (MOVE) becomes two
units.

## 3. The one decision rule

> **Commands and etiquette stay; knowledge moves.** Would the agent need this on **EVERY** task
> under this file's folder → **keep**. Only when working on **that subject** → **move + pointer**.
> A harness file is context tax paid on every turn; it earns each line.

## 4. Verdict table — unit kind → verdict → destination home (`type`)

| The unit is… | Verdict | Destination home (`type`) |
| --- | --- | --- |
| build / run / test / lint commands, env vars, ports | **KEEP** | stays in the harness file |
| agent etiquette, permissions, tool rules | **KEEP** | stays — *unless* it is a team-wide process rule proven beyond the agent ⇒ **MOVE** `standards/workflows/` (`standard`) |
| environment quirk needed every turn | **KEEP** | stays in the harness file |
| architecture description / module map | **MOVE** | `standards/architecture/` (`standard`; unproven ⇒ `authority: background`), or `concepts/<subject>/` (`concept`) if non-binding |
| coding / naming conventions | **MOVE** | `standards/code/` · `standards/naming/` (`standard`) |
| "we chose X because Y" — agreed / proven | **MOVE** | `standards/` (`standard`; agreed-but-unproven ⇒ `authority: background`, proven ⇒ `current`) — no separate decision home |
| roadmap / TODO / next-steps item (raw, unscoped) | **MOVE** | a spec in `specs/plans/` (via `/quenching-specs create`, outside the OKF bundle — unranked until `/quenching-specs triage` says otherwise), or `vision/` (`vision`) for settled direction with no deadline |
| step-by-step procedure / onboarding | **MOVE** | `how-to/` (`documentation`) |
| facts about an external tool / lib / service | **MOVE** | `external/{tools,libraries,regulations}/` (`external`) |
| domain concept / glossary term | **MOVE** | `concepts/<subject>/` (`concept`) |
| schema / table descriptions | **MOVE** | `catalog/…` (`schema` / `table`) |
| restates a fact an existing doc already holds | **DEDUPE** | cite the existing doc; cut + leave a pointer |
| contradicts an existing doc | **FLAG** | per-item: fix the doc, fix the file, or both — resolve, never auto-pick |
| secrets / credentials / personal notes / `CLAUDE.local.md` (legacy) | **UNROUTABLE** | stays + reported; secrets urged out-of-band, NEVER into shared `knowledge/` |
| no documentary home | **UNROUTABLE** | stays + reported |

**The ephemeral-writes declaration line is always KEEP.** Its membership test is a root-harness
line beginning `Ephemeral writes:` and carrying exactly one path plus this citation:
`Ephemeral writes: <path> — the contract is /docs/standards/agents/ephemeral-writes.md`.
Preserve it verbatim; never classify it MOVE or DEDUPE.

**Tie-breakers** are `/quenching-knowledge add`'s — see
[knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md).

## 5. Pointer-honesty checklist (the step-8 verify gate)

The validator won't check harness files, so this skill does:

- **Every link resolves** — each `[..](..)` target exists on disk (the file, the anchor).
- **Each pointer's one-liner matches its target** — the bullet describes what the doc really
  holds; no stale or aspirational description.
- **No moved fact is paraphrased inline** — a MOVED unit leaves the harness file entirely; only a
  citing pointer remains.
- **Only KEEP residue + pointers + the trailing harness comment remain** — no orphaned knowledge.
- **No frontmatter** — a harness file never carries frontmatter or a `type`.
- **Size budget** — root ≤ ~60 lines, subfolder ≤ ~20 (the exemplar is 18). Over budget ⇒ more
  knowledge still needs to move.

## 6. Nesting rules

- **Root `AGENTS.md`** = repo-wide operations + the `knowledge/` home map, loaded on every turn.
  There is no root `CLAUDE.md` in a conformant target (§7).
- **Subfolder `AGENTS.md`** = nearest-file navigation (auto-loads only under that folder): the
  folder's local commands/quirks + a pointer to the home that **covers** it. **Never duplicate the
  root.**
- **Creation is evidence-gated.** Propose a new subfolder pointer **only** where the evidence is
  concrete — never blanket-create one per directory. Two evidence sources, each gated per item:
  1. **A unit scoped to a folder** — a harness file being refactored holds a KEEP-class unit (local
     commands/quirks) that clearly belongs under a subfolder; the pointer lands there.
  2. **A greenfield folder** (step 1's discovery pass, swept **repo-wide from the root** — never
     scoped to the folders that already have an `AGENTS.md`, since those are precisely the ones a
     hand-picked list would over-represent) — a folder with a **local operational surface** but
     **no** `AGENTS.md`: a `*.sh` / `Makefile` / `justfile` / `package.json`-script, a distinct
     toolchain, or a `README` whose fenced blocks are run-commands. It earns an `AGENTS.md`
     **only** if it passes the §3 decision rule: the agent would need those commands on **every**
     task under that folder and they **don't derive from the root**. Fill the thin
     `agents-subfolder.md` mold — the local commands (KEEP) + a pointer to the home that covers the
     folder; the folder's `README` stays the deep map, the `AGENTS.md` just points to it.
- **Exclude from discovery** generated-output, asset, and vendored dirs — `.build/`, `dist/`,
  `build/`, `target/`, `node_modules/`, `**/__pycache__/`, `fonts/`, `assets/`, `.venv/`, nested
  `site-packages`/vendored dependency trees, and anything gitignored as a build artifact (verify
  with `git check-ignore`). A folder that only holds data or
  output earns no harness.
- `/quenching-knowledge align`'s skeleton already owns `docs/standards/AGENTS.md`.

## 7. AGENTS.md is the root source; CLAUDE.md is legacy

<!-- rules -->
The root `AGENTS.md` carries the repo-wide KEEP residue and pointers, and every harness reads it.
A root `CLAUDE.md` that carries its own instructions is consolidated into `AGENTS.md` — moved with
`git mv` when `AGENTS.md` is absent, unit by unit when both exist, with a fact present in both a
**FLAG** — and the pointer-only residue (`@AGENTS.md`) is **removed**, never recreated.

**Fact, and the version it was verified on.** the host 2.1.312 (2026-10-28) reads `AGENTS.md`
directly when there is no `CLAUDE.md`/`CLAUDE.local.md` in the working directory or above it
(default `claude-md-or-agents-md`, added in 2.1.277). With both files side by side, only
`CLAUDE.md` loads unless the setting is `claude-md-and-agents-md`. OpenCode 1.18.35 and Codex read
`AGENTS.md`. A host before 2.1.277 still needs the one-line `CLAUDE.md` import; that is a legacy
fallback, not the conformant layout.

<!-- rationale -->
One editable file read by every harness keeps one fact in one place; the generated Claude bridge
stays under `.claude/skills/` only because Claude does not discover `.agents/skills/`.
