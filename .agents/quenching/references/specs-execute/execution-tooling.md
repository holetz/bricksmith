# Specs execution — tooling rationale

This file owns the rationale behind the execution loop's tooling choices. It is explanatory
context, loaded only when a command needs to explain one of those choices.

## Tooling asides, relocated

### Why `Bash` is unrestricted

<!-- rationale -->

`/quenching-specs execute` is the one `/quenching-specs-*` command that runs the target repo's own toolchain
— build, tests, linters, migrations, and `git` — as part of implementing a task. Its siblings are
scoped to `python3`/`py` because they only ever talk to `.agents/quenching/bin/cq specs`.

### Why the resolved-whole notice matters

<!-- rationale -->

When `cq` does not resolve, the body falls back to `Read`ing the cited file whole and says so in
the report — because that is the run's context cost changing, not a cosmetic difference.

Every rung above that fallback is the plugin's own file — the target-local `.agents/quenching/bin/cq` through the `bin/` shim on
`PATH`, or the plugin path — and nothing else is one. A deployed target is the generated
exception: its body calls the manifest-owned `.agents/quenching/bin/cq`, not a hand-installed
copy. This sentence used to name a rung outside the plugin, "the target's `.claude/hooks/cq`", which
[align/tool-resolution.md](../align/tool-resolution.md) §Resolving the tool forbids outright:
*there is no third rung*, never a copy under a target's `.claude/hooks/`. A copy that lives there is
never executed by anything the plugin runs, so a body that reached for it would have been reaching
for a file nobody keeps current.

### Why the declared files, never their folder

<!-- rationale -->

Measured, not assumed: on this repo, the four subject folders a spec touched held 19 files
(~31k tokens) against 5 files (~13k) for what `## Impact` declared, and that gap arrives at turn
one, where every later turn re-sends it.

### Why there is no mechanical net for an undeclared contract

<!-- rationale -->

The mirror image of the line above. `.agents/quenching/bin/cq specs validate` already warns when a declared standard has
no task (`sp-impact-uncovered`); the inverse — a binding standard nobody declared — is not
derivable, because deciding a standard governs a task is reading, not parsing. Every approximation
of it has to re-read the folder to have something to warn about, which is the cost
`/quenching-specs execute` step 4 removed by reading only the declared files.

### Why the spec's author declares the policy

<!-- rationale -->

The spec's author is the only party who knows whether this repo's suite takes four seconds or
forty minutes. What each policy fits:

- `per-task` — a fast suite, or a task set where each step can break the last.
- `per-section` — most repos — a section is the smallest independently shippable unit.
- `end-of-plan` — a slow suite, or an integration that is meaningless until the whole spec lands.

### Why the surface harness belongs to neither

<!-- rationale -->

Running it from the spec cycle charges every spec for a front most of them never touch.

### Why a marker and not a counter

<!-- rationale -->

v1 kept an attempt count in a sidecar `.specs.json` and stopped at five. The count was machine
state a human never saw: a task went quiet after five failures with no trace of *why*, and the only
way to resume was a `--reset-attempts` incantation that bought five more attempts at the same wrong
approach. A written reason serves the same purpose — stopping unattended retry loops — while being
legible to the person who has to unblock it, and it lives in the file they are already reading. A
blocked task with no reason is exactly the hidden state this replaced.

### Why re-read from scratch after two failures

<!-- rationale -->

Two failures in a row nearly always means the third attempt is repairing a mental model that was
wrong at attempt one, and each further patch is built on the same error.

### Why the self-review is four items and not a code review

<!-- rationale -->

This is deliberately *not* a full code review: it runs per task, and a three-line change must not
cost a full-diff read.

On item 1, reuse: duplicating it is the most common cost of task-scoped work. On item 2, useless
defense: defensive code for an impossible state hides real failures.

### Why the spec task is recorded after the commit

<!-- rationale -->

That order is the point for the specs store: the commit is a local fact, while the task checkbox
is a remote write that cannot travel inside it. Recording the subject and sha only after the commit
exists lets a failed spec write be retried without rebuilding or amending the code commit.

### Why the commit chain is one call and not four

<!-- rationale -->

Written as separate calls the sequence was a rule the body had to be obeyed to hold; chained, it is
enforced by the shell — verify before staging, the commit before the spec tick, and a broken
link short-circuiting every link after it. The spec write is intentionally after the local
commit, so the two facts have the failure behaviour their different stores require.

### Why each task has its own commit, and why both subject and sha

<!-- rationale -->

**There is no per-task bookkeeping commit any more.** The spec task write happens after the
commit and does not create another local commit. One task is one commit for code and docs the task
named, plus one remote task record carrying the commit's subject and sha.

One commit per task is what makes retrying and resuming worth having: a bad task can be blocked or
undone without touching what already landed. The branch keeps those independently anchored commits
through the whole run, so review and reversion can stay at task granularity.

The subject is the durable human-readable task anchor and the sha is the direct machine anchor. The
subject survives a rebase while a sha does not; recording both gives status and validation the
stronger direct fact without losing the readable fallback. The rules still forbid amending an
earlier task or force-pushing, so no later repair is needed.

### Why discoveries are captured indiscriminately

<!-- rationale -->

Whether it is worth acting on is a later judgment, and asking the executor to make it mid-task is
how a finding gets dropped for being inconvenient.

Two failure modes this line exists to prevent, and they pull in opposite directions: a build that
stops to author a standard nobody asked for, and a build that silently loses what it learned.
Declared → write it. Emergent → record it in one line.

### Why delegation is priced, not just permitted

<!-- rationale -->

A sub-agent starts on a cold context and does not share the session's prompt cache, so it pays the
full first read of every file it touches. Where N tasks declare the same large file, that is N
cold reads against the orchestrator's one warm one.

The account is **declared arithmetic over files on disk, not a measurement of any run** — the
distinction `docs/standards/automation/session-evidence.md` §The rule a counted claim must obey
imposes, and it is stated as an estimate here because that is what it is. On this repo's
`configurable-spec-backend`, 18 of 29 tasks are delegation-eligible and 13 of them declare the same
file: the pre-refactor specs script (as it stood then, before this repo split it into a package), ~37k tokens.
Task-by-task that is ~13 × 37k ≈ 480k against roughly 150k for an
orchestrator reading it once and re-reading from cache — a delegation that reads as a saving and
is not one. Measured across the whole transcript archive, this permission had never once been
exercised, so nothing here revokes it; what was missing was the arithmetic that says when it pays.

### This is not `context: fork`, and the never-fork rule is untouched

<!-- rationale -->

The plugin's standing rule forbids `context: fork` **on these commands**, because a forked context
cannot present the mid-flow confirmations every sweep depends on — the conversation carrying the
human's OK would be out of reach.

Dispatching a `Task` for a bounded, file-scoped unit of work does the opposite: **the orchestrator
stays in the live conversation**, exactly where `/quenching-knowledge glossary-backfill` and
`/quenching-knowledge import` already dispatch from. One moves the decision-maker out of reach; the
other sends a worker out and keeps the decision-maker in place. They are different mechanisms
about different things, and no future sweep should "fix" one into the other.

### Why the Handoff cadence is four events, not a judgment

<!-- rationale -->

Two cadences were tried before the four-event list and both failed. Measured on a 13-task run,
rewriting `## Handoff` after every committed task produced revisions ~90% identical to one another.
Substituting a judgment — "rewrite it when the underivable state changed" — fails the same way a
threshold would: an unattended run never judges that something went stale, so a judgment-based
trigger never fires. Each of the four events names an act the loop just performed, never an
assessment it has to make, which is what lets the rule hold in an unattended run.

### Why a section is the boundary, and why the size sets only the tone

<!-- rationale -->

A number invented before it is measured fixes the answer, which is why §The Handoff cadence is four
events rather than a judgment.

The run's cost is `tokens × turns remaining`, so it grows with the **square** of the turn count:
seven runs of ~45 turns cost roughly a seventh of one run of 300 for the same work. That figure is
declared arithmetic over the integral, not a measured run, and it assumes resumption costs about
nothing — which holds only because the trail above was already being maintained for other reasons.
A section is the unit because it is the smallest independently deliverable one the front already
defines; `per-section` is the default verification policy for the same reason, so a boundary is
also the point where the suite has just run.

The window size came back once it was measured rather than invented: `.agents/quenching/bin/cq components session
context` reads the recorded peak against the role budget of
`docs/standards/automation/context-discipline.md` §Measured context budgets. It is read at a
stage end, never at a section boundary: a boundary question cost more turns than the new session it
offered saved. For the worker that number is a size estimate and the run never stops on it; the
conductor's budget is the one stop that survives, and the orchestrator checks it between waves.
