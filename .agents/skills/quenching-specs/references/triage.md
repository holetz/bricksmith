---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*), Bash(py:*), AskUserQuestion
---

# /quenching-specs triage — rank the front, once, on one confirmation

**Input**: `$ARGUMENTS` — optionally one ID to limit the sweep; omit to rank everything in
the open (`plans`) phase.

It reads every spec in the open front, proposes ONE ordered list, and — on a single confirmation —
writes each spec's `priority` record.

**This is the only command that ranks.** `.agents/quenching/bin/cq specs next --front` consumes what this writes; with no
`priority` anywhere its ordering falls back to age alone.

**It ranks and nothing else.** Closing out is `/quenching-specs conclude`'s, resolving
`## Discoveries` is `/quenching-specs develop`'s discoveries stage.

The layout, the derived stages and the front's on-write check live in
[specs-develop/spec-driven.md](.agents/quenching/references/specs-develop/spec-driven.md)
§The spec document §Derived stages. The `.agents/quenching/bin/cq specs` surface lives in
[specs-surface.md](.agents/quenching/references/specs-develop/specs-surface.md)
§The `.agents/quenching/bin/cq specs` tool surface; the report mold, which owns the shape of both the step 2 table and
the step 6 report, lives in
[report-mold.md](.agents/quenching/references/specs-develop/report-mold.md)
§The report mold. How the tool is resolved and its path written lives in
[align/tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool §Write the resolved path literally on every invocation.

## Resolving the tool

Resolve `cq` per
[align/tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool. Branch on the **exit code** (0 ok · 1 findings · 2 refusal) and the `--json`,
never on prose.

## The record this command owns

```yaml
priority:
  level: 3
  criticality: high
  complexity: medium
  date: 2026-07-27
```

| Field | What it is | Rule |
| --- | --- | --- |
| `level` | this spec's **position in one ordered list** — 1 is next | an integer, unique across the front. This is what ranks. |
| `criticality` | a coarse word: `critical` · `high` · `medium` · `low` | the fallback that still ranks a half-filled record. Never the primary answer. |
| `complexity` | one of `low` · `medium` · `high` · `xhigh`, never a numeric size — see below | proposed on **every** ranked row. `low` needs concrete grounds; without them the floor is `medium` |
| `date` | when the ranking was made | stamped on every write |

The record is `writeOnce: false`: a later sweep re-ranks, and each write restamps `date`. Only this
command writes it.

**`complexity` answers a different question than `level`.** Propose it against the criterion
[spec-driven.md](.agents/quenching/references/specs-develop/spec-driven.md) §The scale
states: how much a human needs to be part of the process, from `low` (the LLM can carry it with
close to no supervision) to `xhigh` (a judgment stage joins the plan). A spec too thin to judge — a
`captured` one with a `## Problem` and no `## Proposal` — leaves this sweep carrying `medium`.

## Workflow

### 1. Read the front directly
Resolve the open front through `.agents/quenching/bin/cq specs`. A store refusal → report it and stop. Then:
```bash
.agents/quenching/bin/cq specs list --phase plans --json      # every spec in plans/: folder, derived stage, and its records
.agents/quenching/bin/cq specs section <id> Problem         # per spec being ranked, for the reason column
```
`list --phase plans --json` carries the `records` for exactly the front this sweep ranks, so the
current `priority` of every spec in scope arrives in that one call — **never open a spec to read
it.** Read `/docs/vision/` when present, to ground the ordering in where the repo is going rather
than in what is loudest.

Note what the table will have to explain: specs already ranked, specs whose ranking predates their
current stage, near-duplicates, and any `priority` whose shape does not match §The record this
command owns.
**Done when:** every spec in scope has been read and the anomalies are listed.

### 2. Build ONE ranking table
§The spec table, in its proposal form — the shared columns carrying each spec's **current** state,
then the four this command adds:

| Spec | Summary | Stage | Tasks | Priority | Complexity | Proposed `level` | Proposed `criticality` | Proposed `complexity` | Reason |

Rules for the table:
- **Every spec in scope gets a row** — including "stays unranked, because …".
- `level` values are **contiguous from 1** across the ranked rows. A gap or a duplicate is a bug in
  the proposal, not a nuance.
- A spec already carrying a `priority` appears with its current value and a **stated reason** for
  any change; no reason means no change. `Complexity` and `Proposed complexity` are two columns for
  that reason: the value on disk and the value being proposed sit side by side, so a re-judgement is
  visible as one and an unchanged row repeats itself.
- **Every ranked row proposes a `complexity`** — `medium` when the spec is too thin to judge, and
  the reason column says which of the two it is ("floored at `medium`, no `## Proposal` yet" reads
  differently from "`medium` — the pattern is proven in the tree"). `low` appears only where the
  reason names the grounds that earned it.
- Anything the sweep noticed but does not rank — a near-duplicate pair, a stub going nowhere, a
  ranking whose grounds have expired — is listed **below** the table as an observation naming the
  command **and the exact argument** that would act on it, runnable as printed. Never as a row.
**Done when:** one table covers every spec in scope, with contiguous levels and a reason per change.

### 3. One OK
Present the table and **wait for a single confirmation**. Adjustments → adjust and re-present;
rejection → write nothing and stop. Put a choice between two orderings on a decision sheet
([questions.md](.agents/quenching/references/specs-develop/questions.md) §The four shared mechanics §1)
rather than asking it open-ended.
**Done when:** the human has approved, adjusted, or rejected the whole table.

### 4. Apply exactly what was approved
One `.agents/quenching/bin/cq specs record` per approved spec, **in sequence** in one Bash block, and never an edit to
the file. On the git store each write is a local compare-and-swap on the clone's pending commit, so
writers still run one at a time; a refusal stops the block, and the run resumes from the refused
line (`sp-git-cas-exhausted` is re-run alone). The priorities reach the branch and the cards at the
next sync point; run `.agents/quenching/bin/cq specs push` after the block when another clone or the board needs the
ranking now:
```bash
.agents/quenching/bin/cq specs record <id1> priority --set level=<n1> --set criticality=<word1> --set complexity=<word1> --set date=<today> \
&& .agents/quenching/bin/cq specs record <id2> priority --set level=<n2> --set criticality=<word2> --set complexity=<word2> --set date=<today> \
&& ...
```
One fully-formed line per approved row — all four `--set` on every line, since `complexity` is
floored at `medium`, never omitted. The tool merges: a field not named survives, and the spec's
`id`, `title`, `date`, `verification` and its other records are never in reach of this write. The
record's own `date` is a different key from the spec's capture `date:`.
**Done when:** each approved row is recorded through `.agents/quenching/bin/cq specs` and no unapproved row was touched.

### 5. Check
```bash
.agents/quenching/bin/cq specs validate --phase plans --json
```
That is the whole check, and it must exit 0. The ranking lives in each spec's own `priority`
record; nothing else is written and nothing goes into the `/docs/` bundle.
**Done when:** the validator is clean, or its residue is reported verbatim.

### 6. Report

```bash
.agents/quenching/bin/cq components read .agents/quenching/references/specs-develop/report-mold.md \
  --sections "§The report mold" --rules-only
```

Emit §The report mold. Two body blocks:

1. **The ranking as it now stands** — fixed. Run one tool call and quote its output verbatim
   (§Quoting a tool's own output) rather than recomposing the step 2 table in prose:
   ```bash
   .agents/quenching/bin/cq specs next --front --table --order priority \
     --columns "spec,summary,stage,tasks,priority,complexity"
   ```
   §The spec table, the four proposal columns dropped, `Priority` and `Complexity` showing the
   values now on disk. Specs that stayed unranked keep their row, `Priority` and `Complexity` both
   reading `—`.

   **`--order priority` is required:** the default ordering leads with what is in flight, and the
   unranked sort last under `priority`. Quote the output; never re-sort it, re-tally it, or reach
   for the payload behind it.
2. **Observations** — optional, omitted whole when there are none. §The observations table, each
   row's `Recommended action` **runnable as printed**: the command with its real argument
   substituted, never a bare command name the reader has to complete.

Then §The next-step block, whose recommended line is `/quenching-specs execute <id>` for the
spec the approved ranking put first — the first thing the `priority` this just wrote decides.
**Done when:** both blocks and the next-step block are shown.

## Invariants to never violate

- Never write anything before the single consolidated confirmation; a rejected plan applies nothing.
- Never silently clobber a human-set `priority` — a re-rank carries an explicit reason and an
  explicit approval.
- Never propose duplicate or non-contiguous `level` values, and never force a rank onto a spec the
  sweep cannot honestly place.
- Never write a ranked row without a `complexity`, and never guess one below `medium`. `low` is a
  claim that the work can run almost unattended, so it is written only where the reason names what
  earned it.
- Never write a field outside the `priority` record. This command ranks; it does not develop,
  execute, or conclude.
- Never edit a spec's frontmatter directly; `.agents/quenching/bin/cq specs record` is the writer.
- Never remove a spec, move a spec, tick a checkbox, or resolve a `## Discoveries` line.
- Never infer completion or abandonment, and never treat staleness as evidence of either.
- Never fan out sub-agents, and never re-implement a check in prose — run `.agents/quenching/bin/cq specs validate` and
  report what it says.
- **Never print an observation whose `Recommended action` is not runnable as printed** — a bare
  command name, or a literal `<id>` reaching the output, is a defect.
