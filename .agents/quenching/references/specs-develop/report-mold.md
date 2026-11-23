# The specs report mold — one terminal shape for every command

This file owns the literal report blocks and the translation rule shared by every
`/quenching-specs-*` command. Lifecycle and provider-tool facts live in their own focused
references; a command loads this file only when it emits a report.

## The report mold

<!-- rules -->

**Every `/quenching-specs-*` command's terminal report is built from the blocks below**, and a command body
declares only its own deltas: which body blocks it emits, which columns they carry, and which
next-step candidates exist under which condition. The mold is **literal — copy a block and
substitute**, never compose a shape per command.

<!-- rationale -->

Measured across the eight bodies before this section existed: two rendered a literal block and six
described their report in prose, producing six different closing verbs, no shared glyph, an `Age`
column with no declared source, and `title` unused by every table although `.agents/quenching/bin/cq specs` had been
emitting it all along. A shape restated in eight bodies is the fan-out
`/docs/standards/quality/computed-fact-prose-fanout.md` describes — it ages in seven the
moment it changes in one, with every checker green.

### The three bands

<!-- rules -->

Three bands, in this order, always: **header · body · next step**.

- The header and the next-step block are **fixed** — always printed.
- Each body block is declared fixed or optional by the command emitting it.
- **A fixed block with nothing in it prints its title and `—`; an optional block with nothing in it
  is omitted whole.**

| Glyph | Means |
| --- | --- |
| `→` | the recommended row, or the recommended next-step line |
| `✓` | a stage that passed |
| `!` | blocked, or a section present and empty |
| `·` | a field separator |
| `—` | no value — a **not-yet**, never a defect |
| `…` | elision (`… N more`) |

`✓ ! ·` are the three `.agents/quenching/bin/cq specs status` already prints for section state, and mean the same here.

<!-- rationale -->

The empty-fixed / omit-optional split is the rule `commands/knowledge/status.md` carries and its `specs/`
sibling does not. A fixed block that vanishes when empty is indistinguishable from a pass that
dropped it; an optional block printed empty is noise on every run.

### The header line

<!-- rules -->

One line, then the body. Two forms — front-wide:

```
## The specs front — 6 specs in plans/
```

One spec:

```
## 41 — Budget tokens per session
executing · 5/9 tasks · quenching:specs/41.md
```

**The third field is the locator the tool returned** — the `path` field `.agents/quenching/bin/cq specs new`, `status`,
`list`, `next --front` and `section --write` all carry — never a filename the body assembled. The git store returns
`quenching:specs/<id>.md`; a deprecated `github` backend returns the issue URL and `azure-boards`
the work-item URL. The card's URL is a separate field.

<!-- rationale -->

A body that prints a path the backend never wrote sends a human to a file that does not exist.
`/quenching-specs create` carried that rule alone, and all four single-spec commands print a locator.

### The spec table

<!-- rules -->

One ordered column set. **A command omits any column, never reorders, and never invents one.**

**The tool renders it; a command quotes what the tool printed.**
`.agents/quenching/bin/cq specs next --front --table [--columns C,C] [--order rank|priority]` prints this table, and
`--columns` is the mechanism for the omission rule above — the order is the tool's, so a subset
cannot come back reordered. A command that composes these cells itself is a second renderer of one
ranking, which is the fan-out
`docs/standards/architecture/report-mold.md` forbids and which this table had three of before
the flag existed.

| Column | Source | `—` when |
| --- | --- | --- |
| `Spec` | `next --front .candidates[].slug`, `list[].slug` | never |
| `Summary` | `title:` — the spec's native descriptive title | never |
| `Stage` | `.stage` — one of the nine derived stages | never |
| `Tasks` | `tasks.checked`/`tasks.total`, then `· N blocked` | `total` is 0 |
| `Priority` | `records.priority.level` and `.criticality` | the record is unset |
| `Complexity` | `records.priority.complexity` — how much a human must be in the loop | the spec was never ranked |
| `Records` | which of the seven are set | none is |
| `Age` | `next --front[].ageDays`, **or** days since `list[].date` | never |
| `State` | `sp-spec-blocked`, `sp-spec-complete`, the branch fact, `sp-spec-stale` with the age, else `—` | nothing to say |

```
| Spec | Summary | Stage | Tasks | Priority | Complexity | Age | State |
| --- | --- | --- | --- | --- | --- | --- | --- |
| → 41 | Sessions never expire, so a stolen token is good forever | executing | 5/9 | 1 · high | medium | 3d | on this branch |
| 58 | Rate limit the public API | ready | 0/12 | 2 · high | low | 9d | — |
| 63 | A failed webhook is dropped and nobody is told | proposed | — | — | — | 21d | — |
```

**`Summary` is the established table label, sourced directly from `title:`.** The title is the
one short description, so the table has no fallback path and no missing-field warning to count.

Which command carries which:

| Command | Columns |
| --- | --- |
| `/quenching-specs status` | `Spec` `Summary` `Stage` `Tasks` `Records` `Age` `State` |
| `/quenching-specs triage`, the proposal | `Spec` `Summary` `Stage` `Tasks`, `Priority` and `Complexity` showing the **current** values, plus `Proposed level` `Proposed criticality` `Proposed complexity` `Reason` — the four proposal columns are the model's judgment and the only ones it composes |
| `/quenching-specs triage`, the report | `--order priority`, the four proposal columns dropped, `Priority` and `Complexity` now showing the approved values |

**`Complexity` is its own column because it answers its own question.** `level` and `criticality`
rank a spec against the others; `complexity` says how much a human has to be in the loop while it is
built — the criterion in
[spec-driven.md](.agents/quenching/references/specs-develop/spec-driven.md) §The scale.
Folding it into the `Priority` cell would read as a third rank, and a reader
scanning for which specs cannot be run unattended would have to parse three axes out of one field.
The two empty together, never one without the other: `/quenching-specs triage` floors a guess at
`medium` rather than omitting it, so a `—` here means the spec was never ranked at all — never that
it is easy.

**`Age` names the call it came from.** `list --json` has no `ageDays` and `next --front` does; both
are days since the spec's `date`, and `date` is in `list`, so either derives it — but say which,
because a figure with no source is one nobody can check.

**`State` mixes two populations, and they are not interchangeable.** `sp-spec-complete`,
`sp-spec-blocked` and `sp-spec-stale` are **prose-only codes** the agent computes from `tasks` and
`Age`; `.agents/quenching/bin/cq specs` never emits them. A code the tool does emit is quoted, never invented; a
prose-only code is never presented as tool output.

### The findings table

<!-- rules -->

One row per finding, for the split by what closes each that a read-only view owes
(`/docs/standards/architecture/read-only-views.md`):

```
| Spec | Code | What it is | Closed by |
| --- | --- | --- | --- |
| 58 | `sp-empty-section` | `## Risks` present and empty | nobody — a human writes it |
| 41 | `sp-spec-complete` | every box ticked | `/quenching-specs conclude 41` |
| — | `sp-stray-file` | `plans/notes.txt` | nobody — a human removes it |
```

Every code is one
[specs-align/conformance.md](.agents/quenching/references/specs-align/conformance.md)
defines — never invented, never softened. A front-wide finding leaves `Spec` as `—`.

### The observations table

<!-- rules -->

What a sweep **noticed but does not rank** — a near-duplicate pair, an overlap of scope between two
specs, a sequencing one spec imposes on another, a finding another spec has already fixed. It
carries no `sp-` code, so it is never a row of §The findings table: every code there is one
[specs-align/conformance.md](.agents/quenching/references/specs-align/conformance.md)
defines, and inventing one to fill the column puts a finding the align does not fix into the align's
own vocabulary.

```
| Observation | Specs | Recommended action |
| --- | --- | --- |
| one root cause, three specs | fix-load-config-root-argument, corrigir-load-config-resolvendo-repo-pelo-cwd | `/quenching-specs conclude corrigir-load-config-resolvendo-repo-pelo-cwd` |
| overlapping scope | reduce-execute-conclude-cost, cut-conclude-run-cost | `/quenching-specs develop cut-conclude-run-cost` |
| the original defect is already fixed | isolate-functional-checks-probes | nobody — a human decides whether it still has a subject |
```

- **`Observation` is what the sweep noticed, in one phrase** — the *kind* of thing it is, taken from
  the sweep's own reading of the front, never a retelling of the other two columns. It never reads
  `—`: an observation with nothing to say is not a row.
- **`Recommended action` is runnable as printed**, exactly as §The next-step block is: the
  plugin-prefixed slash spelling
  ([align/sweep-doctrine.md](.agents/quenching/references/align/sweep-doctrine.md) §7)
  **with its real argument substituted**. A literal `<id>` reaching the output is a defect, and so
  is a bare command name whose argument the reader has to reconstruct from the rest of the row.
- **An observation no command closes says what a human must decide** — `nobody — <the decision>` —
  rather than naming a command that does not fit it.
- **`Specs` carries every spec the observation spans**, comma-separated. An observation over three
  specs that names one has lost the fact that made it an observation; a front-wide one reads `—`.
- The block is **optional**: omitted whole when there are none, never printed empty.

Which of the two tables a row belongs to is decided by the code, never by the command emitting it: a
finding carrying an `sp-` code goes to §The findings table, and anything the sweep merely noticed
comes here.

### The session-size line

<!-- rules -->

**Printed by `/quenching-specs develop`, `/quenching-specs execute` and `/quenching-specs conclude`
only**, as the last line of the body, right before §The next-step block. One read, once per report:

```bash
.agents/quenching/bin/cq components session context --json
```

```
Session: 212480 / 200000 (main) — 12480 over budget
```

The exit code decides the tone, never the line's presence:

| Exit | The line | The next-step block |
| --- | --- | --- |
| `0` | `<peak> / <budget> (<role>) — <remaining> left` | unchanged — the next stage is an offer |
| `1` | `<peak> / <budget> (<role>) — <over> over budget` | the `→` line recommends the next stage **in a new session**, with the exact command — `→ /quenching-specs execute 41   — in a new session: this one is over its budget`; a question the command already asks carries that recommendation as option A of its decision sheet, marked recommended — or first and marked "(Recommended)" on the menu the human asked for |
| `2` | `Session: unmeasured — <code>` | unchanged — an offer, never a recommendation without a number |

- **The stage end triggers the read; the number decides only the tone.** Nothing stops, nothing is
  asked that the command did not already ask, and nothing is recorded: the resumption trail stays
  `## Handoff`, `git log` and the recorded subjects.
- **An `autonomous` run prints no line** — it skips this mold whole, and a conductor's budget is
  its own.

<!-- rationale -->

Four measured `/quenching-specs execute` sessions peaked between 250k and 370k, each stage paying
for everything the earlier ones had loaded. `.agents/quenching/bin/cq components session context` (epic 1627 S3) is the
measurement, and the main-session budget is
`docs/standards/automation/context-discipline.md` §Measured context budgets. The peak excludes
the request in flight, so the line slightly undercounts; it only sets a tone, so that is accepted.

### The next-step block

<!-- rules -->

Always last. Nothing is printed after it.

```
Next step
→ /quenching-specs execute 41   — 5/9 tasks, 3.2 is open
  /quenching-specs develop 41   — 2 open discoveries
  /quenching-specs triage        — 4 specs carry no priority
```

- **Runnable as printed** — the real spec ID substituted. A literal `<id>` reaching the output is a
  defect.
- Exactly one `→` line.
- **The `— reason` tail appears only when there is more than one line.** A single candidate needs no
  justification.
- The printed spelling is the plugin-prefixed slash. A body that then *invokes* passes the registry
  name, without the leading slash, to the `Skill` tool — two spellings of one command, and the three
  forms are owned by
  [align/sweep-doctrine.md](.agents/quenching/references/align/sweep-doctrine.md) §7,
  which this mold selects from rather than restates.
- **The block is a suggestion, never an offer** — with two named exceptions, both printing the
  block and *then* ending the turn on a decision sheet rather than stopping: `/quenching-specs execute` at
  100%, and `/quenching-specs create`, whose closing screen is exactly that offer (§The one screen
  in its own body) — develop it now, with or without questions, or stop here. `/quenching-specs status`
  prints the block and stops — no plan, no "shall I". The form is identical across the three;
  only what follows it differs.

<!-- rationale -->

Before this block: `/quenching-specs conclude` printed no forward step at all, and `/quenching-specs execute`'s chain
into it named the command without the slug — so the two commands that end a spec's lifecycle
printed the least runnable suggestions in the surface, at the moment a human most needs one.

### Quoting a tool's own output

<!-- rules -->

**Verbatim means a fenced block, unrewritten, unsummarized, carrying the exit code.** It covers
`.agents/quenching/bin/cq specs`, `git`, and any check a body runs.

**An inconclusive result is named as inconclusive, never counted as passed.** A check that cannot
tell *this failed* from *this could not be measured* has returned no verdict.

<!-- rationale -->

Seven wordings of *verbatim* were in circulation across create, align, status, execute, conclude and
triage with no owner, and the inconclusive rule existed only in `/quenching-specs conclude` — the one command
where acting on a false green is unrecoverable, but not the only one that runs checks.

### What is translated and what is not

<!-- rules -->

This file is English; **the report a command prints is not**. It follows the target repo's declared
tag ([/docs/standards/agents/communication.md](/docs/standards/agents/communication.md) §What it
governs). So each column has a **canonical name**, which is its address above, and a **printed
label**, which follows the tag.

- **Translated:** band titles, column labels, reasons, state text, `Next step`.
- **Canonical whatever the tag:** the slug · `stage` values · record names · `sp-*` codes · the
  thirteen `##` headings · command names.

The same header row in a repo declaring `pt-BR`:

```
| Spec | Título | Estágio | Tarefas | Prioridade | Idade | Estado |
```

The labels moved; nothing a grep depends on did.

<!-- rationale -->

A column label is prose, not one of the six canonical categories that standard fixes, so it
translates like the rest of the report. Leaving the labels English would be the exact failure it
names — reading the tag at session start and still reporting in English.
