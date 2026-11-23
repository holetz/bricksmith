---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Bash, Read, Glob, Grep, Write, Edit, AskUserQuestion, Skill
---

# /quenching-specs conclude — review, distil, sweep, hand off — and archive after the merge

**Input**: `$ARGUMENTS` — the spec id, and optionally its outcome. A conductor launched with
`--autonomous` adds the word `autonomous`; for a `done` outcome the run then opens the PR itself
after the green gate, and the `spec-orchestrator` adopts that PR on the `spec-verifier` PASS
instead of opening a second.

Closes ONE spec out in two moments, split by the merge. **Before the merge**, five things happen,
in this order, and each is a separate decision: the whole branch is **reviewed**, the `/docs/` the
work *revealed* is **written**, `## Outcome` is **written** and the spec **distilled** into the OKF
bundle, the project's own automation is **swept** for what the run revealed about it — every real
defect captured as its own spec, for another session — and the pre-merge gate is **proven green** —
then the run **stops**, naming `/quenching-git pr-create` or `/quenching-git merge` as the human's
own next command — except that a spec recorded at `low` complexity chains directly to
`/quenching-git pr-create` through `Skill` after the green gate; under `autonomous` that same
invocation carries the word `autonomous` and runs unasked. **After the merge**, and only then, the
spec is **archived** and its worktree released (step 8).

**A `done` spec is archived after its merge, never before.** The spec store is a git ref, not a
folder inside the PR: an archive is visible to every reader the moment it is written, while the PR
it describes can still stall, fail its checks or be closed. `cq specs promote --to archive
--outcome done` refuses while the work ref is not in `origin/<base>`, and `--force` does not reach
that refusal. The post-merge close is one verb, `cq specs reconcile --write`, and every route to a
merge ends in it: this command resumed after the merge, `/quenching-git merge`, the
`spec-orchestrator` after `cq git pr merge`, and the reconciliation at the open of
`/quenching-specs execute`, `orchestrate` and this command.

**Done work ends on the work branch; abandoned close-out writes land in the checkout holding the
base.**

The distillation doctrine — what crosses into `/docs/`, what stays, and how it is graded — lives in
[specs-conclude/distill.md](.agents/quenching/references/specs-conclude/distill.md)
§What crosses, what stays. Every hand-off into another module's command (`/quenching-align`
for the bundle) runs only where that module is enabled, per
[tool-resolution.md](.agents/quenching/references/align/tool-resolution.md) §Routing into
another module; with `quenching-specs` alone, the distillation still writes through the shipped
insert procedure and only the offers into `quenching-align` drop. The layout and the gates live in
[specs-develop/spec-driven.md](.agents/quenching/references/specs-develop/spec-driven.md)
§The spec document §The gates and the stage-scoped explicit-none rule. The `.agents/quenching/bin/cq specs`
surface lives in
[specs-surface.md](.agents/quenching/references/specs-develop/specs-surface.md)
§The `.agents/quenching/bin/cq specs` tool surface; the report shape lives in
[report-mold.md](.agents/quenching/references/specs-develop/report-mold.md)
§The report mold.
## Resolving the tool

Resolve `.agents/quenching/bin/cq specs` per
[align/tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool. Branch on the **exit code** (0 ok · 1 findings · 2 refusal) and the `--json`,
never on prose.

## Doctrine

- **The outcome is stated, never inferred.** `done` and `abandoned` are opposite claims about the
  same file, and nothing — not task progress, not staleness, not a sweep — may decide which was
  meant. If the human has not said, ask — under `autonomous`, refuse instead (§Under `autonomous`).
- **Concluding as `done` refuses to lie.** Open `- [ ]` boxes with `--outcome done` is exit 2 with
  the list. `--force` exists for the case where the human knows why — the work was descoped, or
  proven elsewhere — and says so.
- **Abandoning is always allowed.** `--outcome abandoned` never refuses and never needs `--force`.
- **`done` distils; `abandoned` does not.** Concluding as done mints by-products into `/docs/` as
  knowledge the product **adopted**. An abandoned spec harvests at most what was learned by *not*
  building it, as
  `authority: background`; a decision it *would* have made never crosses.
- **An abandoned spec's branch is offered for deletion, never handed off toward a merge.** Partial
  work on a branch nobody adopted is history, not a change; offer to keep it or delete it, and
  default to keeping. This is the one branch-disposal decision that stays inline here rather than
  in `/quenching-git cleanup`.
- **Everything `abandoned` writes lands in the checkout holding `<base>`, never this branch** — see
  [specs-conclude/abandoned.md](.agents/quenching/references/specs-conclude/abandoned.md)
  §Locating the checkout and writing into it §The branch-delete offer, informed rather than defensive.
- **Declared rules were already written.** The `/docs/standards/` a task explicitly named went in
  during execution, honestly graded. What lands here is what the work *revealed*.
- **What a standard attaches to the *merge* is settled at step 5, not built as a task** — a version
  bump, a changelog entry, a manifest re-stamp.

## Resuming

Read this before deciding what to run. Each stage has one durable signal, and a stage whose signal
is already set is **reported and skipped**, not repeated:

| Stage | Signal it already ran | On resume |
| --- | --- | --- |
| review | `reviewed: {date}` in frontmatter | skip; offer a re-read only if the diff grew since |
| emergent `/docs/` | it rides with the review — same stage, same commit | skipped with the review |
| `## Outcome` | the section is filled | skip the draft; go to distil |
| distil | no record — it is offered once per conclude | offer it; an empty harvest is a valid answer |
| release obligations | the branch diff already carries what the standard requires | report it satisfied; re-read the standard only if the diff grew |
| flow sweep | no record — it is offered once per conclude; the specs it captured are their own evidence | read the front for the captured ids and report them; re-sweep only when this run revealed a new defect |
| validation gate | no record — it is a verdict on the tree as it stands *now* | always re-run it; a green run from before the last commit proves nothing |
| already merged | the work ref is in `origin/<base>`, or a `merge` record exists, or the `pr` record's PR is merged | the hand-off is done: run only step 8, the post-merge close |
| archived | the spec is in `archive/` with `outcome:` stamped | report it closed; nothing is left to run |

`reviewed` is `writeOnce: false`: a diff read again is a new fact. `outcome` is `writeOnce: true` —
if it is already set and reality disagrees, that is a **finding to report**, never a value to
overwrite. `merge` and `pr` are read only to answer the "already merged" row above, and for
`abandoned` from the base checkout (Doctrine).

## Under `autonomous`

A run whose input carries `autonomous` has no human and no decision sheet: it asks nothing.
Every point where this command would ask takes the one answer below — the narrowest that lets the
step run — and nothing outside this table is granted. A case the table sends to `needs-human`
stops the run with `STATE: blocked` and `NOTE: needs-human: <the question>`.

| Where it would ask | The answer under `autonomous` |
| --- | --- |
| Step 1 — the outcome | taken **only** from the input's `--outcome`; absent → `needs-human`. `autonomous` never supplies an outcome |
| Step 1 — which spec (several marked ids, or no marking) | `needs-human` |
| Step 1 — open tasks under `done` | `needs-human`; never `--force`, never a switch to `abandoned` |
| Step 3 — the emergent `/docs/` plan | `done`: the narrated plan is applied on the work branch, unconfirmed — the PR is its human review. `abandoned`: declined; the candidates are named in the report |
| Step 4 — the `## Outcome` draft | written unconfirmed; for `abandoned`, an exit 2 from `promote` → `needs-human` with the refusal quoted, never `--force` |
| Step 5 — the distillation plan | as step 3: applied on the work branch for `done`, declined and named for `abandoned` |
| Step 5 — the release obligations | `done`: applied on the work branch as narrated, the standard quoted in the report |
| Step 6 — the flow sweep | `done` and `abandoned`: every evidenced candidate is captured unasked with `.agents/quenching/bin/cq specs new`, one spec per candidate, its `## Problem` carrying the inputs; none is developed, and the ids are named in the report |
| Step 7 — branch behind its base | the base merged in (`git merge --no-edit origin/<base>`, never a rebase), then the gate; a conflict outside the generated mirrors → `git merge --abort` and `needs-human` with the paths |
| Step 7 — an inconclusive check | `needs-human`, the check named |
| Step 7 — the abandoned branch-delete offer | keep, recorded in the report |
| Step 8 — the post-merge close | no question exists: `reconcile --write` and the worktree's release run as written; a `needs-human` row or a refusal is reported and never forced |

A red gate is `STATE: failed`, never a question — unless the red is a pre-existing error (step 7). For a `done` outcome step 7 opens the PR itself;
for `abandoned` there is no handoff to make.

## Workflow

### 1. Resolve the spec, the outcome, and what already happened
An ID in the input → use it. **No ID given → try auto-discovery first**, off the current
branch's own marking:
[auto-discover.md](.agents/quenching/references/specs-conclude/auto-discover.md)
§Reading the marking §Filtering to valid IDs §Resolving what remains, cited rather than
restated. One valid id resolves it silently, naming the marking as the source; more than one asks
which, on a decision sheet. No valid marking → §The fallback there measures the diff and always
asks whether to materialize a minimal spec — accepted, its new id is used from here on exactly
like a marked one. **Declined, headless completion — closing with no spec file at all — is not
built**: say so, record it with `.agents/quenching/bin/cq specs discover`, and fall back to `.agents/quenching/bin/cq specs list --json` and
ask. Establish the outcome — **ask if it was not stated**, on a
decision sheet: *done* (it shipped) or *abandoned* (it will not be built). Under `autonomous`,
every question of this run is answered by §Under `autonomous`; elsewhere a sub-agent does not ask:
return `STATE: blocked` with `NOTE: needs-human:` carrying the decision sheet.
```bash
.agents/quenching/bin/cq specs status --spec "<id>" --json
```
**First close what is already merged:** `.agents/quenching/bin/cq specs reconcile --write --json` archives every
spec of the front whose work is merged and whose `## Outcome` is written, this one included. A row
for this spec with `action: archived` → report it closed and run only step 8's worktree release; a
`needs-human` row is carried to the report. Then read task progress, the `## Outcome` state, and the
records — `branch`, `reviewed`, `merge`, `pr`, `outcome` — from that payload. Then, for the `## Discoveries` lines themselves, the one body this
step needs: `.agents/quenching/bin/cq specs section "<id>" Discoveries --json`. Then read git: the
current branch, whether the work branch exists, and whether it is already merged. Announce the
outcome and, per §Resuming, which stages this run will actually perform.

Open tasks under a `done` outcome are surfaced **now**, before anything is written, so the human
can choose between finishing them, forcing, or switching to `abandoned`.
**Done when:** one spec, one outcome, and the list of stages still to run are all settled.

### 2. Review the whole branch diff
```bash
git diff origin/<base>...HEAD   # <base> is the branch record's `base`; NO-REMOTE: git diff <base>...HEAD
```
Against `origin/<base>` because a PR merged through `gh` moves no local ref (step 7 fetches it); the
three-dot form keeps the diff to this spec even when the remote base advanced after the cut. This reads
the whole change for **coherence**, beyond execute's per-task self-review — two tasks that solved the same problem differently, an abstraction that wanted
extracting once the third caller appeared, a `## Impact` path nothing ever wrote, a standard the
diff contradicts.

**A rename widens the read from the diff to the corpus.** When the change renames a column, a
struct, a field or a domain term, grep the whole tree for the old names — `/docs/` included — and
treat every hit as a finding: fixed on this branch, or named as a deliberate remainder with its
reason. The three-dot diff cannot show what a sibling branch merged after this one was cut: a
document generated by another spec's conclude never appears in it, which is exactly how `MOB`
survived its own rename in a concept written by another spec (11/11/2026). A grep scoped to the
folders the diff touched is not proof of the corpus.

Present the findings. Fixes go in as ordinary commits on the branch, before the merge. Then stamp
the record — `.agents/quenching/bin/cq specs record "<id>" reviewed --set date=<today>`, never by editing the
frontmatter.

**The test is `branch.work != branch.base`, never the record's presence** — in-place work stamps
one too (`/docs/standards/workflows/plan-git-record.md` §Three frontmatter records carry the
underivable git facts). No record, `work` equal to `base`, or no git → say so and skip to step 4;
there is no branch diff to read.
**Done when:** the diff was read and `reviewed` is stamped, or the run recorded why there was
nothing to review.

### 3. Write the `/docs/` the work revealed
The `/docs/standards/` a task explicitly named is already in — execute wrote it as part of that
task. What lands **here** is what the work revealed and nobody declared: the `## Discoveries` lines
worth a doc, and whatever the branch review just surfaced.

Decide what crosses with the table in
[distill.md](.agents/quenching/references/specs-conclude/distill.md)
§What crosses, what stays; write each through the insert procedure in
[knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md)
§The frontmatter stamp §Updating `index.md` §Enriching the glossary §Self-check, stamping
`authority` honestly.

**The names it writes are the branch's, not the spec's.** A column, a table or a term that lands in
a document is spelled from the registry, the `table_schema` and the standard as they stand on this
branch — never copied from the spec's own prose, which was written before the branch renamed
anything. A document that quotes the spec instead of the code lands teaching the vocabulary of the
day the spec was defined.

Present them as ONE plan and take one confirmation (under `autonomous`,
§Under `autonomous` answers it).

These land on the branch, in their own commit — or, for `abandoned`, the base checkout (Doctrine).
A `## Discoveries` line that gets a doc is resolved in place. No OKF bundle → skip silently.
**Done when:** the emergent docs are written and committed, or the offer was declined, or there is
no bundle.

### 4. Write `## Outcome`
**First preserve observed build cost, with provenance.** This is independent of the derived
`.agents/quenching/bin/cq specs estimate` reading heuristic. Inspect `records.buildCost` from step 1: a valid measured
record already covers the build and survives a resumed conclude; do not replace it with missing
telemetry. New evidence may replace `unavailable`, with every field written together so an old
reason or measurement cannot survive the re-stamp. An abandoned, unbuilt spec also reports
unavailable rather than inventing an execute cost.

Use the existing meter **only** when the briefing identifies an explicit **build-only** Claude
Code subagents directory and the agent ids belonging to this spec's execute runs:

```bash
.agents/quenching/bin/cq components session cost "<explicit-build-only-subagents-directory>" --json
```

**Never infer** that scope from the latest session, the source spec's branch, transcript mtime,
prompt prose or a directory containing unrelated workers. Missing/ambiguous scope, native **Codex**
usage that was not measured, a meter refusal, missing requests or parse findings are unavailable;
exit 1 is partial evidence, never silently a complete measurement. Preserve the reason and scope
in the report. Do not invent usage from context capacity, file sizes or model memory.

On exit 0 with exact attribution and known models, retain the meter's `byModel` totals **per model**:
`model`, `requests`, `input`, `cacheRead`, `cacheWrite`, `output`. For each model, add
`maxRequestExposureTokens` as the maximum `groups[].maxWindow` for that model. That existing
meter field is reported request exposure, **never a context peak**: executor iterations can
aggregate in top-level usage. State that limitation in `source`; no iteration-peak coverage is
claimed. Keep the array compact as one JSON scalar, with no sum across tiers and no monetary cost.
Set `source` to runtime, explicit directory/agent ids, measurement command/exit and coverage;
`measurements` contains only observed values, and `reason` is empty. An unknown model/agent
attribution does not justify a measured whole-build claim.

Write the conclude-owned record **before `## Outcome`**, through the generic record writer:

```bash
.agents/quenching/bin/cq specs record "<id>" buildCost --set status=measured --set date=<today> \
  --set "source=<runtime; exact build scope; command/exit; request-exposure limitation>" \
  --set 'measurements=<compact per-model JSON array from the observed payload>' --set 'reason='
```

For unavailable evidence write no numeric stand-in; an empty scalar is absence, not zero:

```bash
.agents/quenching/bin/cq specs record "<id>" buildCost --set status=unavailable --set date=<today> \
  --set "source=<runtime and any explicitly known scope>" --set 'measurements=' \
  --set "reason=<why the build was not measured>"
```

Read the write's exit code; retry a transport failure with backoff, and read an exit-2 refusal
without repeating it blindly. A failed write stops this step before `## Outcome`. Report the record's
status, provenance, per-model values or unavailable reason; calibration is future evidence work,
not an inference this conclude makes.

`## Outcome` is the archive gate — the post-merge close cannot archive without it, so it is written
now, while the run still knows what shipped. Draft it, confirm it (under `autonomous`, §Under
`autonomous`), write it:
```bash
.agents/quenching/bin/cq specs section "<id>" Outcome --write     # body on stdin
```
For `done`: what shipped, what was left out, what the next reader needs — **reviewed, distilled,
ready for merge**; name neither a merge strategy nor a PR. **Do not archive it here** — the spec
stays in `plans/` until step 8 runs after the merge.

For `abandoned`: the reason it will not be built is the whole content, and nothing will merge, so
the archive happens now:
```bash
.agents/quenching/bin/cq specs promote "<id>" --to archive --outcome abandoned
```
A refusal (exit 2) lists exactly what is missing — surface it verbatim and let the human decide;
**never pass `--force` on your own initiative.**
**Done when:** `## Outcome` is written — and, for `abandoned`, the spec is in `archive/` with its
`outcome:` stamped — or the run stopped at a refusal the human declined to override.

### 5. Distil, and settle the release obligations
This is the last writing step, and everything it writes lands on the **work branch** for `done` — or, for
`abandoned`, the base checkout (Doctrine above). Two things happen here, in this order.

**First, the distillation pass** — the single bridge into `/docs/`, per
[distill.md](.agents/quenching/references/specs-conclude/distill.md)
§The procedure (one confirmation), **branching on the outcome**:

- **`done`** — the full pass over what is *left*: a decision still sitting in `## Design`, a generic
  understanding, a term the spec coined, a follow-up worth its own spec. Most of the harvest was
  already written in step 3 or during execution; **zero to a few is the normal result**.
- **`abandoned`** — **only** a narrow harvest of what was learned by not building it, as
  `authority: background`. Never mint a decision the spec *would* have made. Most abandonments
  distil nothing, and that is the correct result.

One plan, one OK (under `autonomous`, §Under `autonomous` gives it). Every write goes through
[knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md)
§The frontmatter stamp §Updating `index.md` §Enriching the glossary §Self-check. No bundle → skip
silently, landing per Doctrine: the work branch for `done`, the base checkout for `abandoned`.

**Then settle the release obligations the repo's standards attach to the merge itself.** With an
OKF bundle present, derive which standards the branch diff's own paths answer to — an aggregate in
the shell, never a walk of `/docs/standards/<subject>/` — and read exactly those, per
[align/evidence-doctrine.md](.agents/quenching/references/align/evidence-doctrine.md)
§1 §2. Apply what they require *of the merge* rather than of any one task — a version bumped across
artifacts a standard says must move together, a changelog entry, a manifest re-stamped. This is the
only correct moment for it: the whole branch is written, so what the release *is* is knowable.

Nothing is invented. A repo whose standards attach nothing to a merge gets nothing, silently, and
so does a repo with no bundle. What a standard *does* require is presented as ONE plan with the
standard quoted, taken on one confirmation (or §Under `autonomous`), and committed on the branch. A requirement the diff
already satisfies is reported as already done, never redone.

For `abandoned`, **no release obligation is settled** — a version nobody adopted is a claim the
history should not carry. The distillation above still runs.
**Done when:** the distillation offer was made and applied or declined, and the release obligations
were applied, reported as already satisfied, or reported as none.

### 6. Sweep the flow itself, and capture what it owes
The change is finished; the way it was made is evidence too. This step judges the run itself — the
skills, flow references, agents, standards, harness and deploy surface that carried it — and turns
each real defect into a spec of its own, captured with the problem and the evidence it needs, for
another session to develop. The project's automation evolves because its runs leave the defects
written down instead of carrying them to the next session's memory.

**A candidate is a defect of the project's own automation, evidenced by this run.** All three must
hold:

1. it is about **how the work was carried**, not what it delivered — the delivered change is the
   branch's and is already handled here;
2. this run produced the evidence, quotable: a step that had to be improvised, a reference that
   contradicted another, a gate that could not see a class of defect, a command whose failure the
   flow does not anticipate, a rule the run had to invent because no standard had it;
3. no open spec already covers it — check the front first
   (`.agents/quenching/bin/cq specs list --json`, matched on the title and on the defect it names)
   and add a `## Discoveries` line there instead of opening a duplicate.

**What does not qualify matters as much**: a defect of the delivered product, a nit in one file, a
preference of this session, or an improvement no fact from this run supports. Zero is the normal
result; a sweep that finds something every time is inventing.

**One spec per candidate, captured and not designed** — the design costs a session, and the session
that takes it should be the one that argues with the defect:

```bash
.agents/quenching/bin/cq specs new "<the defect, in one line>" --complexity medium <<'EOF'
## Problem

<what happened, in this run's own facts: the step, the command and its output, the file:line of the
reference that misled, the spec and the date — and what the improvement has to make impossible>

**Insumos**

- <path:line of the reference, the standard, the agent definition>
- <the command that failed, with its exit code and its output quoted>
- <the spec id and the step of this run where it happened>
EOF
```

The problem carries the **inputs** — the paths, the command and its output, the spec id — because
the session that develops it will not have this conversation. **Never develop the captured spec
here**: its design and its go belong to whoever takes it, unless the human asks for that improvement
in this same run, and then it is a new `create` and not a task of this spec.

**Where the improvement lives decides what the spec says.** A defect of a `quenching-*` skill, flow,
agent or runtime is fixed in the quenching checkout and reaches the repository by `cq deploy`, so
the `## Problem` names the source path in that checkout and the deploy step, never the generated
copy in this repository. A defect of this repository's own skill, standard, harness or script is
fixed here, and the spec says so.

Present the candidates as ONE plan and take one confirmation (under `autonomous`, §Under
`autonomous` answers it): each candidate with its evidence, and for each the choice between
capturing it now — the default — and declining it. A declined candidate is named in the report with
its reason, never silently dropped, and a resumed run re-reads the front instead of opening a second
spec for the same defect.

For `abandoned`, the sweep is about how the flow carried a spec nobody built; most abandonments
teach nothing, and that is the correct result.
**Done when:** every evidenced candidate is captured as its own spec or declined in the report, and
none was developed in this run.

### 7. Prove the gate green, then hand off
Nothing after this point writes anything — the last commit this run makes is step 5's, and step 6
writes only the spec store.

**First the gate: re-run whatever the repo's `## Validation` names — on the WORK BRANCH, before
merging.** Run it from the checkout that carries the work (the worktree or branch this run is
standing on), so what it grades is the change being proposed.

**A failing check stops the handoff.** Report which check failed and what it printed, leave the
branch unmerged, and let the human fix it *on the branch* — then this command is re-run. Never hand
off past a red check, and never repair one with a commit that isn't the fix itself.

**A red that is not this spec's has its own route**, per `/docs/standards/workflows/task-execution.md`
§Pre-existing errors: reproduce it in the base worktree (`.quenching/red`, removed before returning).
No reproduction → it is the branch's own and the paragraph above applies. Reproduced and contained (the
four criteria together: cause diagnosed and reproduced on the base, few files without a contract change,
an existing verification proves it, no human decision) → the fix itself is the one commit allowed
here, declared as a `[fix-preexistente]` task (`files:` and `verify:`) so the verifier reads it as scope,
and the gate runs again. Otherwise capture it now with `.agents/quenching/bin/cq specs new` (`## Problem` plus
cause, reproduction, files), record `promoted: <id>` in `## Discoveries`, and stop `STATE: failed` (under
`autonomous`, `needs-human:` naming that spec): the red stays a FAIL, the PR names it and the merge
waits for the captured spec. Never skip, `xfail`, suppress a test or edit a `verify:`. The sweep of
step 6 keeps its own criteria; this route is for defects of the product, not of the automation.

**The gate grades the branch, so the branch must already carry the base.** If the base moved since
the branch was cut, a green gate says nothing about the merged tree:

```bash
if git remote get-url origin >/dev/null 2>&1; then
  git fetch origin <base> && git rev-list --count plan/<id>-<handle>..origin/<base>
else
  git rev-list --count plan/<id>-<handle>..<base>     # NO-REMOTE: the local base is the only one
fi                                                     # commits on the base the branch does not have
```

Count against `origin/<base>` wherever `origin` exists: the PR is opened against the remote, and a
PR merged through `gh` moves no local ref, so the local `<base>` can read zero over a branch that
lacks a merged dependency. A fetch error is reported and stops the handoff; it is never read as
zero.

Non-zero → **say so and stop before the gate**, naming the count. Bringing the branch up to date is
a write, and it is the human's call which way — merging the base in, or `/quenching-git sync`'s own
rebase, which carries the recorded task subjects through the rewrite. Never do it unasked, and
never run the gate over a branch you know is behind.

**Under `autonomous` the answer is given, and it is the merge.** A sibling spec merged mid-wave is
the ordinary way to be behind, and a `needs-human` there is never relaunched. Run
`git merge --no-edit origin/<base>` — a merge, never a rebase, so the task shas stay ancestors and
the spec-verifier's check 7 accepts them — and then the gate over the merged tree. A conflict only in
the deployed surface (`.agents/quenching/**`, `.claude/skills/**`, `.agents/.quenching-deploy.json`)
is mechanical: take the base's side (`git checkout-index --stage=3 -f -- <path>`, `git add <path>`), then
**regenerate before concluding the merge** — `cq deploy --target . --write` — `git add` what it
rewrote, and conclude the merge. A manifest taken from the base alone loses the branch's entries.
Then `cq deploy --target . --check` exits 0. A conflict in any other path →
`git merge --abort` and `needs-human` naming the paths.

**An inconclusive result is not a green one.** A check that cannot tell "this failed" from "this
could not be measured" has returned no verdict — say which it was, and ask (§Under `autonomous`:
`needs-human`), rather than merging on it. Where the repo keeps `/docs/standards/quality/surface-verification.md`, its §The five
preconditions a check must satisfy is where that distinction is defined for the command surface.

**Run the scope the diff justifies.** A check with a `--only`-style selector gets the subset this
branch can actually break, because a harness that spawns fresh agent sessions bills for every one.
When the repo's `## Validation` names
the scope, follow it; when it does not, run what it says and report the cost as a finding worth a
selector.

The gate passed — the branch is reviewed, distilled, its `## Outcome` written, and proven. For a `done` outcome,
read the complexity from the status payload already in hand. **Under `autonomous` the run opens the
PR itself, whatever the complexity**, because publication cannot depend on an orchestrator that
may never exist: the same verb the `low` path chains, with the word `autonomous` as the human's
pre-answer to its confirmation.

```text
Skill("quenching-git", "pr-create <id> autonomous")
```

`/quenching-git pr-create <id> autonomous` owns its own push, confirmation and `pr` record;
report the url it stamps on `PR:`, and never a second handoff — `/quenching-git merge` is not
invoked from here under any outcome. **Outside `autonomous`, `low` chains the provider handoff
now:**

```text
Skill("quenching-git", "pr-create <id>")
```

Invoke it with the spec id; `/quenching-git pr-create` owns its own confirmation, push and PR
record. Do not offer a second handoff or invoke `/quenching-git merge` on the low path.
**For `medium`, `high` and `xhigh`, do not invoke it: name the handoff and stop:**

Use the provider configuration already read in step 1 to name the next command: recommend
`/quenching-git pr-create` when the configured provider supports the PR route, otherwise
`/quenching-git merge`. The human picks, and neither is invoked from here. Name the branch and the
base so the recommendation is copy-pasteable. Add one line: once the merge lands, `/quenching-specs conclude <id>` archives the spec and releases its worktree (step 8). For an `abandoned` outcome, skip this handoff and
follow the branch-disposal path below.

For `abandoned`, there is nothing to hand off toward — do not remove any worktree; frame and make
the branch-delete offer per
[specs-conclude/abandoned.md](.agents/quenching/references/specs-conclude/abandoned.md)
§The branch-delete offer, informed rather than defensive, default **keep** (taken unasked under
`autonomous`), and record the choice and fate in the report.

**Done when:** the gate ran green on the branch and the handoff was named, or the run recorded
why nothing could be handed off — a red gate, or (abandoned) the branch's own fate decided instead.

### 8. After the merge: archive and release the worktree
This step runs only once the work is merged — a conclude resumed after the merge (the "already
merged" row of §Resuming), or right after a `low` or `autonomous` hand-off whose merge already
landed. Right after `pr-create` the PR is open, nothing here applies, and the run ends at step 9.

```bash
.agents/quenching/bin/cq specs reconcile --write --json
```

`reconcile` is the whole archive rule and this step adds none of its own: it fetches, finds every
spec whose work is merged, and archives as `done` each one carrying `## Outcome` through the same
gate `promote` applies. A row with `action: needs-human` (`## Outcome` missing, a task still open)
is reported with its `why` and never forced.

**Then bring the local base up to the remote** — the merge moved only `origin/<base>`:
`.agents/quenching/bin/cq git refresh --json`. It is fast-forward only: it never discards a commit or an uncommitted
change and never switches a branch, and a `diverged` or `merge-refused` result is reported and left as it is, never forced.

**Then give the worktree back.** A spec built in a **slot** (`../<repo>-slot-<n>`) needs nothing
more: `reconcile` already released it in the row it archived (`slot.released`) — detached at the
remote base, unlocked, its `.venv` kept warm for the next spec. A `slot.released: false` row keeps
the slot held and carries its reason (a dirty tree, ignored files no `shared.regenerablePaths`
entry covers): report it and leave the slot standing.

A spec built in a worktree that is **not** a slot (cut before slots existed) is retired instead,
with no question and no extra flag. The recorded fact is the `branch` record (`work`, `base`) and
the worktree path from `git worktree list`; skip silently when the work was done in the base
checkout or in a plain branch. Run it from the base's checkout, never from the worktree it removes:

```bash
cd <the base's checkout> && .agents/quenching/bin/cq git worktree retire --path <the spec's worktree> \
  --branch <branch.work> --base <branch.base> --json
```

`retire` fetches the remote base, requires the branch tip to be an ancestor of it, refuses a dirty
tree, and runs `git worktree remove` and `git branch -d` without `--force`. **Never pass `--force`
or `--discard-ignored`, and never `retire` a slot** — a slot is released, never removed, except by
`/quenching-git cleanup` above the floor. A refusal is information, not an obstacle — report the
`code` or `message` it printed and leave the worktree standing; it is never retried with a stronger
flag.
**Done when:** the spec is in `archive/` with `outcome: done`, or its `needs-human` row is reported;
and its slot was released, its legacy worktree retired, or the refusal reported.

### 9. Report

**When this run's input carried `autonomous`, skip the read below and the mold with it** — a conductor presents the report, not this run; return the content in the fixed shape the caller specified. Otherwise:

```bash
.agents/quenching/bin/cq components read .agents/quenching/references/specs-develop/report-mold.md \
  --sections "§The report mold" --rules-only
```

Emit §The report mold. The single-spec header line carries the locator, its phase (`plans` until the
post-merge close, `archive` after it) and the outcome; three
body blocks:

1. **At close** — fixed. Task progress, `reviewed` / `branch` as they now stand, and — for an
   abandonment — that nothing was adopted and what became of the branch.
2. **The review** — fixed. What it found and what was done about it, the docs written in step 3 and
   step 5, **what the sweep captured in step 6** — the ids it opened and the candidate it declined,
   with the reason — and what the pre-merge gate returned. §Quoting a tool's own output governs the gate's
   result, which means **a check that came back inconclusive is named as such, never counted as
   passed**. Close the block with one line on **target acceptance** — whether the change was
   exercised where it lands, apart from the local gate: `Target acceptance: verified (<what ran,
   where>)`, `not performed (<why>)` or `n/a (<why nothing lands outside the gate>)`. A green gate
   proves this checkout, never a deployed target, a real session or the provider, so it never
   stands in for `verified`.
3. **Unresolved `## Discoveries`** — optional, each line named. They are
   `/quenching-specs develop`'s discoveries stage to close, and they are easiest to lose at exactly
   this moment.

Print §The session-size line before the block — `.agents/quenching/bin/cq components session context --json`, read once; exit 1 turns the `→` line into a recommendation to run that handoff **in a new session**, exit 0 and 2 leave it an offer.

Close on §The next-step block, its recommended line naming step 7's handoff — `/quenching-git pr-create`
or `/quenching-git merge`, whichever was recommended, or `/quenching-specs develop <id>` when
block 3 has rows and that needs settling first.
**Done when:** path, outcome, records, both `/docs/` passes, what the sweep captured, the target
acceptance line and the next-step block are all reported.

## Invariants to never violate

- Never infer the outcome. `done` and `abandoned` are the human's word, always — under `autonomous`,
  the explicit `--outcome` the caller passed, and nothing else (§Under `autonomous`).
- Never treat staleness as evidence of abandonment.
- Never pass `--force` unprompted — a refusal is information, not an obstacle.
- Never hand off an abandoned spec's branch toward a merge — offer to keep or delete it instead.
- **Never `git branch -D`, at all**, when framing the abandoned branch-delete offer — git's own
  refusal over a not-fully-merged branch is the safety, and forcing past it destroys the only copy
  of work nobody adopted.
- **Never hand off past a red `## Validation` gate**, and never count an inconclusive check as a
  green one.
- **Never run the gate over a branch that is behind its base.** Report the count and stop; bringing
  it up to date is a write, and which way is the human's call. Under `autonomous` the call is made:
  the base is merged in (step 7), never rebased.
- **Never archive a `done` spec before its merge.** Step 4 writes `## Outcome`; only step 8, after
  the merge, archives — and `promote`'s own refusal is the second barrier, not the first.
- **This command's own commits end at step 5.** Nothing it does writes to the branch after the gate
  in step 7, except the base merge `autonomous` runs before that gate — the merge, the PR, and whatever they each commit belong entirely to
  `/quenching-git merge` and `/quenching-git pr-create`, run separately, on the human's own word.
- **A pre-existing red is never waved off nor muted**: a contained fix is a declared `[fix-preexistente]`
  commit, anything else is a captured spec, and the gate stays red until one of them lands.
- **Never develop a swept candidate in the run that found it.** Step 6 captures; the design and the
  go belong to the session that takes the spec, and only the human asking for that improvement in
  this same run makes it a new `create`. Never open a sweep spec without the evidence that named
  it, and never open a second one for a defect an open spec already covers.
- **Never let the sweep judge the delivered change.** The product's defects are the branch's, and
  the review already owns them; a candidate that is really the change's own defect belongs to
  `## Discoveries`, not to a new spec.
- Never overwrite the `writeOnce` `outcome` record to make reality fit — report the disagreement
  instead, and never edit the frontmatter to get past `.agents/quenching/bin/cq specs record`'s refusal.
- Never re-run a stage whose signal is already set without saying so and being asked to.
- Never re-write a rule a task already wrote into `/docs/standards/` during execution — concluding
  syncs nothing.
- Never settle a release obligation for an **abandoned** spec, and never invent one no standard
  states.
- Never bulk-copy a spec into `/docs/`; only what outlives it crosses.
- Never distil an abandoned spec's decisions as adopted knowledge; `background` is the ceiling.
- Never edit or delete anything already in `archive/`. The distillation's `## Outcome` append
  lands before the archive, on the spec this run is closing; never touch a spec other than that
  one, nor an archived spec from an earlier run.
- Never rewrite history: no amend of a task commit, no force-push, no `--no-verify` and no
  `--no-gpg-sign` on the commits this command makes.
