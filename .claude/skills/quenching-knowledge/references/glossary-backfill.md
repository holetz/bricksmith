---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*), Bash(py:*), Write, Edit, Task, AskUserQuestion
---

# /quenching-knowledge glossary-backfill — backfill the glossary from the whole bundle

**Input**: `$ARGUMENTS` (an optional home/slice to scope the sweep; omit to scan the whole bundle).

Sweeps the canonical OKF bundle's **entire** `/docs/` tree for repo-specific terms that were never fed into
[`/docs/glossary.md`](/docs/glossary.md), and backfills them in one
consolidated pass. This is a **retroactive, whole-bundle** sweep, not a capture: it never
writes a `concepts/` concept doc and never asks the human what a term means — those stay
`/quenching-knowledge learn`'s job. The glossary format and the shared **Enriching the glossary**
procedure live with `/quenching-knowledge add`
([knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md)); the
`type` vocabulary and conformance rules with `/quenching-knowledge align`
([knowledge-align/taxonomy.md](.agents/quenching/references/knowledge-align/taxonomy.md),
[knowledge-align/conformance.md](.agents/quenching/references/knowledge-align/conformance.md)). The fan-out convention
(bounded recon, sliced `Task` agents, compact partials, one merged plan) follows
`/quenching-knowledge import-memory`, the only other skill in this plugin that uses `Task`.

## Workflow

### 1. Locate the glossary and read the baseline
Find `/docs/glossary.md` — the bundle root is the fixed `/docs/` convention. If the
`concepts/` home or the glossary seed is missing, stop and offer
`/quenching-knowledge align` to install the skeleton, then stop; do not resume in the same run. Read the current entries once — this
is the dedupe baseline every slice's candidates get checked against.
**Done when:** the glossary path and baseline are resolved, or the missing-bundle handoff is reported.

### 2. List the bundle, bounded
List every doc path under `/docs/` with `Glob`, paths only — never read bodies here,
excluding `index.md`, `log.md`, and harness files (`AGENTS.md`; legacy `AGENTS.md`). `catalog/**` and
  `external/repositories/**` are listed but **not** body-scanned by default — note them as
  excluded from the sweep. If `$ARGUMENTS` names a home or slice, restrict the candidate set to
  that subtree and report the omitted homes; otherwise scan the whole bundle. **Done when:** the
  bounded path set and exclusions are fixed.

### 3. Slice and fan out
Group the remaining paths by top-level home (`standards/`, `vision/`,
the reader-facing quadrants, `concepts/`, `external/` minus the excluded slice);
split further if a home runs large (~15–20 docs per
agent). Dispatch one `Task` sub-agent per slice — with `model: haiku` and `effort: low`:
a slice agent does pure extraction and returns compact tuples, and over-collection is
harmless because the orchestrator cross-checks every candidate against the Step 1
baseline before anything reaches the plan. It reads only its slice's doc bodies and
returns a compact candidate list — `{term, one-sentence definition, source doc path}` —
dropping anything it can't honestly derive from the text. No sub-agent reads outside its
slice or writes `glossary.md`.
**Done when:** every slice has returned, or an empty/excluded slice is recorded.

### 4. Merge, dedupe, cross-check
Consolidate every slice's candidates: drop a candidate whose term is already listed in the
Step 1 baseline **unless** it now offers a link the existing entry lacks (a **refine**);
dedupe candidates for the same term found in more than one slice, preferring the most
specific source doc. **Done when:** one deduplicated candidate set is ready for the plan.

### 5. Present ONE consolidated plan
Show a single table: `Term | definition | link (or none) | source doc | action
(add/refine-link/skip)`, plus which homes were excluded from body-scanning
(`catalog/**`/`external/repositories/**`) and why. **Wait for one confirmation** before
writing anything — never present a plan per slice. **Done when:** one consolidated plan is shown
and its confirmation is settled.

### 6. Write on confirmation
The orchestrator alone edits `glossary.md`: insert each **add** in alphabetical
position as `* [<Term>](<path>.md) — <definition>` (linked) or `* **<Term>** —
<definition>` (unlinked); for each **refine**, add the missing link to an existing unlinked
entry — never overwrite a filled definition or link. The glossary is the only file this
command writes; the count of terms added and refined belongs in the report, not in the bundle.
**Done when:** every approved add/refine is written or its refusal is reported.
**Done when:** every approved add/refine is written or its refusal is reported.

### 7. Self-check against the conformance core
Verify every file you touched against
[knowledge-align/conformance.md](.agents/quenching/references/knowledge-align/conformance.md),
plus this skill's own gate: the list is still sorted and its links resolve. **Done when:** the
glossary passes the conformance and link checks.

## Invariants to never violate

- Never invent a definition or a link target — a candidate must trace to its source doc's
  own words, and a link must point at a doc that actually exists.
- Never let a sub-agent write `glossary.md` directly — sub-agents only propose; the
  orchestrator is the sole writer.
- Never skip the single consolidated confirmation or present per-slice lists — every slice
  merges into ONE plan before any write. A cycle-authorized run (convergence.md §The cycle-authorization contract) replaces
  the gate with narration but still merges everything into ONE presented plan.
- Never clobber a filled entry (definition or link) on merge, and never leave the list
  unsorted.
- Never blindly descend into `catalog/**` or `external/repositories/**` — list-only by
  default, reported as excluded, opt-in only.
- Never author a `concepts/` concept doc or ask the human what a term means — that is
  `/quenching-knowledge learn`'s job, not this skill's.
