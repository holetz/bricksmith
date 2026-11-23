---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Agent, Task
---

# /quenching-specs orchestrate — conduct an epic or a queue

**Input**: `$ARGUMENTS` — a composite id (any spec whose `## Tasks` names members) or a list of spec
ids, and optionally `--autonomous`. Empty or ambiguous → run
`.agents/quenching/bin/cq specs list --json` and ask which on a decision sheet ([questions.md](.agents/quenching/references/specs-develop/questions.md) §The four shared mechanics §1).

This command is a launcher. The protocol (waves, routing, budgets, acceptance, audit) lives in the
`spec-orchestrator` agent definition, `quenching/agents/spec-orchestrator.md`; do not restate it.

## Workflow

### 1. Resolve the set
Resolve `cq` per [align/tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool; branch on the exit code. Close what already merged first —
`.agents/quenching/bin/cq specs reconcile --write --json` archives every merged spec carrying `## Outcome`, and a
`needs-human` row is relayed to the human. Confirm each id exists with `.agents/quenching/bin/cq specs show --spec <id> --json`.
**Done when:** the composite id or the list of spec ids is verified to exist.

### 2. Check who can run alone
Read `.agents/quenching/bin/cq config show --effective --json` once and take the `shared.autonomy.runAloneUpTo`,
`shared.autonomy.merge` and `shared.autonomy.maxWorkers` lines; exit 0 without a line means the
default (`low`, `policy`, `3`). Read each spec's metadata with `.agents/quenching/bin/cq specs status --spec <id> --json`: `complexity` comes from
`records.priority.complexity` and `approved` from `records.approved`. Split the set from these
`status` payloads: **runs alone** is a spec with `approved` recorded or
a `complexity` at or below `runAloneUpTo` (`none` leaves only the approved ones); **needs a human**
is any other spec with no `approved`. Nothing needs a
human → go to step 3. Otherwise ask once on a decision sheet, naming the specs on each side:

- **Develop now** — run `/quenching-specs develop <id>` in this session, one spec after another,
  for the specs that need a human, then launch everything. A develop of a `medium` spec costs about
  40k tokens of this session (its body and references alone are about 17k), so this is offered
  only for **two specs or fewer**; for more, end the run by listing one
  `/quenching-specs develop <id>` line per spec and `/quenching-specs orchestrate` again after.
- **Launch the independent subset** — only the specs that run alone and depend on none of the
  ones that need a human.
- **Force it** — `--autonomous`: every spec runs, and the approval is stamped
  `by=orchestrator-forced`, never `by=human`.

`--autonomous` already given → skip the question. **Done when:** the set to launch is fixed.

### 3. Launch the orchestrator
Start the `quenching-spec-orchestrator` agent with a prompt of at most 1,500 characters: the epic id or
the spec ids, the base branch, the word `autonomous` when it was forced, and the three values as
`runAloneUpTo=<v> merge=<v> maxWorkers=<v>`. Pass nothing else; it
carries its own protocol, and without `autonomous` it blocks a spec that still needs approval.
**Done when:** the orchestrator has returned its fixed report.

### 4. Relay
Show its per-spec blocks and totals line unchanged. A spec in state `blocked`, `failed` or
`continue` is named with its note and left for the human; never retry it here.
**Done when:** the report is shown and every non-`done` spec is named.
