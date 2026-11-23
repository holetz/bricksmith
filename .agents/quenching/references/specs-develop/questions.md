# Composing and refining — what `develop` asks, and how

The owner of **what** `/quenching-specs develop` asks and **when it stops**. The command body owns
the loop (resolve → derive the pass → ask → one edit → verify); this file owns the two operations,
the stages around them and the mechanics they share, and the body never restates it.

**Two operations, not a ladder of banks.** **Compose** is monotonic: it takes the spec from wherever
it is to `ready`, filling what is absent. **Refine** is not: it runs over the composed spec and may
rewrite anything it finds, the proposal included. Which of them runs is **derived** — the stage says
where composing starts, a concrete signal says whether refining runs without being asked for.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## The pass — what runs, and in what order

A pass runs up to four stages, always in this order:

| # | Stage | Runs when | What it is really asking |
| --- | --- | --- | --- |
| 0 | [discoveries](#discoveries) | `## Discoveries` holds a line nobody resolved | what did execution already find that this spec has not absorbed? |
| 1 | [compose](#compose) | the spec is not yet `ready` | what IS this, and what has nobody answered yet? |
| 2 | [refine](#refine) | a concrete signal fired, or a human asked for it | is this the right thing to build at all? |
| 3 | [approval](#approval--the-close-and-its-recommendation) | always | does this spec get a go, and what should happen to it next? |

**The stage says where composing starts, never which operation runs.** `.agents/quenching/bin/cq specs status --spec <id>
--json` reports it, and the `sections` and `ready` fields of that same payload say exactly which
headings compose still owes:

| Derived stage | What compose has left to do |
| --- | --- |
| `captured` — only `## Problem` is filled | give the problem a shape, then close the ten-section ready set |
| `proposed` · `designed` · `refined` | close whatever of the ready set is still missing or malformed |
| `ready` · `approved` · `executing` | nothing — so the pass is a refine |

**A spec that is already `ready` and gets invoked again is a refine request.** Nothing remains to
compose, and re-invoking the command is a human asking for the one operation left. That is also the
route by which a spec on the simple track is refined (§Who answers — the calculated pass).

**The order is fixed, and it is the whole point.** Arguing with a spec before its tasks, its impact
and its validation exist is arguing with half a claim. *Is this worth building?* is answerable only
against a whole spec, which is why refine runs after compose and never before it.

<!-- rationale -->
The ladder this replaced picked ONE bank per pass off the derived stage and re-derived between them.
Two costs, one cause. Every crossing paid a reference read, a section read, an edit and a `validate`
for a spec nobody had finished. And the crossing was not even honest: the dependency sweep the shape
bank mandates lands `### Mapa de dependências` under `## Design`, and `designed` beats `proposed` by
last-match — so every shape edit derived `designed`, and the adversarial bank, the only one that
ever disagreed with a spec, was skipped by construction. It reached only specs born `proposed`.

What the ladder got right is kept: the pass is derived, never chosen. v2 asked the human to pick
between `interview`, `critic`, `premortem` and `alternatives` before it had read anything — a
question the spec's own state answers better than the human can.

## The four shared mechanics

Every stage of the pass obeys all four.

### 1. Grouped by dependency, asked on a decision sheet, and every question carries a recommendation

<!-- rules -->
**A question travels with the ones whose answers cannot change it, and alone otherwise.**

- **Independent → one sheet.** Where answering A would not change what B asks, A and B go
  together on ONE **decision sheet**. There is **no cap**: every independent decision open at that
  moment goes on the same sheet.
- **Dependent → the next sheet.** Where a question's *content* depends on an earlier answer, it
  stays off the sheet, waits for the reply, and is chosen in light of what was just said rather
  than from a list written in advance.

**The decision sheet is the default form of every question put to the human.** It is prose in the
turn, not a tool payload, and its shape is fixed:

1. **An opening line with the reply format** — `D1 A · D2 A · D3 B …` — saying free corrections
   may follow it.
2. **One `### D<n> — <title>` per decision**: the context (how it is today, why it matters), a
   table where one helps, and the options `A (recommended)` / `B` / `C`, each stating its
   consequence. **The recommendation is option A**, listed first, with its reasoning in A's own
   text.
3. **The dependencies declared** — a decision appears early only when it depends on no earlier
   one; a dependent decision is named as waiting for the next sheet.
4. **A closing "what I fix without asking, unless you veto" block** — the numbered corrections the
   evidence already settles, each with its evidence.

**The sheet ends the turn.** It is an explicit stop: nothing after it runs until the human replies.
**A reply that does not resolve every `Dn` without ambiguity is asked again** — a new sheet
carrying only the unresolved `Dn` — and is never filled in by default. A free correction in the
reply is an answer like any other and names its section (§4).

**The labels follow the declared conversation language** — the one tag the root harness line
declares. In a pt-BR conversation they read `A (recomendada)` and "O que corrijo sem pedir, salvo se
você vetar"; the structure does not change.

**`AskUserQuestion` is the menu, used only when the human asks for one.** Its grant stays in
`allowed-tools` as the proof that the gate exists; the sheet is that same gate in another form.
When the human asks for the menu, the recommended option is listed first and marked
"(Recommended)", and the grouping rule above still decides what travels together.

**A sub-agent without the `AskUserQuestion` grant never asks.** It returns `STATE: blocked` with
`NOTE: needs-human:` carrying the sheet, and the conductor presents it. **A sub-agent that holds the
AskUserQuestion grant is the exception:** it carries the sheet inside one `AskUserQuestion` call
(each `Dn` a question, the recommended option first and marked "(Recommended)", the reasoning in its
description), because that call is its only channel to the human, and it does not end the turn on
prose. Declared autonomy and the simple track ask nothing, sheet or menu.

Which shape a stage takes is a property of the stage, stated with it:

| Stage | Shape | Why |
| --- | --- | --- |
| [discoveries](#discoveries) | grouped | every line is independent, and all of them take the same fixed set of three resolutions |
| [compose](#compose) | sequential to shape, grouped to close | what is actually wrong decides which shapes are worth tabling at all; once a shape is on the page, the remaining headings do not move one another |
| [refine](#refine) | sequential | a criticism once answered rewrites the next one |

Every question carries **an inline recommendation and the reasoning behind it**, so the human can
answer in one letter:

> **Weak** — "What should the failure budget be?"
> **Strong** — "### D1 — Failure budget. **A (recommended):** five attempts before a task is
> declared blocked: enough to absorb a flaky test or a missing import, short enough that a
> genuinely wrong approach stops burning tokens. **B:** three, if this suite is slow enough that
> five costs too much."

<!-- rationale -->
The rule this replaced was *never batch* — absolute, and stronger than its own reason. That reason
was always about **dependence**: a batch of five questions gets one shallow answer covering the
easiest of them, and a single question lets the next be sharper for having heard it. Neither clause
says anything about two questions that cannot reach each other, and paying a full round trip for
each of those buys nothing — every turn re-sends the whole conversation, so a question asked alone
costs the preamble again.

Measured on this repository's own transcripts, question turns are **not** where this command's
tokens go: real `/quenching-specs develop` runs made 2–5 `AskUserQuestion` calls, against whole-file
reference reads worth ~900k token-turns in a single run. Grouping is here because the old rule
overreached, not because it closes a leak.

The recommendation is not a formality. A pass that hands the human a bare question list has moved
the work rather than done it — the command is supposed to arrive with an opinion.

The sheet replaced the menu as the default because the menu limited the dialogue: four questions
per call, a few options each and no room for the context a decision needs. In session 76eec84e the
human rejected a four-question `AskUserQuestion` and asked for the decisions listed with their
recommendations and detail, to answer them all at once in text. The sheet is that answer, made
the default. The grant stays because the lints read it as proof of the gate, not as a promise of
the tool's form.

### 2. Accumulate; apply in ONE edit per pass

<!-- rules -->
**Nothing is written while a stage is running.** Keep a running list of
`(question, answer, target section)` and write it all at once, as a single narrated edit. The
accumulation and the one-edit shape are the mechanic; the confirmation that used to gate them is
not. Invoking the command is the authorization, and the human's window on the edit is the spec's own
URL in the backend.

**The edit lands per pass, not per stage** — compose and refine write together, in one call, and the
tool is built for exactly that: `.agents/quenching/bin/cq specs section <id> "<H1>,<H2>,…" --write` splices N headings and
**lands them as ONE write**, so a mismatched heading set refuses (exit 2) without writing any of
them. A pass is therefore all-or-nothing: an abandoned one leaves the spec exactly as it was.

<!-- rationale -->
- Later answers routinely revise earlier ones, and refine exists to revise what compose just wrote.
  Writing compose's answers before refine has run means editing the same paragraph twice, and the
  intermediate state is never reviewed by anyone.
- One diff is readable. Six scattered edits are not — and that one diff is what the human reads in
  the backend afterwards.
- The gate left when it was priced: this command edits no code and takes no irreversible cycle
  action, so the pair of turns each stage spent waiting protected nothing the backend's own edit
  history does not now carry. What the per-bank boundary used to narrow, the one-edit-per-pass rule
  restores whole.

### 3. A declared stop condition

Each stage below states when it is **done**. Announce the pass's stages and their stop conditions
when the pass starts, so the human knows the shape of what they agreed to, and honour them — a stage
that keeps finding one more question is the free-ranging exploration this front deliberately gave
up.

Three universal stops apply on top of each stage's own:

- **The human calls it.** "That's enough" ends the stage and goes straight to the consolidated edit
  with whatever was accumulated.
- **Diminishing returns.** Two consecutive questions whose answers change nothing about the spec —
  stop and say why.
- **The question is not this spec's.** A question that would read identically against any spec is
  noise. Do not ask it; do not pad the count with it.

### 4. Every answer names the section it lands in

An answer with no destination is conversation, and conversation is what gets summarized away. As
each answer arrives, record which canonical section it belongs to — that mapping IS the
consolidated edit, and an answer that maps to nothing is either out of scope (route it, per the
body's guardrails) or was not worth asking.

## Who answers, and who is argued with — the calculated pass

<!-- rules -->

The stages below are the engine of **content**, and each is the same wherever it runs: the same
axes, the same lenses, the same seven gate symptoms, the same stop conditions. **Nothing shortens a
stage.** What the session calculates is two things — **who answers** a stage that runs, and
**whether refine runs**.

**The calculation.** Before it asks anything, the session reads the state
(`.agents/quenching/bin/cq specs status --spec <id> --json`), enumerates the decisions the execution will need, and
classifies each as **resolved by evidence** (the spec, the codebase, the `/docs/standards/` its
`## Impact` declares and `/docs/glossary.md` answer it), **dependent on the human** (appetite, a
policy, a choice of direction no evidence settles) or **very costly to undo** (irreversible, or of
wide blast radius). `priority.complexity` records the depth the calculation reached
([spec-driven.md](.agents/quenching/references/specs-develop/spec-driven.md) §The scale);
it informs the calculation and never replaces it. Absent reads as `high`.

| Calculation | Track | Who answers | Stamp |
| --- | --- | --- | --- |
| no decision depends on the human | **simple** | the session composes everything from evidence and **announces** what it will do before it does | `approved --set by=session` |
| at least one decision depends on the human | **complex** | the session asks **in batches**, one round at a time, then decides | `approved --set by=human` |
| `autonomous` declared | **declared autonomy** | the session, from evidence, asking nobody | `approved --set by=orchestrator-forced` |

**Batches.** A round carries the questions whose answers do not change one another, each with the
session's recommendation (§1). Questions that depend on a previous answer wait for the next round.
Every stage drafts an answer first and puts it in front of the human as the question — *"here is
what I would write for `## Out of Scope`; is the boundary right?"* — so a question about a
**drafted** section cannot change what the next question asks, only what the next edit writes.

**Declared autonomy asks nothing and never refines.** What evidence does not answer becomes an
`## Open Decisions` line carrying **how it will be decided** — never invented, never asked. Refining
changes the proposal, and that is always the human's.

**Every answer that rests on an assumption is recorded.** One line per assumption under
`### Assumptions` in `## Design`, naming the section that depends on it, so execution and review
read it. An answer resting on something unproved is also written with the assumption named in the
text it lands in.

**What the session may never do.** It may not invent an explicit none. It may not resolve a
`## Discoveries` line `promoted:`, which mints a spec (§Discoveries). It may not present a screen
from inside a sub-agent — the sub-agent returns the screen's content and the conductor presents it.
It may not stamp `session` while a decision still depends on the human. And it may not treat an
unanswerable item as answered: `## Open Decisions` with how it will be decided is the result, and a
pass that has no evidence and writes a confident sentence anyway has failed in the way the simple
track is most likely to fail.

**Refine runs by concrete need.** It runs when a signal fires (§Refine's scope, critique and
irreversibility) or when the human asks for it, and never on declared autonomy. The lens follows
the risk: **alternatives** if a decision of shape is open, **critique** if there is a contradiction
or a gap in the evidence, **premortem** if the change is irreversible or of wide blast radius. The
premortem is an option, never the default. The report of the pass names the lens applied, because
`refined.mode` records only `compose` or `refine`.

<!-- rationale -->

The previous design chose by a recorded level, and the measurement over the specs dated from
2026-10-01 shows what that bought: `refined` is `compose` in 415 specs and `refine` in 7, so refine
ran in about 1% of them, and the premortem the text provided for `high` and `xhigh` was not the
practice. A calculated pass puts the speed of small work in one place — nothing to ask, so nothing
is asked — and keeps judgment where a concrete risk is.

## Compose

**Runs when** the spec is not yet `ready`. **Goal:** take it from wherever it is to a spec worth
building — a shape where there was only a problem, and a closed ten-section ready set where there
were gaps. It is **monotonic**: it fills what is absent and sharpens what is thin; it does not
overturn what a human already settled. Overturning is §Refine's.

**Shape.** Sequential to shape, grouped to close (§1). What is actually wrong decides which shapes
are worth tabling at all; once a shape is on the page, the remaining headings do not move one
another and travel together on one decision sheet.

**Drive the gap off the tool, never off a reading of the file.** The `sections` and `ready`
(`{ok, missing, malformed}`) fields of the step's own `.agents/quenching/bin/cq specs status --spec <id> --json` payload
already name every heading that is absent or malformed, and the section read returns
`(## X is absent)` for the rest — so the map of what compose owes costs no call of its own.

### Giving the problem a shape

Only where `## Proposal` is absent or empty. The dependency sweep runs first (§Gathering the
evidence), and its map lands in `### Mapa de dependências` under `## Design` — written by the
orchestrator, never the sub-agent. Then the problem statement itself, read against the codebase.
Roughly in this order:

- **What is actually wrong.** Is the `## Problem` describing a symptom or a cause? What does the
  code do today — read it, do not theorize.
- **Why now.** What changed, or what does waiting cost? A problem with no answer here is a plan to
  leave in `plans/`, and saying so is a result.
- **The shape.** Two to four genuinely different shapes the answer could take, in a table:
  approach, what it costs, what it buys, what it forecloses. Recommend one.
- **The boundary.** What is the nearest thing someone will mistake for in-scope? That answer is
  `## Out of Scope`, and it is cheapest to write now.
- **What would make this not worth doing.** The honest version of a go/no-go.

A good ASCII diagram — current flow, proposed flow, the state machine — is worth several of these
questions and often replaces one.

**Lands in** `## Proposal` (the shape), `## Out of Scope` (the boundary), `## Design` /
`## Alternatives Considered` when the shapes table produced a real comparison.

**Evidence answers** what is actually wrong (read the code), the shapes table with its
recommendation, and the boundary. **Only a human answers** *why now* — what waiting costs is
appetite, not a fact about the tree — and *what would make this not worth doing*. Their presence puts
the spec on the complex track; on declared autonomy both go to `## Open Decisions` unless
`## Problem` already states them.

### Closing the ready set

The rest of the ten sections, honestly. This is the half that most easily degrades into filling
headings to make a check pass, and the explicit-none rule is the whole defence.

The questions come from the gap itself, in the spec's own terms:

- a section that says `- none` with no reason attached — is that a decision or an omission?
- `## Out of Scope` absent → what was ruled out, and what is the nearest in-scope lookalike?
- `## Validation` absent or vague → what exact command proves this worked, and what must it print?
- `## Impact` declaring a standard no task names, or a task writing a doc `## Impact` never
  declared;
- a `## Tasks` item whose completion nobody could judge ("improve error handling");
- a task ordering that has step 4 depending on step 7;
- a term the spec uses in a sense the repo's glossary does not.

**And the three questions only this moment can answer** — every one of them is a decision
`/quenching-specs execute` is forbidden to make for itself, so a spec that leaves them unasked is
built with the cheap options closed:

- **which task groups touch provably disjoint `files:`** → mark them `[P]` now. Execution **never
  infers** the marker ([spec-driven.md](spec-driven.md) §`## Tasks` and the `[!]` blocked marker),
  so an unmarked group runs serially forever, however disjoint it was.
- **which delegable task is missing `files:`** → declaring it is what *permits* the task to be
  handed to an executor sub-agent at all; omitting it forecloses both delegation and `[P]`.
- **which task has an obvious existing file to imitate** → `pattern:`, the cheapest context an
  executor can be given: one path beats three paragraphs of description.

Measured across five `ready` specs on this repository — 48 tasks — `files:` was declared on 81% and
`verify:` on 92%, but `pattern:` on 8% and `[P]` on **4%**, with three of the five specs carrying no
parallel-eligible task at all. The mechanism was never the missing piece; the question was.

Compose also settles the two declarations nothing else owns: the **`verification` policy** (one of
the three values in [spec-driven.md](spec-driven.md) §Frontmatter, asked once and written to
frontmatter, so `execute` never has to guess mid-build) and the parsed `### Standards this spec will
write into docs/standards/` sub-heading under `## Impact`.

**Evidence answers** all seven symptoms and all three execution decisions — `[P]`, `files:`
and `pattern:` are provable against the tree, and `.agents/quenching/bin/cq specs parallel` proves the first
mechanically. **Only a human answers** the `verification` policy; on the simple track and on declared autonomy the pass **declares
nothing** and lets the default stand, because stamping the default records a decision nobody made.

**Stop when** the ready set is closed — every gate section answered with content or a reasoned
`- none`, every task with a judgeable completion, no `## Impact` path uncovered — and the human
would recognise the spec as describing their idea. A `[P]` compose set is **proved** when the edit
lands, by `.agents/quenching/bin/cq specs parallel`, never argued about here: the tool checks disjunction mechanically and
names the group it refuses.

<!-- rationale -->
These were two banks, `shape` and `gate`, separated by an edit and a re-derivation. They were never
two questions: closing the ready set is finishing what shaping started, over sections the same pass
is writing. The split bought one thing — a stage transition on disk — and cost a full crossing to
buy it, while the `## Design` that transition depended on was being written by the dependency sweep
as a side effect. One operation, one edit, one `validate`.

## Refine

**Runs when** a concrete signal fires — scope, critique or irreversibility — or when a human asks
for it, never on declared autonomy (§Who answers). **Goal:**
make the spec survive the argument, or change it.

**This is the one operation with a licence to overturn.** Compose fills; refine may rewrite anything
it finds — cut the scope, convert a risk, re-table the alternatives, and **change the proposal
itself**. That licence is safe because of where refine runs: a changed proposal is a question put to
a person, never a decision a pass took alone. Declared autonomy never refines, which is why the
licence never reaches an unattended pass.

**Shape.** Sequential (§1) — a criticism once answered rewrites the next one.

**It runs over a composed spec.** The tasks, the impact, the validation and the risks exist by the
time it starts, which is what makes *is this the right thing to build at all?* answerable rather
than rhetorical.

**The dependency sweep, where compose did not run it.** A spec that reached refine without ever
being swept — invoked again when it was already `ready` — gets the sweep first, on the same terms
(§Gathering the evidence). Its result lands in the same `### Mapa de dependências` under
`## Design`, written by the orchestrator. A spec is swept at most once.

It runs three lenses. Use the ones the spec's own signals argue for; a spec that deserves two gets
two, in this order. **The premortem runs only when the change is irreversible or of wide blast radius** — it is an
option chosen by that risk, never the default.

### The signals — which lenses fire, and what the close recommends

The same three conditions do double duty: they select the lenses here, and they are what
§Approval's closing screen judges the composition against when it recommends the next move.

| Signal | Lens it fires |
| --- | --- |
| `## Alternatives Considered` is absent, empty, or records one option | alternatives |
| the spec is large, reaches into product code, or `## Impact` declares an uncovered standard | critique |
| the spec is risky or irreversible: a migration, a rename with a blast radius, a release | premortem |

**No signal fired** is a result, not a gap: the close recommends approving, and says so. Reading the
signals costs nothing beyond the sections the pass already has.

### Lens: alternatives

Generate **two to four genuinely different** approaches — different in shape, not in parameter.
"Same design with a bigger cache" is not an alternative. Include, where they apply, the three that
are almost always available and almost never written down: **do nothing** (what happens if this is
not built?), **the smallest thing that could work**, and **buy or borrow instead of build**.

Table them — approach, cost, benefit, what it forecloses — state a recommendation with the
reasoning, and ask the one question: does the spec keep its approach, or switch?

Each alternative is stated as its strongest advocate would state it; one that cannot be written
down convincingly was not a real alternative and does not belong in the table.

Lands in `## Alternatives Considered` — **including the rejected ones and why they lost**, which is
the entire point: the next person to have the same idea reads why it was already turned down. A
switch also rewrites `## Proposal`, and every section the old proposal reached.

### Lens: critique

Assume the spec is wrong and find where. Be specific. Aim at:

- **The premise.** Does `## Problem` describe a problem anyone actually has? What is the evidence?
- **Scope inflation.** Which part of `## Proposal` does not follow from `## Problem`? What survives
  if it is cut?
- **Cheaper paths.** What buys 80% of the benefit for 20% of the change?
- **Contradiction with a binding contract.** Does any task violate a `docs/standards/` doc? Name
  the doc and the line.
- **Permanent cost.** What does this add forever — surface, config, a rule to remember? Is the
  benefit recurring or one-off?
- **Reversibility.** If this turns out wrong in a month, what does undoing it cost?
- **The load-bearing assumption.** Which single unstated assumption, if false, invalidates the spec?

**Every criticism carries a proposed remedy**: cut it, defer it, shrink it, or accept it with a
stated reason.

Lands in `## Out of Scope` (a scope cut taken), `## Open Decisions` (one that needs evidence), or
the spec's own sections where the remedy rewrites them.

### Lens: premortem — for the irreversible

State the frame out loud: *"It is three months from now. This spec was built and it went badly.
What happened?"* — then generate the failure stories from the spec's actual content, not from a
generic risk list.

Cover at least: what broke for a user of this repo; what half-landed and left an inconsistent
state; what the spec assumed about existing code that was not true; what the tasks did not cover;
who could not use the result. For each story: how likely, how bad, and **how it is detected** — a
failure nobody notices is worse than a loud one.

Then convert. Every story becomes exactly one of: a **mitigation task** in `## Tasks`; a **risk
with a named mitigation** in `## Risks`; an **accepted risk**, recorded with the reason it is
acceptable; or a **scope cut** into `## Out of Scope`. A story that converts into nothing was not a
real risk — drop it and say so.

**Evidence answers** every alternative in the table, all seven critique targets and every
premortem story — each is readable against the tree. **Only a human answers** the accept-versus-cut
verdict where the remedy trades scope for risk, and **every proposal change**, which is the one
thing this operation may propose and never take alone.

**A refine that rewrites what compose wrote in the same pass rewrites the draft, not the file.**
Both land in the pass's single edit, so the spec never holds the superseded version.

**Stop when** every criticism has been answered, every failure story converted, the alternatives
table exists and the human has chosen — and the spec's biggest remaining risk is one they
explicitly accepted.

## Surface refinement

**A second subject, never a stage.** The definition pass plans the spec's objective and the
process surface together; both land in `## Tasks`.

**Explicit input only.** The human asks — "refine the workflow too", "change the process alongside
the objective" — or supplies `--with-surface`. Like `autonomous`, this is a declaration in the
input, never a reading of the state: `## Impact`, a missing section and the pass's own judgment
cannot trigger it.

**It plans; it does not edit.** Definition writes the spec and nothing else: no workflow, skill,
reference, structure or code file. `/quenching-specs execute` writes the surface files one task
at a time, each with its own commit and `verify:`. That boundary lets the pass obey the doctrine
it is changing.

**Enumerate with the ask.** Account for every process-surface opportunity found while forming the
objective — a workflow, a skill, a reference, their structure or an automation. Fold each into
the task group or park it as one `## Discoveries` line (§Discoveries); nothing is dropped or edited.

**The task group.** Use the grammar of [spec-driven.md](spec-driven.md) §`## Tasks`:

| Contract | Required shape |
| --- | --- |
| Position | `### 1. Process surface` is the FIRST group of `## Tasks`, ahead of the objective's tasks that rely on the new behaviour: the dependency is real and serial. |
| Items | Every item carries `files:` and `verify:`. `files:` names the surface paths listed under `## Impact` in the `Process surface this spec expects to touch` list ([artifacts.md](artifacts.md)), plus any generated `.agents/**` / `.claude/**` mirrors and deploy manifest the source edit rewrites, declared in the same task. |
| Coverage | Every path under that `## Impact` sub-heading is named by a task in the group; a declared path without a task is a review finding. |
| Order | No `[P]` crosses the group: its tasks share the surface and land in order. |
| Timing | Plan the group during definition; execution never infers it. |

**The research profile.** Where the plan needs what current practice says, delegate the reading to
a read-only research sub-agent: `Read`, `Grep`, `Glob`, `WebSearch`, `WebFetch` — never `Edit`,
`Write`, `NotebookEdit` or `Bash`. It returns one table and no raw page text — per row the source
URL, the publication date (`undated` when none), the retrieval date, the claim, and what it would
change in this spec; a claim that cannot carry the three source facts is dropped rather than
hedged. It never decides: the pass chooses the alternative and cites the row in
`## Alternatives Considered` or `## Design`. A web source may support an alternative or a
rationale; it never grounds a binding rule alone
(`quenching/references/align/evidence-doctrine.md` §3). A target that carries its own profile under
`docs/standards/automation/agents.md` §The research profile uses that grant, which never narrows
this one.

**Stop when** every declared surface path is named by a task carrying `files:` and `verify:`,
`.agents/quenching/bin/cq specs parallel --spec <id>` exits 0, and nothing outside the spec was
written.

## Discoveries

**Park the process surface, always.** Every pass — with or without the surface ask, on every
track — parks a finding that the workflow, a skill, a reference, the structure or an automation
around the spec is the constraint as ONE `## Discoveries` line carrying `path:line` and the
consequence. A cost finding is one kind of it, and it carries a counted figure — tokens, round
trips or whole-file reads — read from `cq components session context` or
`cq components session cost`, never from the pass's recollection. Parking is not an offer and
mints nothing.

**Runs when** `## Discoveries` holds a line with no resolution on it — stage 0 of the pass, before
compose, at any derived stage.

**Goal.** Empty the queue an executor filled indiscriminately. Whether a discovery is worth acting
on is this stage's judgment, never the executor's — which is exactly why it was captured without
one.

**Shape.** Grouped (§1) — the lines do not reach each other, and every one of them takes the same
three resolutions, so they all fit on one decision sheet.

Ask about every unresolved line on one decision sheet, one `Dn` per line, each with a
recommendation. Every line resolves **in
place**, so provenance is never lost:

```markdown
- the rate limiter double-counts retries -> promoted: fix-retry-accounting
- the config loader is slow on cold start -> dismissed: acceptable, runs once
```

Three resolutions and nothing else:

| Resolution | When | What it costs |
| --- | --- | --- |
| `promoted: <id>` | it is work someone will do, and the front does not already hold it | one `.agents/quenching/bin/cq specs list --json` to check, then one `.agents/quenching/bin/cq specs new` — offer it, and write the ID back |
| `folded: <section>` | it changes THIS spec | the answer lands in that section in the same edit |
| `dismissed: <reason>` | it is real but not worth acting on, or another spec already covers it | one line, and the reason is the whole value |

**Check the front before minting.** `promoted:` reads the open specs first; where one already
covers the line, resolve it `dismissed: already covered by {id}`.

A line is never deleted, and never left unresolved with a shrug. `dismissed: acceptable` with no
reason is the failure mode to hunt for.

On the simple track and on declared autonomy, `folded:` and `dismissed:` are resolutions evidence supports. **`promoted:` is not** — it
mints a spec, which no unattended pass may do. The line stays unresolved, this stage's stop
condition is **not met**, and the report says so. A `dismissed:` invented to empty the queue is the
failure mode to hunt for here.

**Stop when** every line carries a resolution.

<!-- rationale -->
A queue filled by executors that could not see each other produces the same finding several times,
so the duplicate arrives through the front door and has to be turned away there.

## Approval — the close, and its recommendation

**Runs** always, last. **Goal.** Settle the one fact the sections cannot — **that this spec may be
built** — and say what should happen to it next.

This stage adds nothing to the body: it writes one record and no section. What it shows is always
the same: what the spec now commits to, in the proposal's one line, the task count, the
`verification` policy in force, the declared `docs/standards/` paths, and the biggest accepted
risk.

**No token-budget check precedes approval.** The approval runs no `cq specs estimate`, shows no
estimate and never asks, blocks or recommends decomposition over a budget. Splitting a spec is a
scope judgment §Refine's signals make, never a byte count.

**The screen recommends, rather than defaulting to approve.** It judges what the pass just produced
against §Refine's three signals and leads with the move those signals argue for, as option A of
its decision sheet (§1), marked recommended and carrying the signal that motivated it in its own
text:

| Recommendation | When | What choosing it does |
| --- | --- | --- |
| **Approve** | no signal fired | `.agents/quenching/bin/cq specs record "<id>" approved --set date=<today> --set by=human`, never by editing the provider document directly |
| **Refine** | a scope or critique signal fired, and refine has not run this pass | the choice IS the OK to refine with the lens the signal names, restamping `complexity` if the depth grew, and the pass re-enters at refine |
| **Refine with the premortem** | the spec is irreversible | same mechanics, premortem lens |
| **Stop here** | — | nothing stamped, and the report says what is open |

A pass where refine already ran does not recommend refining again; it recommends approving, and
names what the argument settled. Recommending the raise a human has already taken is asking them to
decide the same thing twice.

**On the simple track, the pass approves, and the record says so.**
`.agents/quenching/bin/cq specs record "<id>" approved --set date=<today> --set by=session`. There is no screen, because
the simple track is the one with nothing left for a human to decide. The review window is **the
spec's URL in the backend**, announced before the pass read anything and repeated in its report. The
stamp is not an inference from the sections and never claims to be one. When the conductor declared
`autonomous`, the stamp is `by=orchestrator-forced` instead. `by:` is what keeps the session's
authority distinguishable from a human's word, and a reader who wants only human approvals filters
on it.

**The gate is a floor, not a verdict.** `ready` means ten sections have content; it does not mean
the spec is good — which is what refine is for. And approval is not a gate either: `execute` on an
unapproved spec asks inline and stamps rather than refusing, so declining here costs nothing but a
question later.

**Stop when** the record carries both a `date` and a `by`, or the human stopped the pass.

## Gathering the evidence — economically, and delegated

<!-- rules -->

Every stage that reads the codebase or `docs/` to answer its own questions — compose's
"read it, do not theorize" included, not only what follows below — does so under
[align/evidence-doctrine.md](.agents/quenching/references/align/evidence-doctrine.md):
the aggregate a `grep`/`gh`/`cq` call produces, never the raw dump; `.agents/quenching/bin/cq components read <path>
--sections "§X"` for `docs/standards/` and `knowledge/glossary.md`, the same way this
plugin's own references already address a section instead of a whole file.

**Park at the read.** A reading that exposes a constraint in the workflow, a skill, a reference,
the structure or an automation around the spec parks it on the spot, under §Discoveries' own
always-park rule — one line, `path:line` and the consequence, a counted figure for a cost
finding. The reading never becomes the edit.

Two uses delegate that reading to a sub-agent, and they do not share one tool profile.

**The dependency sweep** runs **once per spec, at the pass's open** — before compose asks anything,
and before refine does where compose did not run. It is the only mandatory reading of the two: a
cross-file dependency map gathered before anything is asked, never in place of asking. It is asked
for **one table** — every file the proposal's own area touches or is touched by, and what breaks or
goes orphaned if it changes — which lands **dated** in `### Mapa de dependências` under `## Design`.
A spec is swept **at most once**, and never for having crossed a `complexity` level: a spec looks
small exactly while nobody has read its dependencies. Its sub-agent runs the **wider** profile —
read-only in full, everything but `Edit`, `Write`, `NotebookEdit` and `Agent`, `Bash` included —
because a dependency map is exactly the aggregate a `grep`/`gh`/`cq` call produces, and the narrower
profile below could not return it.

**Compose and refine** additionally ask questions that only a reading answers: which alternatives
the codebase actually admits, which `docs/standards/` contract a task would violate, which term
the spec uses in a sense the glossary does not. That reading is **optional and delegable**; the
interrogation never is.

Where it is taken, the sub-agent runs the **narrower** profile — `Read, Grep, Glob` and nothing
else — and returns **one compact table** and no trail:

| Stage | What it is asked for |
| --- | --- |
| refine | candidate whole-shape alternatives, each with cost, benefit and what it forecloses |
| refine | contradictions with a binding contract — the `docs/standards/` doc and the line |
| compose | terms the spec uses in a sense `glossary.md` does not |
| compose | `## Impact` paths no `## Tasks` item names, and tasks naming paths `## Impact` never declared |

All three sub-agents follow the verifier shape: they **inspect and report, never edit**. The
narrower two state an explicit *not checked here* list, which is the false-positive control; the
dependency sweep returns a table with no verdict to hedge, so it carries none.

**The orchestrator keeps every question, every write and every confirmation.** The sub-agent never
talks to the human and never touches the spec — the dependency map included, which the
orchestrator writes into `### Mapa de dependências` under `## Design`, never the sub-agent. Every
sub-agent's findings are material for questions the orchestrator still asks itself, under §1 —
never answers substituted for them.

<!-- rationale -->

Two things that look like this and are not available. **`context: fork` cannot ask a question** —
`sk-fork-gate` is an error, and a fork beside an `AskUserQuestion` grant is incoherent by
construction ([capabilities.md](.agents/quenching/references/components-command-new/capabilities.md)
§`context: fork`); the complex track is nothing but questions. And a sub-agent **does not share
the session's prompt cache** — it runs on a cold context and pays the full first read of every file
it touches, so delegation here is never a cache play.

What it *is* is the delegation test met exactly: the returned table is far smaller than the sweep
that produced it, and the sweep's file reads stay out of the long context that the interrogation
then pays for on **every** turn that follows.

Why the dependency sweep exists at all, and the measurement that would graduate it, are recorded in
`/docs/standards/automation/dependency-sweep.md` — this plugin's own bundle, not a target's.
Named bare because the rules above stand without it; every binding half is here.

## Recording the pass

`refined: {mode, date}` records that a real interrogation happened and which operation ran it. Stamp
it **only** for the two stages that interrogate:

| Stage | Stamps `refined`? | Why |
| --- | --- | --- |
| discoveries | no | it resolves a queue; it does not interrogate the spec |
| compose | `mode: compose` | every gap was questioned rather than filled in silently |
| refine | `mode: refine` | the spec was argued with, and could have lost |
| approval | no | it authorizes; `approved` is its own record |

A pass that ran both stamping stages records the **deepest** one reached — `refine` over `compose` —
because that is the strongest claim the pass supports. The record is `writeOnce: false`
([spec-driven.md](spec-driven.md) §Frontmatter): a later pass restamps it, because a second
interrogation is a new fact, not a correction of the old one. `mode: refine` is the one value that
cannot be earned by completeness, which is what makes it worth reading.

**Never fabricate it.** `refined` is written only after a stage really ran. It is the one field
whose entire value is that it cannot be inferred from the sections, so a stamp on a pass where
nothing ran makes the field worthless everywhere.

**The simple track and declared autonomy still stamp it.** There compose answers its own questions
from evidence, and **that drafting IS the interrogation** — the record says which operation ran it,
never who answered. What the record can never survive is a pass that ran nothing and stamped anyway.
`mode: refine` is unreachable on declared autonomy by construction, since it never refines.

**`approved` carries a second field, and it is never omitted.** `by: human` for a person's word on
the closing screen, `by: session` for the stamp the simple track makes on the session's own
calculation (§Approval), `by: orchestrator-forced` for the stamp a declared-autonomy pass makes under
an `--autonomous` conductor, and `by: low-gear` only as the historical value on older specs. A
record written with no `by:` reads as `human` — that is the only thing that could have written it
before the field existed — so leaving it off a `session` stamp silently claims a human the pass
never had. The tool cannot require a field on a record; this rule is what does.
