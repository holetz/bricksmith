---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Bash, Read, Glob, Grep, Write, Edit, AskUserQuestion, Task, Skill
---

# /quenching-specs execute — build one spec, one task at a time

**Input**: `$ARGUMENTS` — optionally a spec id. Omitted → infer from the conversation, or
auto-select when exactly one spec is under way; vague or ambiguous → you MUST prompt.

Builds the `## Tasks` of ONE spec: writing each task, verifying it under the spec's declared
policy, reviewing its diff, committing it alone, and recording the spec task after that commit.

**This command stops at the last commit.** Reviewing the whole branch, writing the `/docs/` the work
revealed, merging, and archiving belong to `/quenching-specs conclude` — the archive only after the
merge.

`Bash` is unrestricted: the verification, `.agents/quenching/bin/cq specs` and Git commands are resolved from the live
spec. The commit itself is one `.agents/quenching/bin/cq git commit` call, which commits the existing index.

## Resolving the tool

Resolve `cq` (`.agents/quenching/bin/cq components read` is the section reader every `§X` citation in this body resolves through)
per [align/tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool; branch on the **exit code** (0 ok · 1 findings · 2 refusal) and the `--json`,
never on prose.

## Workflow

### 1. Select the spec
An ID was given → use it. Otherwise infer from the conversation, auto-select when exactly one spec
is under way, or run `.agents/quenching/bin/cq specs list --json` and pick on a decision sheet ([questions.md](.agents/quenching/references/specs-develop/questions.md) §The four shared mechanics §1). Announce
"Building spec: `<id>`" and how to override. A sub-agent does not ask: return `STATE: blocked` with `NOTE: needs-human:` carrying the decision sheet.
**Done when:** one spec is resolved.

### 2. Take the tree, the isolation and the state in one read
**The precondition comes first.** Load the rule that binds this step:

```bash
.agents/quenching/bin/cq components read .agents/quenching/references/specs-execute/execution.md \
  --sections "§The precondition"
```

The rest of this step is read in **one call** — the post-merge close of whatever already merged,
the tree, the isolation ref, the spec's own state, and the environment probe below:

```bash
.agents/quenching/bin/cq specs reconcile --write --json   # archives merged specs carrying ## Outcome
git status --porcelain
git branch --list "plan/<id>-<handle>"
git branch --show-current
git worktree list
git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null
.agents/quenching/bin/cq specs status --spec "<id>" --json
.agents/quenching/bin/cq specs config --json
# and the hook probe of 2b, in this same call
```

`git status --porcelain` non-empty → **refuse to start**, per
[execution.md](.agents/quenching/references/specs-execute/execution.md)
§The precondition. Offer to commit or stash; do not override this precondition inside execution.

**Then resolve the work ref, and check before offering.** The spec's work branch is the `branch`
record's `work` when the `status` payload carries one, else `plan/<id>-<handle>` — the ref already listed.
A record naming something else costs one more `git branch --list "<work>"` to know whether it is
alive. Two outcomes end the question here:

- **This checkout is on the work ref** (`git branch --show-current` equals it) → the spec **is**
  isolated. Go straight to the loop and offer nothing.
- **The ref is alive but held elsewhere** — a `git worktree list` entry, or a checkout this one is
  not on — → it is being built somewhere else, and starting here would fork the work. Name where,
  and stop.

**A ref that merely exists is not isolation.** Anything short of being **on** the ref falls through
to the branches below.

**Resolve the base branch next**, stopping at the first that answers: the spec's own `branch.base`
record, when one already exists; else `git symbolic-ref refs/remotes/origin/HEAD` (already read
above); else `git config init.defaultBranch`, and then `main`.

**Not on the base → adopt the current branch, and skip the cut.** `git branch --show-current`
disagreeing with the resolved base means the human already answered the isolation question at
checkout. Show the inference on the same line as the confirmation, before stamping —
`base: main — inferred; this branch was not cut by this command` — and stamp:

```bash
.agents/quenching/bin/cq specs record "<id>" branch --set base=<resolved base> --set work=<current branch>
```

Never derive `base` from `git merge-base` or `--fork-point`; they answer a commit, not a branch
name.

**On the base with the work ref alive and unclaimed → take a slot over it, unasked, never a
second branch.** `.agents/quenching/bin/cq git worktree acquire --branch <work ref> --base <base> --json`
switches a free slot (or a new one under `shared.worktreeSlots.max`) to the work ref, links the
declared shared paths and runs the declared `worktreeSetup` — displayed verbatim, as every cut does —
inside it. A ref already checked out in a slot returns that slot. **Nothing is stamped** — a
`branch` record, where one exists, is write-once and already true. At the ceiling the verb refuses
`git-worktree-slots-full` with each holder named: wait with `--wait`, and on
`git-worktree-slots-timeout` stop and tell the human the limit was reached — never cut a worktree
outside the slots.

**On the base branch, with nothing to check out → hand off to `/quenching-git branch`**, which
cuts the worktree from the remote base (its cost stated, `worktreeSetup`, the `branch:` record and
the branch's `quenching-specs:` marking). It is a worktree by default and asks nothing about the
form; a plain branch happens only when this run's input asked for one in the human's own words.
It is a `Skill` call, never `context: fork`:

```
Skill("quenching-git", "branch <id>")
```

When it returns, re-read the state it left:

```bash
.agents/quenching/bin/cq specs status --spec "<id>" --json
```

`branch.work` now set → the worktree was cut; continue the loop from the returned worktree checkout,
never from the base checkout — and carry that path on every command of the loop (`cd <worktree> &&
…` in the same call, `git -C <worktree>`, `.agents/quenching/bin/cq --root <worktree>`), because the harness may return
the shell cwd to the base checkout after each command and a cwd-dependent command would then
measure or write the wrong tree. **`.agents/quenching/bin/cq git …` takes no `--root` on its write verbs** (a usage error there): run it as `cd <worktree> && .agents/quenching/bin/cq git …`, in the same call as the `git add` it commits. No record at all → the
git command it ran failed (a name already taken, a dirty path, a locked worktree) and nothing was
stamped — report it verbatim and stop; never fall back to a branch or the base, which is the
outcome the worktree default exists to prevent.

Not a git repo → stop before writing: this command's task/commit contract requires Git. Never force
isolation, never `git init` on the human's behalf, and never rewrite history.

**2b. Probe the environment before writing any code** — a hook wired in `.claude/settings.json`
whose script is gone fails every commit. Probe once, in the same call as the reads above, per [execution.md](.agents/quenching/references/specs-execute/execution.md)
§The hook probe. Anything other than `none` → report it before the first task, name the hook and
the missing path, and let the human decide; never `--no-verify` past it. Silent when nothing wired.

**2c. On a resume, re-run the last checked task's `verify:` before any code.** A resume is a
`status` with at least one task checked. Take the last checked task and run its `verify:` (the
`## Validation` command when it has none) from its declared `cwd:`, in the same checkout the run
builds in. Green → go on to step 3. Red → **stop**: report the task, the command and its output
and write nothing, until the cause is classified. Reproduce the failure in the base worktree
(`.quenching/red`): it reproduces there → pre-existing, and the route of **5i** applies; it does not →
the regression belongs to the task that caused it, not to the conclude gate, and nothing is fixed
here. Nothing checked yet → run nothing.

**Done when:** the tree is clean, the worktree (or the requested branch) has been taken or found
already held, any unresolved hook has been reported, and a resume's last checked task still
verifies.

### 3. Settle the approval, off the state step 2 already read
**Load the stage ladder first** — what the derived stage names and means:

```bash
.agents/quenching/bin/cq components read .agents/quenching/references/specs-develop/spec-driven.md \
  --sections "§Derived stages"
```

The `.agents/quenching/bin/cq specs status --json` payload is **already in hand** from step 2 — do not read it again.
From it: the derived stage, the section states, task progress, the blocked tasks, the recorded
subjects, and **`verification`** — the spec's declared policy, which decides when the suite runs.
Whether a task goes to an executor sub-agent stays the two conditions
of [execution-delegation.md](.agents/quenching/references/specs-execute/execution-delegation.md)
§Delegating an executor, at every level.

- **`approved` unset** → ask for it inline, in one question showing what the spec commits to, and
  on a yes stamp it `--set by=human` (`.agents/quenching/bin/cq specs record "<id>" approved --set date=<today> --set
  by=human`). **Never refuse over it.** A no ends the run cleanly. **Under `autonomous`, `approved`
  unset is a real block**: never ask, never stamp — return `STATE: blocked` with `NOTE: needs-human:
  approval is develop's`.
- **`next` reports `write_section`** → the ready gate is not met. Name the missing or malformed
  sections and route to `/quenching-specs develop <id>`, then stop.
- **Every task already `- [x]`** → say so and offer to chain into `/quenching-specs conclude` (step 7).
- **`[!]` blocked tasks** → name them and their reasons up front. They were tried and stopped, not
  skipped.

Mention any open `.agents/quenching/bin/cq specs validate` warning **once** — `sp-unrefined` (nobody has interrogated
this spec), `sp-impact-uncovered` (a declared standard no task writes) — and offer
`/quenching-specs develop`. Never gate on it: the ready gate is a floor, not a verdict.
**Done when:** the state is in hand, `approved` is settled, and any warning has been surfaced once.

### 4. Read what the tasks must satisfy
Ask for the `build` moment — the six sections an executor needs — never by naming them:

```bash
.agents/quenching/bin/cq specs section "<id>" --moment build --scope current --json
```

Branch on the payload, never the exit code. `sections[].state`, already read once in step 2, is
the same fact this call's own `absent` list repeats: a section not yet `filled` — `## Handoff`
empty on a spec's first build is the ordinary case, not a finding — is read as empty.

`--scope current` cuts **only** `## Handoff`, and the other five sections arrive whole: the
evergreen global block plus the `### N.` of the section the next actionable task sits in, flagged
`"scope": "current"` in the payload. Drop the flag only to audit the whole history of the block.
The path comes from what `status` resolved; never assume filenames. `## Impact` names the
`/docs/standards/` paths and the code this spec expects to touch.

Then, if the repo carries an OKF bundle (`/docs/index.md` with `okf_version`), read the
`/docs/standards/**.md` files the spec **declares** under `## Impact`, plus the ones the current
task's own text names — **never the folder** `/docs/standards/<subject>/`, the wrong and the
expensive unit ([execution-tooling.md](.agents/quenching/references/specs-execute/execution-tooling.md)
§Tooling asides has the measurement). Those files are **binding contracts** for
HOW the work is built, complementing the spec's own sections (WHAT to build). A task that
contradicts one is surfaced (step 5), never silently resolved. No bundle → skip silently.

A declared bullet may carry a `§`address beside its path — `/docs/standards/automation/skills.md
§Invocation and permission are authored decisions §The admission criterion`. With one, read
exactly those sections (`.agents/quenching/bin/cq components read <path> --sections "§A" --sections "§B"`); with none,
read the file whole. Reading less is the spec author's assertion, never the executor's economy.

**Nothing scans the folder for a contract nobody declared**
([execution-tooling.md](.agents/quenching/references/specs-execute/execution-tooling.md)
§Tooling asides). The gap costs one line when it shows up — `.agents/quenching/bin/cq specs discover` records it while
building, and `/quenching-specs develop` repairs `## Impact`.
**Done when:** the spec's sections and the declared binding standards are read.

### 5. Implement tasks — loop until done or blocked
Ask the tool for the next task; **never pick one by reading the file**:
```bash
.agents/quenching/bin/cq specs next --spec "<id>" --json
```
It returns that task's `verify`, `files`, `pattern` and `parallel`, plus the spec's `verification`
policy, and it **skips `[!]` blocked tasks** (`action: "blocked"` once every remaining task is).

Then, for that task:

a. **Show what is being worked on** — the ID, its declared `files:` and its `verify:`.

b. **Write the code**, minimal and scoped to the declared files (a task tagged `[fix-preexistente]` declares its own, see 5i) — but only after the Codex
   consultation below, which is the first action of 5b for EVERY task, not a choice. A task that declares `files:` and
   writes nothing under `/docs/` **may** go to an executor sub-agent under
   [execution-delegation.md](.agents/quenching/references/specs-execute/execution-delegation.md)
   §Delegating an executor; when it is, load the rules that bound it before dispatching:

   ```bash
   .agents/quenching/bin/cq components read .agents/quenching/references/specs-execute/execution-delegation.md \
     --sections "§Delegating an executor"
   ```

   **The Codex executor comes first, one task at a time.** For EVERY task, before writing anything and
   unless stop-loss switched it off in this run, run `cq codex eligible --spec "<id>" --task "<task-id>" --json`:
   exit 0 → dispatch it, and act on its exit, per
   [execution-delegation.md](.agents/quenching/references/specs-execute/execution-delegation.md)
   §The Codex executor, loaded then; exit 1 or 2 → the executor above. Every task leaves one `Codex:`
   line, also when `eligible` refuses or stop-loss is on (`skipped`).

   **The `haiku` rung is per task.** For the `Task` executor, `.agents/quenching/bin/cq specs estimate --spec "<id>"
   --task "<task-id>" --json` reporting `smallTierEligible: true` admits a `haiku` pin for one
   attempt, under §Delegating an executor; anything else pins the session model.

c. **Write only the `/docs/` this task names.** When this task writes `/docs/`, load the rule that
   draws the line between declared and emergent, and the boundary it crosses:

   ```bash
   .agents/quenching/bin/cq components read .agents/quenching/references/specs-execute/execution-delegation.md \
     --sections "§Declared versus emergent"
   .agents/quenching/bin/cq components read .agents/quenching/references/specs-develop/spec-driven.md \
     --sections "§Boundary"
   ```

   A `/docs/standards/` path declared under `## Impact` and named by this task is part of its
   deliverable — written through the insert procedure in
   [knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md) §The frontmatter
   stamp §Updating `index.md` §Enriching the glossary §Self-check; stamp `authority` honestly and
   self-check against [knowledge-align/conformance.md](.agents/quenching/references/knowledge-align/conformance.md)
   §Concept docs §Resource integrity. Anything else the work reveals costs one line —
   `.agents/quenching/bin/cq specs discover "<id>" "<finding>"` — and no authoring.

d. **On the first pass through 5d–5e, load the rules the chain runs under — once, never per
   task.** The verification policy, the validation loop, the self-review, and the commit's hard rules;
   then the git conventions that name the subject — one call per file:

   ```bash
   .agents/quenching/bin/cq components read .agents/quenching/references/specs-execute/execution.md \
     --sections "§The verification policy" --sections "§The validation loop" \
     --sections "§The diff self-review" --sections "§The commit"
   .agents/quenching/bin/cq components read .agents/quenching/references/git/conventions.md \
     --sections "§The declared-directive layer" --sections "§The read-if-present rule"
   .agents/quenching/bin/cq git conventions --json
   .agents/quenching/bin/cq components read .agents/quenching/references/git/commit.md --sections "§Commit messages" --sections "§The subject is the anchor"
   .agents/quenching/bin/cq components read .agents/quenching/references/git/isolation.md --sections "§Marking the branch with the specs it built"
   ```

   **Self-review the task's diff** on the four items — reuse · useless defense · obvious comment ·
   dead code — and fix what it finds, on the written diff, *before* the chain below, so what the
   chain commits is already the reviewed version.

e. **Then verify, stage, commit and confirm the spec task as ONE chained operation.**
   The subject is the one the declared-directive layer's table and
   [commit.md](.agents/quenching/references/git/commit.md) §Commit messages
   §The subject is the anchor name, resolved from what 5d already loaded — never reload
   `/quenching-git commit` per task. Execute stages only the task's declared files, commits that
   index with `.agents/quenching/bin/cq git commit --subject`, which never stages, amends or skips hooks, then sends the
   subject and sha it reports to `.agents/quenching/bin/cq specs task --check`:

   ```bash
   <the task's verify:> \
     && git add <the task's declared files> \
     && .agents/quenching/bin/cq git commit --subject "<subject>" --json \
     && .agents/quenching/bin/cq specs task --check <task-id> --spec "<spec-id>" --subject "<subject reported by .agents/quenching/bin/cq git commit>" --commit "<sha reported by .agents/quenching/bin/cq git commit>"
   ```

   **The ordering remains explicit:** verify precedes staging, staging precedes the
   commit, and the spec tick follows the commit. Any failure stops the operation. A commit that
   already succeeded is preserved when the spec write fails; report its sha and retry
   `.agents/quenching/bin/cq specs task --check` without rebuilding or amending it. Run `verify:` only
   when the spec's declared policy says this task is a gate ([execution.md](.agents/quenching/references/specs-execute/execution.md)
   §The verification policy); otherwise the chain starts at `git add`. A task with
   **no `files:` declared** — the line absent, or `files: []` — has no diff to commit, so its
   chain *ends* at the tick, run without `--subject` or `--commit`, and with neither `git add` nor
   run `.agents/quenching/bin/cq git commit`. **A task that runs generators writes files, so it declares their outputs in `files:`**; with `files: []` the spec-verifier fails the scope of whatever it wrote. A `branch:` record also gets the branch marked, per the rule loaded in 5d.

f. **Read the chain's tail, and act on which link broke** — per §The commit, already loaded in
   5d: `verify:`/staging failed → nothing ticked, nothing committed; fix and retry, or block it
   when attempts stop converging. The commit failed → report with the task still unchecked. The
   spec tick failed after a successful commit → preserve the commit and retry only the remote
   write. The recorded subject or sha drifted → report it as a finding and write nothing further.

g. **Run and report the declared hook for this event, and move on.** Once the spec task has
   committed and been confirmed, `after_specs_execute_task` has fired. For every hook the config
   read returned, report the event, command, prompt, `optional` flag and any declared `condition`.
   **Never write `refs/remotes/origin/HEAD`** — it lives in the shared `.git`, outside this
   worktree, and nothing here writes it. The hook gets the base by name instead.
   **In a worktree the hook is never invoked.** When `git rev-parse --path-format=absolute --git-dir`
   and `git rev-parse --path-format=absolute --git-common-dir` differ in the checkout this run
   commits in (absolute, so a subdirectory of the main checkout compares equal), the build runs in a
   linked worktree and the session's hook would review the base checkout with an empty diff, then
   end the turn with the review text. Report each declared hook as above, classify it
   `skipped: worktree` — not a failure, no pause, optional or not — and go to step h; the security
   review of an orchestrated build is the epic's own. Outside a worktree, continue:

   Invoke the declared command as a `Skill` with the task and commit context:

   ```text
   after_specs_execute_task — hook: /security-review — optional: true
     condition: none declared
     prompt: none declared
   Skill("<declared hook command>", "<declared prompt plus task and commit context>")
   ```

   **Name the work checkout and the diff in that context.** A hook runs in the session's working
   directory, which the harness may return to the base checkout while this run builds in a worktree,
   so a hook such as `/security-review` diffs the base and finds nothing. Resolve
   `git rev-parse --show-toplevel` from the checkout this run commits in and pass, with the task
   and commit context, `work checkout: <toplevel>` and `review: git -C <toplevel> diff
   origin/<base>...HEAD`, where `<base>` is the base resolved in step 2,
   with the instruction to review that diff rather than the session's own checkout. The hook declaration is still passed untouched; only the target is added.

   **The hook's output is context, never the run's answer.** Whatever the hook returns (a
   `# Security review: …` text, for one) is read and the run goes on to step h; a run that ends
   with the hook's text instead of the fixed report is a defect. An orchestrated run always ends
   with the fixed report.

   `enabled: false` was filtered during the config read and is never announced or invoked;
   absence of `enabled` means enabled. The executor never evaluates `condition`: it reports the
   declaration and passes it as context to the hook. An optional hook failure is reported and the
   run continues; a non-optional hook failure is reported and pauses the run. Neither failure
   undoes the spec tick or rebuilds the already successful commit. **Classify every hook that
   was invoked as `ran`, `failed` (the command resolved and its run failed) or `unresolved` (no such
   command exists), and every hook not invoked in a worktree as `skipped: worktree`; carry the classification to the report** — an orchestrated run returns it
   as the spec-runner's `HOOKS:` line, so an optional hook that never resolved is never lost. A hook
   absent from config is silent.

h. **At a section boundary, keep the task commits and continue.** The branch is at a clean,
   independently anchored boundary; another section may still be ahead. **No session-size read runs
   here and the boundary is never a question:** do not measure the context, print a budget or offer
   a new session. Stop only when the workflow reaches a question that requires a human answer, or
   under the pause conditions below. See
   [execution-cadence.md](.agents/quenching/references/specs-execute/execution-cadence.md)
   §The section boundary.

i. **A red that is not this spec's has its own route, never a dismissal** — a failing gate, a broken
   test or drift found on the way, read per `/docs/standards/workflows/task-execution.md`
   §Pre-existing errors. **Diagnose, then prove it on the base**: reproduce it in the base worktree
   (`git worktree add --detach .quenching/red <base>`, removed before returning, per
   [execution.md](.agents/quenching/references/specs-execute/execution.md) §The validation loop); no
   reproduction → it is this spec's own and the validation loop applies. Reproduced, the four criteria
   hold together — cause diagnosed and reproduced on the base, few files and no contract changed, an
   existing verification proves it, no human decision — → **fix it in the spec**: append a task
   `[fix-preexistente] <title>` with its own `files:` (generated mirrors included) and a `verify:` that
   fails before and passes after, through `.agents/quenching/bin/cq specs section "<id>" Tasks --write`
   (the checked boxes and their `subject:` survive) then `.agents/quenching/bin/cq specs validate`, and
   run it through this loop. Any criterion failing → **capture it now**, while the context is warm:
   `.agents/quenching/bin/cq specs new` with `## Problem` and the diagnosis (cause, reproduction, files), and
   one `## Discoveries` line `promoted: <id>` on this spec. Never skip, `xfail`, suppress or delete a test,
   edit a `verify:` or use `--no-verify` as the fix. An uncorrected pre-existing red stays a FAIL of the
   gate, and the PR names the base red and the spec that captures it.

**Pause if:** a task is unclear; implementation reveals a design problem (→ `/quenching-specs develop`); a
task contradicts a `/docs/standards/` contract (surface it and let the human pick — revise the
standard via `/quenching-knowledge add`, or the spec via `/quenching-specs develop`); attempts stop converging; or the user
interrupts.

**Under `autonomous`, the pause is the real block**, the closed list of
`/docs/standards/workflows/task-execution.md` §After approval, the executor decides, records and stops
only on a real block: attempts stop converging; a standard contradiction the evidence does not
settle; a new grant, a changed frontmatter key or a lowered bar; an undeclared irreversible, merge, push or deploy
action; a required proof that cannot run; `approved` absent. A proof the spec requires that cannot
run for a missing environment variable, credential or service is a real block — never skipped in silence,
never marked as passed. An uncontained pre-existing red (5i) is that real block: capture the spec, name
it in `needs-human:` and stop. Anything else the evidence or the recorded intent answers is decided and
recorded as one line, `.agents/quenching/bin/cq specs discover "<id>" "decision: <what> — <evidence or criterion>"`; a
`decision:` line is not a Handoff event.
**Done when:** every task is `- [x]` or `- [!]`, or the run pauses with the reason stated.

### 6. Refresh `## Handoff` on four events, never on judgment
Rewrite the **relevant block** of `## Handoff` to the state of play a fresh executor would need
**and cannot derive** — on exactly four events:

- the run **pauses**;
- a task is written **blocked**;
- a **discovery** is recorded;
- the run's **last commit** lands.

`## Handoff` is a small evergreen **global block** plus one block per `## Tasks` `### N.` section.
A rewrite touches ONE of the two — never both, never a closed section's:

```bash
.agents/quenching/bin/cq specs section "<id>" Handoff --write --scope global   # the evergreen block
.agents/quenching/bin/cq specs section "<id>" Handoff --write --scope current  # the block of the open section
```

Everything a resumed run *can* derive — which tasks are done, which commit carried each — is
already in `git log` and in the `subjects` `status` returns; the Handoff is not the resumption
trail. The global block plus the current section's block are sent with every task, so keep both
small. `--scope current` always resolves to whichever section still has open work.

**Not after every committed task, and not on a judgment call either** —
[execution-cadence.md](.agents/quenching/references/specs-execute/execution-cadence.md) §The
Handoff cadence.

**Done when:** the global block and the current section's `## Handoff` block describe the tree as
it stands after the run's last commit.

### 7. Report, and hand off

**When this run's input carried `autonomous`, skip the read below and the mold with it** — a conductor presents the report, not this run; return the content in the fixed shape the caller specified. Otherwise:

```bash
.agents/quenching/bin/cq components read .agents/quenching/references/specs-develop/report-mold.md \
  --sections "§The report mold" --rules-only
```

Emit §The report mold. The single-spec header line carries overall progress; three body blocks:

1. **This session** — fixed. The isolation and its `branch` record, tasks completed, each task's
   commit and the subject recorded for it, any subject that drifted, and each `Codex:` line of 5b.
2. **Blocked** — optional. Each blocked task with its reason.
3. **Written along the way** — optional. The standards written, and the discoveries recorded,
   **quoted** as `/quenching-specs develop` quotes them: the same artifact is owed the same fidelity
   whichever command wrote it.

Print §The session-size line before the block — `.agents/quenching/bin/cq components session context --json`, read once; exit 1 turns the `→` line into a recommendation to run the next stage **in a new session**, exit 0 and 2 leave it an offer.

**Name what git needs.** Add one line stating what the git flow owes the branch next and what to review first: `/quenching-git pr-create <id>` (or `/quenching-git merge`) after `/quenching-specs conclude`, over the diff `origin/<base>...HEAD`. Execute runs none of it: git is the main session's flow, and in an orchestrated run the `spec-orchestrator` publishes after the `spec-verifier` PASS.

Close on §The next-step block, its recommended line carrying **this spec's id**:
`/quenching-specs conclude <id>` at 100%, `/quenching-specs execute <id>` when the run paused
mid-plan.

**At 100%**, after the block, offer once to chain straight into `/quenching-specs conclude` (the `Skill` tool, which takes
the registry name): the branch review,
the emergent `/docs/`, the merge, and the archive-time distillation. Declined → the block already
named the command, so stop. Over budget (the session-size line's exit 1) → the offer's first option
(A, recommended) is to run `/quenching-specs conclude <id>` in a new session, the measured peak in
its own text; chaining here stays an option. Paused → say why and wait.
**Done when:** the summary is shown and the hand-off has been offered or declined.

## Output during the loop

Progress as it happens, not a report — the mold governs step 7, this governs the loop. Its glyphs
are §The report mold's, and mean the same. **Only the mold's own header line opens with a `##` that
carries the ID** — this banner prints plain text, never a heading, so the two can never be
confused for each other:

```
Building: <id>

Task 3/7 — 3.2 <task title>
  files: src/middleware/auth.ts, src/config/limits.ts
✓ self-review: clean
✓ chain: verify && check && commit
    verify: pnpm test middleware/ — passed
    checked 3.2 (subject: plan/<id>-<handle>: 3.2 <task title>)
    committed a1b2c3d — subject matches
```

## Hard rules — no exceptions, and no "just this once"

Every way a build ships a lie with a green checkbox is enumerated in
[execution.md](.agents/quenching/references/specs-execute/execution.md) §The commit, and
each is absolute. The three whose failure is **silent and permanent**:

- **Never edit the `verify:` command, the test, or the assertion** so it stops failing — and never
  disable, skip, `xfail` or delete a test. Change the code, or report the task blocked.
- **Never amend or rewrite an earlier task's commit**, and never force-push.
- **Never tick a checkbox for work that was not verified.** The spec task is ticked only after
  the task was verified, self-reviewed and committed. If nothing could verify it, say so in the
  report rather than implying the task was proved.

## Invariants to never violate

- Require a clean tree before the first code change; **isolated means this checkout is ON the work
  ref**, never that the ref exists somewhere — and where it is not, cut a worktree over a live ref
  or hand off to `/quenching-git branch` to cut one. The worktree is the default and is taken
  unasked; a plain branch only when the run's input asked for it.
- Drive off `.agents/quenching/bin/cq specs status` / `next` / `task` and their exit codes. Never assume a path, never
  choose the next task by reading `## Tasks`, and never hand-edit a `- [ ]` / `- [x]` character.
- Verify per the spec's **declared** policy. Never decide mid-build when to test, and never ask the
  human to decide it then.
- Tick each spec task **after** its commit succeeds — with the subject and sha that commit
  actually carried. A failed spec write leaves the commit preserved and the task unchecked for
  a retry; never rebuild or amend that commit.
- Never write an ordinary record after the commit it describes. The spec task is the explicit
  post-commit exception and carries that commit's subject and sha; no later history rewrite or
  anchor repair is part of a section close.
- Never refuse over a missing `approved`; ask inline and stamp it with `.agents/quenching/bin/cq specs record`, never by
  editing the frontmatter. The one exception is `autonomous`, where it is a real block.
- Stamp `branch:` once the work ref is resolved, taken or declined (`work` then equals `base`) —
  never over an existing record, through `.agents/quenching/bin/cq specs record`, never the frontmatter.
- Write **only** the `/docs/` a task explicitly names. Emergent findings are one `.agents/quenching/bin/cq specs discover`
  line — never an unrequested standard, and never a loose code comment. A pre-existing error is the
  exception (5i): a declared `[fix-preexistente]` task, or a captured spec resolved `promoted: <id>`.
- Delegate an executor only under
  [execution-delegation.md](.agents/quenching/references/specs-execute/execution-delegation.md)
  §Delegating an executor (declares `files:`, touches no `/docs/`, pinned to the session model —
  `haiku` only on a task `.agents/quenching/bin/cq specs estimate --task` reports `smallTierEligible`), and run two tasks in parallel only when `.agents/quenching/bin/cq specs parallel` reports the `[P]`
  group eligible.
- Never review the whole branch, merge, or archive from here — that is `/quenching-specs conclude`.
- Keep changes minimal and scoped to each task; pause on errors, blockers, or unclear requirements
  rather than guessing.
- **Route other durable learning to its OKF home**: an insight worth keeping beyond this spec goes
  through `/quenching-knowledge learn` / `/quenching-knowledge add` / `/quenching-knowledge define` — offered, never auto-written.
