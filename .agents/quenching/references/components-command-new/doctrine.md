# The command-writing doctrine

How a command file earns its place in a target repo's context.

<!-- rules -->

**One file, one description, and it is the only always-on text there is.** The collapse to one
file per entry point deleted the second description, not the second file only — so the triggers
and the `Not for:` boundary that used to live in a skill description now have nowhere else to
be. A description written as a `/`-menu label routes nothing.

**This file owns the judgement; the tool owns the thresholds.** Every number a rule below depends
on — the two description caps, the body line limit, trigger position, the `Not for:` boundary —
lives in `docs/standards/automation/skills.md`, and is checked by `.agents/quenching/bin/cq components lint` under a
stable `sk-*` code. This file names the code and never restates the number, so the rule and its
checker cannot drift apart.

| Doctrine rule | Checked by | Code |
| --- | --- | --- |
| Triggers in the second sentence — *routed commands only* | `lint` | `sk-trigger-position` |
| The `Not for:` boundary is present — *routed commands only* | `lint` | `sk-no-boundary` |
| The metadata fits the host's cap | `lint` | `sk-metadata-cap` (error) |
| The description is portable | `lint` | `sk-description-portable` |
| The description fits the 250-character budget, use case first | `lint` | `sk-description-budget` (warn) |
| The body stays under the size cap | `lint` | `sk-body-length` |
| Every numbered step has a criterion | `lint` | `sk-step-criterion` |
| `allowed-tools` is scoped | `lint` | `sk-unscoped-bash` |
| Invocation control is coherent | `lint` | `sk-unreachable`, `sk-invocation-value`, `sk-inert-stage` (error) |
| `context: fork` never beside a mid-flow gate | `lint` | `sk-fork-gate` (error) |
| `effort`/`context` values the host can parse | `lint` | `sk-profile-value` |
| A plugin's commands are cited in a form that resolves | `lint` | `sk-bare-citation` |
| **The no-op test, sediment, sprawl, positive prescription** | **a reader** | — |
| **Every non-default lever carries a stated buy** | **a reader** | — |

Each of those needs a claim about how an agent would *behave*, and no parser makes one. They are
why this file exists, and why a clean `lint` is a floor rather than a verdict.

**Two of those rows are scoped to a *routed* command**, and the reason is the one lever on this
list that changes which rules apply at all. `disable-model-invocation: true` makes a command
**typed-only**: its description leaves every session's context, so no prose routes to it and
`lint` reports neither routing code against it.

The lever is not free: it also makes the command unreachable **by name** through the Skill tool.
Put it on a stage another command's body invokes and that stage goes silently inert — the
conductor is refused, does not fail, and does nothing. That is `sk-inert-stage`, an error, with
the reachable set derived from the command bodies rather than a hand-kept list. Which class a
command belongs to is decided by the admission criterion in
`docs/standards/automation/skills.md`, and the tier its description then owes is decided
there, §The admission criterion.

**Within the routed class, `sk-no-boundary` stays wider than the rule.** It fires on absence alone,
because the tool reads one command and cannot see whether anything competes with it. Where a
boundary is waived against the whole surface (§The three slots below), the warning is reported as
**accepted, with the competitor set that was checked** — named in the report every run, never
silently swallowed.

<!-- rationale -->

`/quenching-components command-new` applies this doctrine to every command it mints or edits;
`/quenching-components align` cites it when judging conformance gaps. Adapted from mattpocock/skills'
`writing-great-skills`, folded into this plugin's own constraints (the description caps, the
bundled-reference pattern, the plan → OK gate).

## Predictability is the root virtue

<!-- rules -->

The reader — human or agent — must be able to predict from the description alone **when the
skill fires and what will exist when it finishes**.

- The description **front-loads the leading concept** — the first sentence says what the
  skill does and to what, in the skill's own vocabulary, before any qualifier.
- **One trigger per distinct branch.** Each distinct way a user asks for the job gets one
  verbatim quoted trigger phrase ("create a skill", "wire a command for this"); a branch
  without a trigger is a branch that never fires, and two triggers for the same branch are
  sediment. Triggers sit in the **second** sentence so a truncated description keeps them.
- The description ends with the boundary — `Not for: <adjacent job> → <owning skill>` — **where an
  adjacent command actually exists**.

<!-- rationale -->

The routing story lives in the description, not in a shared router doc; but a boundary naming
nobody routes nothing and pays always-on rent forever.

A vertical holding three or more skills is the case for one router skill over per-flow references
inside it, with no shortcuts. In a target, `/quenching-components align` asks the human per family and
`cq components migrate --write --router` applies it; `command:new` adds a flow to an existing router
and never edits a plugin agent or a `quenching-*` skill, per `/docs/standards/automation/skills.md`
§A target extends a vertical with its own router.

### The three slots, and the boundary that must be earned

A description carries what it does, when it fires, and — conditionally — when it does not. Nothing
else: how the command works is body, and a reader who needs the step order has already fired it and
paid for the body — cutting exactly that prose from 17 descriptions recovered 2,605 always-on
characters without touching one trigger phrase.

| Slot | Earned by | Absent → |
| --- | --- | --- |
| the leading concept — what it does, to what | always | `sk-no-description` (error) |
| the triggers — one verbatim phrase per branch, second sentence | always | `sk-trigger-position` |
| the boundary — `Not for: <job> → <command>` | **a named competitor** | `sk-no-boundary` |

The two codes in that column are the routed-only pair above, so on a **typed-only** description
neither fires — and none of the three slots shortens, because a description already charged 0 has
nothing to save by cutting. What changes is who the slots serve: the human picking from the `/`
menu, who has no routing to fall back on. The competitor test below is what decides the third slot
in **either** class; a command with no neighbour has nothing to discriminate against, whoever is
reading.

**A competitor is named, not imagined.** Two commands compete when one of these holds, and the test
is run against the surface, never from memory:

- they act on the **same axis folder** — the same `/<namespace>:` — so a user who knows the domain
  but not the verb can land on either;
- a **plausible user phrasing routes to both** — their trigger vocabulary overlaps, or one's job is
  the obvious next step after the other's.

Neither holding, the boundary is **waived**: it is deleted where it exists and never invented where
it does not. A waiver is stated with the competitor set that was checked, because "nothing competes
with this" is a claim about the whole surface and only a reader holding all of it can make it.

**A quoted trigger is never cut for length.** Shortening a description by deleting a trigger is how
a command quietly stops firing for the user who worded it differently, and only a measured miss
retires one — `/quenching-components command-eval`'s, and that measured retirement is the one edit
measurement authorizes. A trigger that looks like sediment is **reported** with the
`/quenching-components command-eval <command>` that decides it.

## The loading hierarchy

Doctrine flows downhill; context is paid at each level, so each fact sits at the **lowest**
level that still reaches it in time:

1. **In-body steps** — the numbered workflow. Only what every run needs, in execution
   order. This is the most expensive real estate: it loads on every invocation.
2. **In-body reference** — sections after the workflow (`## Special cases`,
   `## Invariants`) consulted when a step points at them.
3. **A bundled reference file** — doctrine, tables, and formats loaded only when a step reads
   them. It lives beside the skill, in `references/` inside the skill's own folder, and is
   cited by absolute path (`.agents/skills/<name>/references/<file>.md`); one read by two
   skills or more lives in the shared `.agents/quenching/references/<name>/<file>.md`. Shared procedure
   lives **once**, in its owning file; every other command cites the owner and never restates
   it (restatement is how copies drift).

## Steps carry checkable completion criteria

<!-- rules -->

A step is done when a stated condition is observable — a file exists, a diff is empty, a
command exits 0, a table matches disk. "Handle the edge cases" is not a step;
"`.agents/quenching/bin/cq components registry reindex` reports `changed: false`" is. Write the criterion as
`**Done when:** …` — that literal marker is what `lint` counts (`sk-step-criterion`).

<!-- rationale -->

A skill whose last step has a checkable criterion cannot end early and call itself done — that is
the guard against premature conclusion.

## The no-op test

For every line, name an input on which the agent behaves **differently** because the line
exists. A line with no such input — restated context, praise for the approach, a rule
already enforced by a stricter rule — is a no-op: delete it. Apply the test on every mint
**and every edit**; a skill body only grows when new behavior arrives, and prose that stopped
changing behavior leaves with the edit that obsoleted it.

## Positive prescription

State the target behavior, not the prohibition: "name the file after the folder path" beats
"don't use arbitrary names" — a prohibition leaves the agent choosing among everything not
forbidden, which is the opposite of predictability. Negation is reserved for **hard
invariants** whose violation is irreversible or costly (a deletion, a clobber, an unguarded
write), stated once, with the consequence attached.

## The execution profile

The writing rules above govern what a command **says**; which capabilities it **uses** —
`context: fork`, a model or effort pin, a subagent, a hook, the invocation-surface controls,
dynamic context — is a second authored decision set with its own doctrine, owned by
[components-command-new/capabilities.md](capabilities.md). The default profile is empty; every departure
is priced there and enters the mint's plan with its stated reason.

## Named failure modes

Judge a draft (and an existing skill under review) against these six; each has a named cure:

| Failure mode | Smell | Cure |
| --- | --- | --- |
| **Premature conclusion** | The skill can claim "done" while work remains | End-state criteria per step; a final self-check step that diffs the promise against disk |
| **Duplication** | Procedure restated from another command or doc | Cite the owner's reference file by absolute path; one owner per fact |
| **Sediment** | Lines surviving edits that no longer change behavior | Re-run the no-op test on every edit; delete with the edit that obsoleted them |
| **Sprawl** | Body creeping toward the context it was meant to save | Push doctrine down the hierarchy; `lint` reports the caps (`sk-body-length`, `sk-metadata-cap`) — staying *well* under them is the judgement it cannot make |
| **No-op** | A line no input can distinguish from its absence | The no-op test, at mint and at every edit |
| **Negation** | Rules phrased as prohibitions with no stated target | Rewrite positively; keep negation only for hard invariants with consequences |
