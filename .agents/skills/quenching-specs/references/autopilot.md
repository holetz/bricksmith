---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Bash(git:*), Bash, Read, Grep, Glob, Agent, Task, TaskStop, SendMessage
---

# /quenching-specs autopilot — one round of improvement; more only on request

**Input**: `$ARGUMENTS` — no flag runs **ONE round** (steps 1–8) and stops. `--until-dry` repeats
rounds until the queue runs dry; `--until <date-time with offset>` (e.g. `2026-10-09T07:00-03:00`)
repeats them until the deadline (and still stops at a dry queue). `--max-launches <n>` sets the
launch ceiling; without it the ceiling is `shared.autonomy.maxLaunches` from `.agents/quenching/bin/cq config show --effective --json`
(default **40**, provisional: it is fixed only after the human reads the measurement of the last three runs), and the flag wins over the key. `--max-tree-tokens <n>` sets an opt-in ceiling on the tokens measured per
model across the tree, with **no default**: without it nothing is read or compared. Optionally focus areas as in `/quenching-specs improve`.
Typed by the human only: **the invocation is the human's delegation** for the whole run — every
forced execution, push, PR and merge below is authorized by it, and by nothing else.

This session is the **conductor**. It decides as the owner's delegate, keeps only summaries, and
never builds: reading, mapping, decomposing, building and reviewing are agents
(`quenching-spec-scout`, `quenching-spec-orchestrator` or, in economy mode, the spec-runner/spec-verifier pair with this session publishing, and the read-only opus
`quenching-spec-reviewer`, which this conductor launches at step 6). Decomposition is a step of develop, run in this session, never an agent. The spec-orchestrator's protocol (waves, workers, spec-verifier, acceptance, publication) lives in
`quenching/agents/spec-orchestrator.md`; do not restate it.

**Why `Bash` is unrestricted here.** Step 1 and step 7 run the gate the target's root harness
declares in its operating section (`AGENTS.md`; legacy `AGENTS.md`), whose command no fixed grant can name. No other
non-`cq`, non-`git` command is run.

## Invariants

- **Never forced.** A decision that **widens** a grant or a surface that executes code (hooks,
  scripts, CI, `allowed-tools`, an agent's `tools:`), or that chooses between designs of this
  autonomous protocol itself, is never taken by the conductor. It goes to the run's epic
  "decide with the human" — one per run, created on first need — and the round continues without
  it. **Narrowing** a grant is not widening it.
- **Relevance over activity.** A spec enters the run only with evidence (`path:line`, a command's
  output, a measurement) and a concrete consequence — the bar of `/quenching-specs improve`. "Nothing
  left to polish" is never a reason to invent work.
- **Findings become specs, never hot fixes.** What the correctness review finds is a spec of the
  next round; the conductor never patches `main` itself.
- **Liveness.** A worker with no activity for more than **20 min** (its transcript's mtime) and no
  live child process is stopped with `TaskStop` and relaunched **once**. `shared.autonomy.livenessMinutes` replaces the 20 minutes when set. A stall is a claim about
  the present: right before `TaskStop`, read the mtime and the child check **again**, stop only
  when the second reading still shows the stall, and write both readings (time, mtime, child
  present) in the round record beside the relaunch. A second stall goes to the
  final report, never a third launch.
- **Git is read-only for every agent this session briefs, except the orchestrator's workers.**
  Each brief forbids `git stash`, `checkout`, `switch`, `reset`, `restore`, `rebase`, `clean`,
  `commit --amend` and force-push. After every agent returns, audit `git status --short`,
  `git stash list` and `git worktree list`; any change is reported, never silently reverted.
- **Cost.** At most 3 workers at a time; each brief ≤ 1,500 characters, each return ≤ 800 words.
  Wait for completion notifications instead of polling. One correctness review per epic, never per
  spec. The conductor reads summaries, never diffs or file dumps.
- **The launch ceiling binds the whole tree.** Every `Agent`/`Task` launched in the run counts —
  children, grandchildren reported by returns, Liveness relaunches and follow-ups — in the
  `launches` field of the round record. Before each launch the conductor compares it with
  `--max-launches`; at the ceiling it admits nothing more, writes the record (`open`, next step)
  and reports `continue`. The count covers what the conductor and the returned reports show; what
  is in flight is declared `not measured`.
- **The tree-token ceiling is opt-in and per model.** Only with `--max-tree-tokens <n>`, before each
  launch the conductor reads `.agents/quenching/bin/cq components session cost --json` (its default is the most recent session for this cwd, the conductor's own) and
  compares, per model, the measured tokens (`input + cacheRead + cacheWrite + output` of that
  model's `byModel` row) with `<n>`, never summing tiers. A model already at the ceiling, or with no
  margin (its measured total plus the largest measured total of a single agent of that model in the
  tree reaches `<n>`; with no agent measured, only the raw ceiling counts), admits nothing more: the
  conductor writes the record (`open`, next step) and reports `continue`. What is in flight or
  unmeasured is declared, never estimated.
- **A `budget:` continue is relaunched, not retried.** When the orchestrator returns `continue`
  with `NOTE: budget: …` — its own conductor budget, measured between waves, never a worker's size
  estimate — the conductor launches it again in a fresh context on the same epic. The
  relaunch counts in `launches` and is not a failure, but needs progress: at least one more spec
  `done` or `merged` than at the previous return. Without progress the epic goes to the report as
  `needs-human`.
- **A `needs-human:` already answered is RESUMED, not relaunched.** Keep the `agentId` every
  `Agent`/`Task` result returns. When an orchestrator (or, in economy mode, a `spec-runner`) returns
  `blocked` with `NOTE: needs-human: …` and the question is answered — by the human, or by this
  session as the delegated owner — write the answer as a dated bullet in the epic's `## Design`
  first (step 4's read, append, write), then hand it back to the SAME agent with
  `SendMessage({to: <agentId>})`, naming the epic and the bullet: the transcript is still warm, and
  a fresh launch pays to re-read spec, protocol and code for a question decided in minutes. **The
  window is not a constant:** read `.agents/quenching/bin/cq components session cache --json` (`boundaries`, per harness
  and model) and compare it with the elapsed time; `docs/standards/automation/context-discipline.md`
  §The prompt cache is a per-harness measurement measures the documented five-minute subagent
  lifetime of the host as the case, and this body assumes no number of its own. A cold relaunch stays, and is
  never a resume, for `budget:` (the invariant above), for a host that exposes no channel (no
  `SendMessage` in your grants, or a handle that cannot be messaged), for an `agentId` lost, for a
  decision taken outside the measured window, and for a decision that repeats one already in the
  epic's `## Design` — that repetition goes to the report with the two notes, `needs-human` and the
  decision it already carried, never a second `SendMessage`. **A resume is not a launch:** it counts
  in `resumes:` of the round block and never in `launches`.
- **The round record is resumable.** Each round writes `<ephemeral area>/autopilot/<date>/rounds.md`
  (one block per round: number, start ref, epic, specs placed, `launches`, `resumes`, next step,
  `open|closed`) at the end of every step 1–7. A round that returns `continue` stays `open`.
- **The deadline binds launches.** With `--until`, no worker starts if it cannot finish before the
  deadline; what is in flight at the deadline is reported `continue` with its state.

## Workflow

### 1. Health
Resolve `cq` per [align/tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool; branch on the exit code. Read the root harness for the gate command and the
ephemeral area. Check, on `main`: `git stash list` is empty, `git worktree list` holds only this
run's worktrees, and the gate is green. Anything red there → this round's first spec is fixing it
(`.agents/quenching/bin/cq specs new`, then steps 4–5 on it alone).

**A dirty checkout is decided HERE, never discovered at the first worktree cut.** Read
`git status --short` and split what it shows. A path the round record's `dirty:` line already names
opened the round with it — it is the human's work, an upgrade or a fix in progress — while a path
that appeared after the round opened is this run's, and the audit of the invariants above handles
it. For the human's, this session decides as the delegated owner, and the invocation authorizes
both answers: when a new spec is permitted, the fix spec above absorbs it; when it is not, every
worktree of the round is cut from `origin/<base>`, which never carries the base's uncommitted
files, and the worktree cut proceeds on that authorization with the base still dirty, reporting
the paths it left in it. With neither answer the round stops before its first launch: `STATE: blocked`,
the dirty paths named. The answer is written in the round record as `dirty: <n> path(s) — <fix
spec|cut from origin/<base>>` before anything launches, and step 4 copies it into the epic's
`## Design`.

Then run
`.agents/quenching/bin/cq git preflight --json` once (covered by the existing `Bash(cq:*)`; no new grant) and write its
five checks (`store`, `types`, `push`, `ghAuth`, `ci`) in the round block as `preflight:`. `store`,
`push` or `ghAuth` in `fail` → fixing it is this round's first spec, like a red gate. `mergesAllowed:
false` (CI unavailable) → steps 4–5 plan no merges and no chain that depends on a merge, and the
report says why. Hooks are never disabled and `--no-verify` is never passed. Read the
round record first: an `open` round whose start ref is still reachable from `main` resumes at its
recorded next step and keeps its `launches`; any other record opens a new round. Record the
round's start ref: `git rev-parse main`.
**Done when:** `main` is green, and either the checkout is clean or the dirty base was decided —
a fix spec, or an authorized cut from `origin/<base>` — and recorded.

### 2. Queue
List the loose specs — open, in no epic — with `.agents/quenching/bin/cq specs list --lean --json`, and the store's
errors with `.agents/quenching/bin/cq specs validate`. Before any of them enters, one `quenching-spec-scout` per batch proves
it is still relevant against the current code:

- the surface it describes has changed → record the evidence in `## Outcome`
  (`.agents/quenching/bin/cq specs section <id> Outcome --write`) and `.agents/quenching/bin/cq specs promote <id> --outcome abandoned`;
- it needs the human (the **never forced** list) → `.agents/quenching/bin/cq specs link add <human-composite> <id>`;
- otherwise it joins this round's queue.

**Done when:** every loose spec is queued, abandoned with evidence, or in the human epic.

### 3. Map
Only when the queue is short (fewer than three specs): run the mapping of
`/quenching-specs improve` steps 1–3 — read-only `quenching-spec-scout` agents per area, evidence plus
consequence, no duplicate of an open spec, at most 15 candidates, every `high` re-checked — with
this session deciding the owner's questions as the delegated owner, except those on the
**never forced** list, which go to the human epic. Focus areas from `$ARGUMENTS` narrow the areas.
The map is ephemeral, under `<ephemeral area>/autopilot/<date>/`.
**Done when:** the candidates kept are listed with evidence, or the map found none.

### 4. Composition
**Mechanical capture, by default.** When the queue and the kept candidates already decide the
members (title, `complexity`, dependencies), this session creates the composite and the members
itself: search by title in `.agents/quenching/bin/cq specs list --json` first
(a match is reused), then `.agents/quenching/bin/cq specs new "<title>" --complexity <level> </dev/null` per member and
`.agents/quenching/bin/cq specs link add <composite> <spec> --group "<wave>" --after <labels>` (labels `S<n>` returned by
earlier `link add` calls), serializing any two members that touch the same files with `after:`.
Exit 2 is read, never repeated blindly. The model restrictions on code and classification stay as
they are.
**Decomposition, by this session.** Members not yet decided, or waves that need reading file
overlap, are decomposed here — never on a token estimate: propose the slices from the queue and the kept candidates,
then create the composite and the members with the same `.agents/quenching/bin/cq specs new` and `.agents/quenching/bin/cq specs link add`.
Either way, write the composite's `## Design` — and write it as a READ, AN APPEND AND ONE WRITE,
because `.agents/quenching/bin/cq specs section --write` REPLACES the section: `.agents/quenching/bin/cq specs section <composite> Design
--json`, the round's dated bullets appended, the body back on stdin. It carries the **autonomy
policy** (forced execution, push, PR and merge of every member after a verifier PASS, authorized by
this invocation, with its date) and **every decision this session took as the delegated owner**,
each a dated bullet with the evidence it rests on — the step 1 dirty-checkout answer, an abandoned
spec, a spec sent to the human epic, a proof not run — written BEFORE the launch that depends on
it. A decision that lives only in a brief is lost at the next relaunch; the briefs of steps 5 and 6
name the composite and the bullet instead of restating it. Check the result with
`.agents/quenching/bin/cq specs status --epic <composite>` and `.agents/quenching/bin/cq specs validate`.
**Done when:** the composite exists with its waves, its `## Design` carries the dated policy and
this round's decisions, and `validate` raises no `sp-link-*` finding.

### 5. Orchestrate
**Economy mode.** An epic with **at most 2 members, every one `low` or `medium`**, runs without
`quenching-spec-orchestrator`: this session launches `quenching-spec-runner` →
`quenching-spec-verifier` for each member and, after the PASS, publishes it itself
(`/quenching-git pr-create autonomous`, then `.agents/quenching/bin/cq git pr merge` on green CI), with the acceptance rules
of the spec-orchestrator's protocol, cited and not restated — steps 3 (delegation briefs), 4
(acceptance only on a spec-verifier PASS, the `execute-done:`/`conclude` two spawns, publish after the PASS,
one re-spawn) and 5 (retry, idempotency) of `quenching/agents/spec-orchestrator.md`.
Each prompt is ≤ 1,500 characters, and it OPENS with the fixed text every prompt of that type
shares — the word `autonomous`, the base, the model passed explicitly and the sentence "No git
stash, checkout or reset; work only in your worktree." — and CLOSES with the variables: the spec
id, the worktree, the epic id and the decision it carries. Fixed prefix first is what lets two
prompts of the same type share a cacheable prefix; the gain BETWEEN distinct agents is ASSUMED, not
measured in this repository — the table of `context-discipline.md` §The prompt cache is a
per-harness measurement measures generations inside one agent — and `.agents/quenching/bin/cq components session cache` is
the measurement that would confirm or refute it. Two members with a
dependency run in series; without one, in parallel (never more than 3 workers). Every launch counts
in `launches`, and the ceilings of the invariants above bind it. No new grant: this session already
holds `Agent` and `Task`.
**Otherwise** (3 or more members, or any `high`/`xhigh`), start `quenching-spec-orchestrator` on
the epic with the word `autonomous`, the base branch and the deadline (≤ 1,500 characters), its
brief ordered the same way: the fixed sentence and the word `autonomous` first, the epic id, the
base branch, the deadline and the decision last.
Apply **Liveness** to every worker, in either mode. A spec returned `blocked` with a `needs-human:`
note takes the resume rule of the invariants: answered inside the measured window, it goes back to
the same agent by `SendMessage`; otherwise it moves to the human epic. `failed` or `continue` is
carried to the report with its note, never
retried in this round; a `continue` whose note starts with `budget:` — the orchestrator's conductor
budget, never a runner's size estimate — is the exception, relaunched as the invariant above says.
**Done when:** every member is `done` or placed (economy mode), or the orchestrator returned its
fixed report and every non-`done` spec is placed.

### 6. Correctness review
After every epic that changed code, start `quenching-spec-reviewer` once (`model: opus`) over the epic's
merges (`git log --merges <round start>..main`); the orchestrator does not launch it. It is
read-only, and the gate is the only thing it executes. It reports only findings it **reproduced** —
a command, its output, the `path:line` — never style, and a security review of the same range when
the epic touched a grant, a hook or anything that executes. That review hunts the widening a worker
makes by mistake, never deliberate evasion of the audit (symlink, BOM, line separators, a diff
driver in `.git/config`): the audit is no sandbox. Never a second review of the same range.

Each finding the reviewer reproduced becomes a loose spec (`.agents/quenching/bin/cq specs new`, then its `## Problem` with
the reproduction), queued for the next round; `NONE` creates none.
**Done when:** the review returned and each reproduced finding is a spec.

### 7. Gate and audit
On `main`: pull, run the gate, and the audit of step 1. Red → the first spec of the next round.
Then decide whether to stop:

- **deadline** — `--until` is reached, or no worker of a next round could finish before it;
- **dry queue** — a whole round in which the queue (step 2), the map (step 3) and the review
  (step 6) produced **no** spec with evidence and consequence.

Neither → without `--until-dry`/`--until`, close the round record (`closed`), print step 8 and
**stop: one round is the default**. With either flag, close the record and open the next round in a
**fresh context**: launch one sub-agent that follows this body from step 1 with the record as its
only input (counted as a launch). If a sub-agent cannot launch sub-agents, stop instead, with the
record `closed` and the next step printed as the invocation to resume in a new session
(`/quenching-specs autopilot --until-dry`).
**Done when:** the gate and the audit were read, and the run either stops or starts the next round
the flags asked for.

### 8. Final report
Print: the launches used against `--max-launches` and the resumes it made; per epic, the merged
specs with their PRs; the human epic and its members; every decision
this session took as the delegated owner; specs in flight at the deadline, as `continue` with their
state; second stalls; and the tokens the run spent per model and agent type, read with
`.agents/quenching/bin/cq components session cost` and never summed across tiers.
**Done when:** the report is printed.
