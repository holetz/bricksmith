---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Bash(git:*), Bash(wc:*), Bash(mkdir:*), Read, Grep, Glob, Write, Agent, Task, AskUserQuestion
---

# /quenching-specs improve — from a critical map to an approved epic

**Input**: `$ARGUMENTS` — optional focus areas (e.g. `specs storage, CI`), `--depth quick|full`
(default `full`), and `--with-session-logs` to mine the target's the host session logs for usage
evidence. Typed by the human only: this command reads the whole repository and creates many specs.

The run has four outputs, in order: a **map** (candidate files the human can read), the human's
**decisions**, an **approved backlog**, and **one epic** whose members are the approved specs. It
never builds anything; building is `/quenching-specs orchestrate` or `/quenching-specs execute`.

## Invariants
- **Relevance over coverage.** A candidate exists only with evidence (`path:line`, a command's
  output, or a measurement), a concrete consequence (cost, failure, lost work, friction a person
  reported) and no open spec already covering it. Cosmetic, speculative or taste-only findings are
  dropped, not listed as "low". At most 15 candidates; fewer is better.
- **Verify before you assert.** Every high-severity candidate is re-checked by this session itself,
  not only by the agent that found it. What was not reproduced is marked `[unverified]`.
- **Read-only until step 6.** Survey agents get no write tools and are told: no `git stash`,
  `checkout`, `reset`, commit or push. After they return, audit `git status --short`,
  `git stash list` and `git worktree list`; any change is reported, never silently reverted.
- **Cost discipline.** At most 3 agents at a time, each briefed in ≤1,500 characters, each returning
  ≤800 words. The orchestrating session reads summaries, not file dumps.
- **The map is ephemeral.** Write it under the target's declared durable-ignored area (the root
  harness line `Ephemeral writes: <path>`), else the session scratchpad: `<area>/improve/<date>/`.
  Specs carry the durable record; the map is not committed.

## Workflow

### 1. Frame
Resolve `cq` per [align/tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool. Read the root harness (`AGENTS.md`; legacy `AGENTS.md`), the `/docs/` index if
present, `.agents/quenching/bin/cq specs config` and `.agents/quenching/bin/cq specs list --lean --json` (open specs, to avoid duplicates), and
`git log --since=90.days --format=%s | wc -l`. Probe each installed front with its read-only verb
(`cq <front> doctor --json` or `status --json`); a finding there is evidence, an exit 2 is
"inconclusive", not a finding. State the focus (from `$ARGUMENTS`, else the whole repository) in
one line. Ask only if the focus is genuinely ambiguous.
**Done when:** the focus, the open-spec titles and the front probe results are known.

### 2. Survey in parallel
Split the repository into at most six areas, chosen from what exists: architecture and code; tests,
verification and CI; documentation and knowledge; the agent harness and command surface
(`AGENTS.md`/`AGENTS.md`, `.claude/`, descriptions, context cost); planning and specs flow;
delivery, operations and security. Run the `quenching-spec-scout` agent per area, at most 3 at a time,
each with: the area, the focus, the open-spec titles, the relevance invariant above, and the
return format: candidates as `title · evidence (path:line or measurement) · consequence ·
severity (high/medium) · effort (S/M/L)`. With `--with-session-logs`, one agent instead measures
the target's sessions under `~/.claude/projects/<target>/`: commands used, tokens per run,
delegation patterns, human corrections. It reports aggregates only and never copies content.
`--depth quick` runs only the areas the front probes flagged.
**Done when:** every area returned and the git audit after the agents is clean or reported.

### 3. Filter and verify
Merge and dedupe. Drop any candidate that fails the relevance invariant or duplicates an open spec
(name the spec instead). Re-check every `high` yourself with one targeted read or command; demote or
drop what does not reproduce, or mark it `[unverified]`. Rank by consequence ÷ effort, and group
candidates that share a root cause into one.
**Done when:** ≤15 candidates remain, each with verified evidence or an explicit `[unverified]`.

### 4. Write the map
Under the map directory, write one file per candidate in folders by area, using these headings so
each converts directly into a spec: `Problem` (with evidence), `Proposal`, `Out of scope`, `Risks`,
`Validation`, plus a header table with severity, effort, suggested `workItemType`, subject and tags
from the target's catalogs, and `Open questions`. Add a `README.md` index with the ranking and the
dependencies between candidates. Report the path and a ranked list of the top items in 10 lines or
fewer.
**Done when:** the map exists and the human has the index path and the summary.

### 5. Decide with the human
Collect the open questions whose answer changes the plan: identity or scope, irreversible
migrations, breaking changes, autonomy limits. Leave out the ones you can settle with a sound
default. Ask them on decision sheets ([questions.md](.agents/quenching/references/specs-develop/questions.md) §The four shared mechanics §1), no cap: independent ones on one sheet, recommendation first, each dependent one on the next sheet, shaped by the last. Then present the backlog as ordered waves with dependencies, and ask which
part to approve: all, some waves, or chosen items. **Ask the epic's autonomy policy** in the same
rounds, recommendation first: develop the members to `ready` now with the human present (each one
reaches `approved by=human`), or authorize forced execution (`/quenching-specs orchestrate
--autonomous`, each approval stamped `by=orchestrator-forced`). Only the second choice records
the canonical policy text "forced execution, push, PR and merge of every member"; the first records
"human present; no forced execution, no push, no merge", which the orchestrator never reads as a merge authorization. Record each decision and default in the map's
`README.md` and update the affected candidate files. Ask nothing whose answer would not change what
you write.
**Done when:** the human has approved an explicit subset of the backlog, or declined, which ends the
run with the map as its only output.

### 6. Materialize the composite
Composition is a property of the document, not a type: do not look for an `epic` entry in the
target's `workItemTypes` and do not create the spec with `--type epic`.

1. **Composite.** Create it with `.agents/quenching/bin/cq specs new "<title>"`. Fill its `## Problem` (the goal),
   `## Proposal`, `## Out of Scope`, `## Risks`, `## Validation` (done criteria per wave) and
   `## Design` (the recorded decisions, **including the autonomy policy chosen in step 5, dated, in the canonical text of that choice**) with
   `.agents/quenching/bin/cq specs section <id> "<Heading>" --write`.
2. **Members.** For each approved candidate, in wave order:
   - Search the lean listing by title first, so a retry never duplicates.
   - Create it with `.agents/quenching/bin/cq specs new` with the suggested `--type`, `--subject`, `--tags` and
     `--complexity`.
   - Write its `## Problem` from the candidate file.
   - Run `.agents/quenching/bin/cq specs link add <composite> <spec> --group "<wave>" --after <labels>`.

Write members one at a time. Every write is checked with `.agents/quenching/bin/cq specs status` and `validate`. The
level is derived: one wave of members is a `feature`, two or more waves are an `epic` — there is
nothing to declare.
**Done when:** `.agents/quenching/bin/cq specs status --epic <id>` lists every approved item with its wave and
dependencies, and `.agents/quenching/bin/cq specs validate` raises no `sp-link-*` finding.

### 7. Hand off
Print the composite id, its derived level, its members per wave, and the next commands:
- `/quenching-specs develop <id>` to compose each member to ready;
- `/quenching-specs orchestrate <epic>` to build the epic in waves — with `--autonomous` appended
  when the policy in the epic's `## Design` is the forced-execution authorization.

Do not start either. With the policy "develop with the human present", the first command is the one
to run before orchestrating.
**Done when:** the hand-off is printed and the map path is repeated for the record.
