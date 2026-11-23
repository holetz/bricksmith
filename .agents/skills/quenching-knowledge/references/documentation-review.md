---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Task
---

# /quenching-knowledge documentation-review — score and report without writing

**Input**: `$ARGUMENTS` (an optional page slice or plan path; omit to review the pages named by
`./.quenching/documentation/plan.md`).

This is a read-only critic. It applies the rubric, forbiddens and ledger in
[knowledge-documentation/quality.md](.agents/skills/quenching-knowledge/references/knowledge-documentation/quality.md),
scores dimension #8 against
[knowledge-documentation/agent-readability.md](.agents/skills/quenching-knowledge/references/knowledge-documentation/agent-readability.md),
and returns a verdict for each page. No `Write`, `Edit`, shell or other mutating tool is granted.

## Doctrine

- **Zero writes, semantically and mechanically.** Inspect only; fixes re-enter through `write` or `build`.
- **Score all eleven dimensions.** Use the exact 0–5 scale and per-page table; do not average away a zero.
- **Critical dimensions gate.** #4 Scannability, #9 Integrity/source and #10 Navigation must meet the gate; landings and indexes carry the higher average threshold.
- **Find the five forbiddens.** Fake depth, pretty-but-useless visuals, README dumps, claims without source and sections without intent are auto-fails.
- **Source gaps are not craft defects.** Report them distinctly so the next `write` round cannot invent a fix.
- **Catalog integrity is a hard review dimension.** Walk the layer/schema index, verify every detail
  route is reachable and check that each item exposes stable identity and source lineage. Missing
  lineage is an integrity failure, not a prose preference.
- **Coverage is dimension 11.** Score mapped sections with substantive routed content, fail empty
  sections, and report the percentage separately from source gaps. Treat `TODO`, `FIXME`, prompt
  text, `.quenching/` paths and unresolved ledger markers in a published page as leakage; the fix
  belongs to `write`, while an intentional exclusion belongs in the plan.
- **Persist the verdict outside the site.** After each round, write `.quenching/documentation/review-<n>.md`
  with scope, round, score table, gaps and verdict. This scorecard is an operational artifact and
  is never copied under `docs_dir`.
- **Fan-out stays read-only.** For a large set, delegate one `Task` per slice and merge summaries; sub-agents receive no write tools.

## Workflow

### 1. Load the plan and page set

Read the plan, resolve each assigned page and its ledger, and note missing files or frontmatter.
**Done when:** the review set and each page's intended audience and intent are fixed.

### 2. Inspect structure, truth and reuse

Check hooks, stable headings, links, examples, components, `TL;DR for agents`, source rows,
`source gap:` markers and the five forbiddens. **Done when:** every observable defect has a page,
evidence and a severity.

### 3. Score each page

Fill the eleven-column score table and calculate the average. State below-threshold dimensions and
the owning pass (`write` for prose, `build` for site layer, source owner for a gap). **Done when:**
every page has a complete score and a pass / loop / source-gap verdict.

Persist that table, the round number, reviewed scope, coverage percentage and ranked gaps to
`.quenching/documentation/review-<n>.md`; keep the page tree byte-identical.

### 4. Review the journeys and ledger

Walk evaluator, implementer and agent journeys end to end. Confirm every strong claim is traceable
and every open gap reaches the report. **Done when:** no journey dead-ends silently and the
ledger verdict is explicit.

Also report `covered / mapped × 100`, list sections with no substantive route, and verify the final
page link leaves its current page. A page that only links to itself or ends in an internal TODO fails
dimension 11 even when its prose scores well.

### 5. Return the bounded verdict

Return the per-page tables, ranked defects, round number and the exact next command. A page below
threshold after round three remains reported below threshold and is never silently shipped.
**Done when:** the report is complete and the working tree is byte-identical to its pre-review state.

## Invariants to never violate

- Never write, edit, delete, stamp or reformat any file.
- Never claim a build or visual QA that was not run.
- Never treat an unsupported claim as passing because the prose is attractive.
- Never hide a zero dimension or a source gap in an aggregate score.
