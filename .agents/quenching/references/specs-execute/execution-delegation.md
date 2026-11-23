# Specs execution — delegation and parallelism

This file owns the bounded delegation, parallelism and knowledge-boundary rules used by the
execution loop. The verify/commit mechanics and the handoff cadence live in focused siblings.

## Declared versus emergent `knowledge/`

<!-- rules -->

A task writes a `docs/standards/` doc **only when the task itself names it** — the path bulleted
under `## Impact`'s parsed `### Standards this spec will write into docs/standards/` sub-heading,
and named by that task. That doc is part of the task's deliverable: it is written before the
commit, reviewed in the same diff, and stamped `authority` honestly — `current` when the task
actually proved the rule, `background` when it is agreed but not yet proven.

Everything else the work reveals — a gotcha, a second-order consequence, a rule nobody had thought
of — costs **one line and no authoring**:

```bash
.agents/quenching/bin/cq specs discover "<id>" "<what was found, one line>"
```

The exception is a **pre-existing error** diagnosed and proved on the base
(`/docs/standards/workflows/task-execution.md` §Pre-existing errors): a declared `[fix-preexistente]`
task, whose own `files:` the executor honours as any other, or a spec captured with `cq specs new` and the
line `promoted: <id>`.

It is captured **indiscriminately**. The lines are resolved by `/quenching-specs develop`'s
discoveries stage, and the doc an emergent finding deserves is written by
`/quenching-specs conclude` at distillation.

A task that writes into `knowledge/` is not delegated — §Delegating an executor.

## Delegating an executor — permitted, and bounded

<!-- rules -->

A per-task executor sub-agent (`Task`) is **permitted** when both hold:

- the task declares `files:` — the sub-agent gets a bounded scope, not the whole repo;
- the task writes nothing under `knowledge/`.

Pin it to the session model, with one exception: **the `haiku` rung, per task.** When
`.agents/quenching/bin/cq specs estimate --spec <id> --task <tid> --json` reports `smallTierEligible: true` — an
executable `verify:` whose tests sit outside the task's `files:` (`oracleProtected`), nothing under
`/docs/` or `knowledge/`, and a projected context at or under 80k — the executor may be pinned to
`haiku` for ONE attempt. A failed `verify:` returns the task to the session model, which corrects on
the diff; a second failure goes to the human. The self-review (5d) reads the diff on the session
model before the tick. `smallTierEligible: false`, or no estimate → the session model. Judgment
never descends, and `/quenching-knowledge import-memory`'s classifiers stay never-`haiku`:
they authorize a deletion and have no oracle.

**The orchestrator keeps, without exception:** spec selection, the isolation, every
confirmation, every `.agents/quenching/bin/cq specs task --check` flip, every `.agents/quenching/bin/cq specs task --block` marker, every
`docs/standards/` write, every `.agents/quenching/bin/cq specs discover` line, the commit, and the decision to pause. The
sub-agent writes code inside its declared files and reports back — it never talks to the human and
never touches the spec's bookkeeping.

**The Codex executor is the admitted non-Claude executor.** A task `cq codex eligible` admits
(exit 0) is written by Codex through `cq codex implement` — a cheap model, not a `Task`, so the
model rule above binds sub-agents, not it. The orchestrator keeps the same list, plus the task's
`verify:` (run on every Codex task whatever the declared policy) and the self-review, which runs
outside Codex on its returned diff.

### The cost of delegating, and when it inverts

<!-- rules -->

**Permitted is not free, and the account runs the other way more often than it looks.**

- **Delegate by file, or by section of tasks — never task by task.** One sub-agent that owns six
  tasks over one file reads it once; six sub-agents read it six times. The one exception is the Codex
  executor: it is dispatched **one task at a time**, never two at once, because its eligibility
  and its quota reserve are judged per task and a single heavy run must not drain the window for
  the rest.
- **Run the four-item self-review INSIDE the sub-agent**, and have it return a verdict. A
  sub-agent that hands its diff back for review puts the diff into the long context, which is the
  cost the delegation was for.
- **Where the tasks are small and the shared file is large, keep the work.** Reading once and
  re-reading from cache is the cheaper arm, and the loop is allowed to say so.

The delegation is a `Task`, not `context: fork` — §Tooling asides.

### Parallelism must be earned

<!-- rules -->

Two tasks run concurrently **only** when all three hold:

1. a `[P]` marker was set on both **at definition time** — never inferred while executing;
2. their declared `files:` sets are **provably disjoint** (`.agents/quenching/bin/cq specs` checks this mechanically —
   see §The `[P]` check);
3. neither writes into `knowledge/`.

Serial is the default and needs no marker. Without proven file disjunction, parallel execution
trades wall-clock for merge conflicts and loses on both.

### The `[P]` check

<!-- rules -->

```bash
.agents/quenching/bin/cq specs parallel --spec "<id>" [--json]
```

Reports each `[P]` group and whether it is `eligible`. Exit **0** when every marked group is
eligible, **1** when any group overlaps or lacks `files:`. **Branch on that, never on judgment**:
a group reported ineligible runs serially, and the reason is stated in the report rather than
argued about.

## The Codex executor

<!-- rules -->

`cq codex eligible` exit 1 or 2 → the task goes on as §Delegating an executor says. Exit 0 →
write the task's text, `files:`, `verify:`, `pattern:` and the sections execute's step 4 read into
a prompt file under `.quenching/codex/`, and dispatch it alone — never two Codex runs at once:

```bash
cq codex implement --spec "<id>" --task "<task-id>" --worktree "<work checkout>" \
  --prompt <prompt file> --json
```

- **Exit 0** → the diff is Codex's. Run the task's `verify:` **whatever the declared policy**,
  then execute's self-review (step 5d). A red `verify:` is fixed by you **on top of Codex's
  diff**, fed its output; start from scratch only when the self-review judges the approach wrong. Codex never
  gets a second attempt at the task.
- **Exit 1** (quota, timeout, Codex error) → write the task yourself.
- **Exit 2** (`codex-git-guard`), payload `outside` **non-empty** → those paths were never in
  `files:` and you may not clean them: stop the task as `needs-human` naming them, so the human
  cleans the tree; write nothing and do not discard.
- **Exit 2** (`codex-git-guard`), payload `statusUnreadable: true` → `git status` failed, so
  what Codex wrote is unknown and the payload carries no `discard`: stop the task as `needs-human`
  with the guard's `reason`; write nothing and do not discard.
- **Exit 2** (`codex-git-guard`), payload `refsUnreadable: true` → the worktree's refs could not be
  read, so whether Codex moved `HEAD`, the branch, the reflog or the stash is unknown and the
  payload carries no `discard`: stop the task as `needs-human` with the guard's `reason`; write
  nothing and do not discard.
- **Exit 2** (`codex-git-guard`), payload `outside` **empty** and neither `statusUnreadable` nor
  `refsUnreadable` → discard the
  diff **inside the payload's `discard` only**:
  rewrite each tracked file with `git cat-file --filters HEAD:<file> > <file>` and delete each
  new one; never `reset`, `stash`, `checkout` or `restore`. Then write the task yourself and
  carry the guard's `reason` to the report.
- **`stopLoss: true`**, on any exit → keep this task's outcome, and route every later task of
  this run as §Delegating an executor says; nothing is written to disk.

Each dispatch is one report line: `Codex: <task-id> <wrote|fallback|guard> — <reason>, <tokens or
not measured>, quota +<delta or not measured>`.

A task the consultation did not dispatch still leaves its line: `Codex: <task-id> skipped — <reason
from eligible, or stop-loss>`. Every task has one `Codex:` line; its absence is the anomaly.
