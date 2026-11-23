# Mapping a Claude or Codex plan onto a spec's canonical sections

Read only on the plan-source path of `/quenching-specs create`, inside step 3 — the sentence path
never carries a plan to map.

## The mapping

A Claude or Codex plan is prose with loose headings, and they may be in any language
(`## Context` / `## Contexto`) — **match on meaning, never on the literal string.**

| Native plan part | Canonical section |
| --- | --- |
| summary, overview, context/contexto, background, the problem, why now | `## Problem` |
| objective, goal, main changes, what it changes | `## Proposal` |
| non-goals, "fora de escopo", what it will not do | `## Out of Scope` |
| declared scope, files and docs it will touch | `## Impact` |
| acceptance criteria, validation criteria, test plan, how to confirm it worked | `## Validation` |
| decisions, chosen approach, architecture, approach, "Decisões" | `## Design` |
| approaches weighed and dropped | `## Alternatives Considered` |
| open questions, "a decidir", unresolved choices | `## Open Decisions` |
| risks, trade-offs, "Riscos" | `## Risks` |
| phases, steps, numbered work, "Etapas" | `## Tasks` (`- [ ]` under `### N. <Section>`) |
| verification steps or testing tasks | `## Tasks` (trailing verification items) |
| assumptions and pending points | `## Open Decisions` when a choice remains pending; `## Risks` when the assumption exposes a threat or trade-off |
| a version bump, a changelog entry, a manifest re-stamp, a release step | **nothing** — the merge owns it, not a task |
| updating docs the build has not produced yet, or the plan's own close-out steps (archive, merge, open the PR) | **nothing** — `/quenching-specs conclude` owns both |

The last two rows drop parts the source plan really does carry. A native plan is written by
somebody thinking about a whole delivery, so its "Etapas" routinely end in *bump the version* or
*update the docs*; neither survives the conversion, because what the release *is* only becomes
knowable once the last task lands, and two branches scheduling it collide on the same number.
`/quenching-specs conclude` settles them against the base actually being merged into. Dropping them
is not a loss of information — it is filing them with the command that can act on them.

The axis is **declared versus revealed**, not docs versus code: a `/docs/standards/**.md` path the
plan declares the spec will write still becomes a checkbox, and is still required to.

A plan that carries none of the middle rows produces a spec with `## Problem` and `## Proposal`
and stops. **There is no rule that a converted plan must reach the ready gate**;
`/quenching-specs develop` takes it the rest of the way.
