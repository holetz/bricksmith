# Specs execution — handoff cadence and boundaries

This file owns the resumability cadence, section boundary and progress banner of the execution
loop. The verify/commit mechanics and delegation rules live in focused siblings.

## The Handoff cadence

<!-- rules -->

The four events are the command body's. Why four events rather than a threshold or a judgment:
§Tooling asides.

**The four events say when a Handoff update happens; they do not add another boundary event.** Since
`## Handoff` gained per-section blocks — a small global block plus one `### N.` block per `## Tasks`
section — an update at any of the four events targets ONE of the two:
`.agents/quenching/bin/cq specs section <id> Handoff --write --scope global` for the evergreen block, or `--scope
current` for the block of whichever `### N.` still has open work. A section's block closes — stops
being targeted — the moment its last task commits, but that close adds no fifth event: `--scope
current` always resolves to whichever section still has an open task, so once `### N.` has none
left, the NEXT of the four events to fire already writes `### (N+1).` instead, wherever in the run
that next event happens to land. A section whose every task commits between two Handoff updates
never gets a block of its own at all — `--scope current` opens one on demand when the next event
finally fires, borrowing that section's own `## Tasks` heading as its title.

## The section boundary — where a run may stop

<!-- rules -->

A `## N.` section's last task committing, with another section still ahead, is a **clean boundary**:
the loop keeps the task commits and continues with the next task.

- **The boundary measures nothing and asks nothing.** No session-size read runs there, no budget is
  printed and no new session is offered; the loop starts the next task. The conductor budget,
  measured by the orchestrator between waves, is the one that ends an autonomous run. A count never
  stops a task midway.
- **Nothing extra is written.** `## Handoff`, `git log`, and the `subjects` `.agents/quenching/bin/cq specs status`
  returns already carry everything a fresh session needs; the boundary adds no record and no fifth
  Handoff event.
- **Only a human-required question or a real blocker pauses the loop.** A section boundary is
  neither; the pause conditions remain the command body's own.
- **The contract still ends at the last commit.** Reaching this boundary is not a close-out: the
  branch review, the merge and the archive remain `/quenching-specs conclude`'s, exactly as they are
  for a run that goes to the end.

## The loop's progress banner

<!-- rules -->

Progress as it happens, not a report — §The report mold governs the run's step 7, this governs the
loop itself. Its glyphs are the mold's and mean the same. **Only the mold's own header line opens
with a `##` that carries the slug** — this banner prints plain text, never a heading, so the two can
never be confused for each other:

```
Building: <slug>

Task 3/7 — 3.2 <task title>
  files: src/middleware/auth.ts, src/config/limits.ts
✓ self-review: clean
✓ chain: verify && check && commit
    verify: pnpm test middleware/ — passed
    checked 3.2 (subject: plan/<slug>: 3.2 <task title>)
    committed a1b2c3d — subject matches
```

## After approval — decide, record, stop on a real block

<!-- rules -->

The contract is `/docs/standards/workflows/task-execution.md` §After approval, the executor decides,
records and stops only on a real block; this section is its loop-side procedure.

- **Decide what the evidence answers.** The spec, the code, the standards declared under `## Impact`
  and the glossary answer most questions; the recorded intent covers the rest by criterion. The loop
  does not ask.
- **Record each decision as one line.** `.agents/quenching/bin/cq specs discover "<id>" "decision: <what> — <evidence or
  criterion>"`. A `decision:` line is not one of the four Handoff events and writes no Handoff.
- **Stop only on a real block**, the closed list of the standard: attempts stop converging; a task
  contradicts a standard and the evidence does not choose; the plan needs a new grant, a changed
  frontmatter key or a lowered bar; an undeclared irreversible, merge, push or deploy action; a
  required proof that cannot run; `approved` absent under `autonomous`. Outside the list the loop
  decides and goes on.
- **A pre-existing red has its own route** (`/docs/standards/workflows/task-execution.md`
  §Pre-existing errors): proved on the base, fixed by a declared `[fix-preexistente]` task or captured as
  a spec. Uncontained under `autonomous`, it is a real block that names the captured spec.
