# Authoring a spec's sections

The per-section authoring doctrine every `/quenching-specs-*` command applies when it writes into a spec. The
**layout, the thirteen sections, the gates, the derived stages and the `.agents/quenching/bin/cq specs` surface** live
once in
[spec-driven.md](.agents/quenching/references/specs-develop/spec-driven.md) and are
cited, never restated here.

There is **no delta and no sync**. A spec proves its durable rules straight into `docs/standards/`
while it is built (`/quenching-specs execute`), and `## Impact` is where it *declares* that scope. Two things
in a spec are machine contracts — the `## Tasks` checkboxes and the one parsed sub-heading of
`## Impact` — and everything else is prose for a human reviewer.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## Writing a section through stdin

<!-- rules -->

`.agents/quenching/bin/cq specs section <slug> "<Heading>[,<Heading>…]" --write` (bodies on stdin) creates each heading in
canonical position on first write, so creating and revising are the same call. An emptied section
becomes an explicit `- none — <reason>`, never a deleted heading (§The explicit-none rule).

**An empty body refuses** (exit 2, `sp-write-empty-body`, naming the empty headings) and writes
nothing — a tty stdin, a producer that failed upstream of the pipe, or a `## <Heading>` block with
no body in the stream. `--clear` beside `--write` is the explicit ask that lets an empty body
through and puts the template's guidance back under that heading; without `--write` it refuses
(`sp-clear-without-write`).

**The bodies go on stdin as one heredoc, in the same call** — a scratch file, a `mkdir` and a `cat`
are three turns buying what one already does, and a pass that filled six sections over six calls
paid six round trips for one edit:

```bash
.agents/quenching/bin/cq specs section "<slug>" "<Heading>,<Other Heading>" --write <<'BODY'
## <Heading>

<the drafted section, verbatim>

## <Other Heading>

<the other drafted section, verbatim>
BODY
```

Quote the delimiter (`<<'BODY'`) so nothing in the prose is expanded by the shell.

The `## <Heading>` lines in the stream are the delimiter, and the set they carry must equal the set
declared on the command line — a mismatch, a repeat, or a heading outside the canonical thirteen
refuses (exit 2) **without writing any of them**, so a rejected edit leaves the spec exactly as it
was. One heading with a raw body and no `## ` line is the singular form and is unchanged.

## The explicit-none rule

<!-- rules -->
A section with nothing in it is answered `- none — <reason>`. Never delete the heading, and never
pad it with restated text from another section.

1. **`- none — <reason>` counts as filled.**
2. **A present-but-empty heading is malformed.** It is neither an answer nor a not-yet.
3. **An absent heading before its own gate is legal** — a *not-yet*, not an omission.

**Never invent the explicit none.** `- none — <reason>` is an *answer*; writing one the human never
gave is worse than leaving the heading absent, because it looks decided. And never write thirteen
of them at creation: a spec that did would derive as `designed` and clear the whole ready gate
without anyone having thought anything.

`.agents/quenching/bin/cq specs new <id>` stamps the frontmatter and `## Problem` alone, from
`assets/specs/templates/spec.md`. Every other heading is created on first write by
`.agents/quenching/bin/cq specs section <id> "<Heading>" --write`, in canonical position. Do not invent new top-level
headings — one outside the canonical thirteen is a stray and `validate` flags it — and never paste
this doctrine into the spec.

<!-- rationale -->
An omission and a null are different facts. "We drew the boundary and nothing fell outside it" and
"nobody ever drew the boundary" read identically when the section is missing, and only one of them
is safe to build on. An explicit null is strictly more information than an absent heading, and it
costs one line.

## The nine definition sections

`## Problem` … `## Risks`, all gated on `ready`. Keep the set tight enough to read in one sitting.

- **`## Problem`** — the problem or opportunity and **why now**, in the repo's own terms (use
  `glossary.md` vocabulary). One or two paragraphs.
- **`## Proposal`** — the change as a short bulleted list of outcomes: WHAT will be true afterwards
  that is not true now, never HOW. Each bullet is something a reviewer could later check was
  delivered.
- **`## Out of Scope`** — what the spec deliberately does NOT do, and why each was ruled out. This
  is where a rejected adjacent idea goes so it stops being re-proposed, and where a deferred piece
  is recorded as deferred rather than forgotten.
- **`## Impact`** — declared scope for human review, and the spec's **one parsed contract** (below).
- **`## Validation`** — how anyone confirms the spec worked: the commands and the output they must
  produce, the fixtures, the invariants that must still hold. **Load-bearing**: a `## Tasks` item
  with no `verify:` line falls back to it. `- none — <reason>` here is a claim that the spec is
  unverifiable by construction — make it deliberately or fill the section in.
- **`## Design`** — each decision with the alternatives weighed and why this one, plus the binding
  contracts the design must not contradict (the relevant `docs/standards/`, the existing code
  shape, external limits). State a decision as a **durable rule**, not a diary entry: this is the
  material `/quenching-specs conclude` later distils.
- **`## Alternatives Considered`** — whole-shape alternatives rejected at the spec level, each with
  the reason it lost. Per-decision alternatives stay inside `## Design`; this section is for the
  ones that would have changed the spec's shape. The next person with the same idea reads why it
  was already turned down.
- **`## Open Decisions`** — what is deliberately still undecided, and **how each gets decided** —
  the evidence or the moment that settles it, never a bare "TBD". A task may be written to close
  one (`- [ ] 6.5 Decide per ## Open Decisions whether …`).
- **`## Risks`** — what could go wrong and the mitigation for each. A risk taken knowingly is
  written `ACCEPTED — <why>`; a silent failure mode is the shape to hunt for.

## `## Impact` — the parsed sub-heading

<!-- rules -->
Four sub-headings, and exactly one is machine-checked:

```markdown
### Standards this spec will write into docs/standards/

- `docs/standards/auth/session-tokens.md` — how a session token is minted and revoked

### Standards at `authority: background` this spec may resolve

- `docs/standards/auth/rotation.md` — proves out, promote to `current` if it holds

### Product code this spec expects to touch

- `src/auth/session.ts` — the mint/revoke path

### Process surface this spec expects to touch

- `quenching/references/specs-develop/artifacts.md` — declare the process surface scope
- `quenching/skills/quenching-specs/SKILL.md` — route the revised authoring flow
```

`.agents/quenching/bin/cq specs validate` parses **only the first sub-heading** (`parse_impact_standards`) and emits
`sp-impact-uncovered` (warn) for any `docs/standards/**.md` path bulleted there that no `## Tasks`
item names. Keep the heading text verbatim — it is the anchor.

A spec with no such sub-heading declares nothing and is never flagged — **the check is opt-in by
writing the heading**. An unfilled `<placeholder>` declares nothing either.

This sub-heading is also the **declared/emergent line**: a `docs/standards/` doc named here *and*
by a task is written during execution; anything the work merely reveals is one `.agents/quenching/bin/cq specs discover`
line and is written at conclude
([execution-delegation.md](.agents/quenching/references/specs-execute/execution-delegation.md) §Declared
versus emergent `knowledge/`).

The process-surface sub-heading **declares scope for the human reviewer**, like the product-code
one. It is **unparsed**: no validator reads it, and writing it is never itself a finding. **Every
path it names gets a task** (§`## Tasks`). A declared surface path that no `## Tasks` item names is
a review finding, not a validation error: the reader checks coverage; the only automation is the
task's own `verify:`.

<!-- rationale -->
The first sub-heading is parsed because it promises standards the spec will write. The three
beyond it are deliberately **not** parsed: they name paths the spec does not promise to write as
standards, and checking them would flag a spec for not delivering a standard it never claimed.

## `## Handoff` — small, and refreshed on events

<!-- rules -->
The context an executor needs and cannot derive: the state of play, the conventions in force, what
was already tried. **Small by construction, and scoped by construction**: it carries a small
evergreen **global block** plus one block per `### N. <Section>` — the same grouping `## Tasks`
already uses — and a task is sent only the global block plus the block of its own section, never a
closed section's block and never the whole `## Handoff`.

It is warned on (not gated) once the ready gate is met, and it is rewritten on **four events** —
the run pauses · a task is written blocked · a discovery is recorded · the run's last commit lands
— rather than when someone judges it stale. What changed is what a rewrite touches, not when one
happens: `.agents/quenching/bin/cq specs section <id> Handoff --write --scope global` for the evergreen block,
`--scope current` for the block of whichever section still has open work — never both, and never a
closed section's.

A section's block **closes** the moment its last task commits, but closing is not a fifth event:
`--scope current` always resolves to the section the next open task belongs to, so once every task
in `### N.` is checked, the next of the four events to fire targets `### (N+1).` instead — `### N.`
is simply never addressed again. Nothing marks it "closed" in the text; the absence of any further
write **is** the close, the same way a merged branch needs no explicit "done" flag. A section whose
tasks all land between two rewrite events never gets a block of its own at all, and that is fine —
`--scope current` opens one on demand, borrowing `## Tasks`' own heading text as its title.

Each of those four is a moment the executor *just finished doing something*, never one where it
appraises something: that is the property that makes the rule survivable unattended, and it is what
any future edit has to preserve. What a resumed run can derive on its own — which tasks are done,
which commit carried each — lives in `git log` and in the `subjects` `.agents/quenching/bin/cq specs status` returns;
this section carries only what nothing derives.

<!-- rationale -->
Staleness is this section's only failure mode, and an event-bound rule is the one cure that
survives an unattended run. The cadence they replaced was one rewrite per committed task,
which on a measured 13-task run produced rewrites ~90% identical to one another — the section is
sent with every task, so a near-identical rewrite is paid for on both sides and buys nothing on
either.

Scoping what travels is the same fix applied one layer deeper: on a real 29-task, 7-section run
(`configurable-spec-backend`), the flat `## Handoff` resent ~79,556 characters (~19,900 tokens)
across the run, peaking at ~5,680 characters per task in the closing sections — mostly the history
of sections already done, which a task in `### 6.` or `### 7.` never needed. A rewrite happening
less often already fixed *when* the resend happened; this fixes *how much* each resend carries.

## `## Tasks`

The implementation checklist `.agents/quenching/bin/cq specs` parses: checkboxes `- [ ] <id> <text>` grouped under
`### N. <Section>` headings. `.agents/quenching/bin/cq specs task --spec <id> --check <id>` flips a box mechanically —
never hand-edit the checkbox character.

Shape it so `/quenching-specs execute` can walk it top to bottom:

- **Ordered by dependency**, grouped into coherent sections (setup → core → wiring → tests → the
  standards `## Impact` declares). Each item is one reviewable unit of work — small enough to
  check off honestly, large enough not to be noise. Each task gets its own commit. The section
  grouping remains the unit that `verification: per-section` verifies after and that execution can
  delegate by file or by a coherent set of tasks.
- **Within what the dependency order allows, group by file.** A section is the unit execution
  delegates — *"delegate by file, or by section of tasks, never task by task"*
  ([execution-delegation.md](.agents/quenching/references/specs-execute/execution-delegation.md) §The cost of
  delegating) — so six tasks over one file, kept in one section, cost that file **one** read; the
  same six scattered across four phase-shaped sections cost it four. A `[P]` group is bounded to
  one `### N.` section too, so scattering forecloses the parallelism as well as the delegation.
  Dependency wins every time the two disagree; this decides only what was already free to move.
- **The standards-writing items are explicit tasks, not an afterthought.** Every `docs/standards/`
  path declared under `## Impact`'s parsed sub-heading gets its own checkbox — e.g.
  `- [ ] 4.1 Write docs/standards/auth/session-tokens.md (authority: current once proved)`.
  Building the spec *is* proving the rule, so writing the standard is part of the work, honestly
  `authority`-graded when it lands. This is the pairing `sp-impact-uncovered` checks: **name the
  path in the task text** so the match is findable.
- **Verification belongs in the list** — a task whose completion is "tests pass" or "the standard
  is written and self-checks clean" is a task, not an implicit hope.
- **What the merge owns is never written here.**
  A version bump, a changelog entry, a manifest re-stamp; the `/docs/` the work *revealed* rather
  than declared; and the cycle's own closing actions — all three belong to
  `/quenching-specs conclude`, which settles them once the branch is written and what the release
  *is* is finally knowable. The axis is **declared versus revealed**, not docs versus code: the
  bullet above still requires a checkbox for every standard `## Impact` declares, and the test is
  whether the task would exist if this branch were never merged.

Do not put `docs/concepts/` captures or glossary terms in `## Tasks` as durable content — those
route through `/quenching-knowledge learn` / `/quenching-knowledge define`; a task may *name* the capture
(`- [ ] 5.2 Capture the retry-budget gotcha via /quenching-knowledge learn`) but the knowledge itself lives in its
OKF home, never in the checklist.

## Execution metadata — optional, indented, additive

<!-- rules -->
A checkbox MAY carry indented metadata lines directly beneath it:

```markdown
- [ ] 3.2 Add rate limiting to the auth middleware
      files: src/middleware/auth.ts, src/config/limits.ts (new)
      pattern: src/middleware/cors.ts
      verify: pnpm test middleware/
      subject: plan/session-tokens: 3.2 Add rate limiting to the auth middleware
```

| Key | What it carries | Why execute wants it |
| --- | --- | --- |
| `files:` | comma-separated paths this task may touch; a comma inside parentheses never separates — a trailing `(…)` that is not a reserved annotation is refused with `sp-files-annotation`. Exactly two are reserved: `(new)` marks a path about to be created, `(operational)` declares a task that writes no repository file and is exclusive | bounds the work; **declaring it is what permits the task to be handed to an executor sub-agent**, and it is what makes a `[P]` marker checkable |
| `pattern:` | an existing file to imitate | the cheapest context an executor can be given — one path beats three paragraphs of description |
| `cwd:` | the directory, relative to the repo root, `verify:` runs from | **absent means exactly today's behaviour** — the session's or worktree's root. Write it only when the task's own `verify:` cannot resolve from there (a plugin-internal tool that only resolves from `quenching/`) |
| `verify:` | the command that proves the task done | run under the spec's `verification` policy; a task with no `verify:` falls back to `## Validation`. Runs from the task's declared `cwd:`, or the default when absent |
| `constraint:` | a bound on HOW this task may be done — a file it must not touch, an approach already ruled out | **nothing reads it yet.** Admitted by the grammar and handed through untouched; its only plausible consumer is an executor sub-agent briefing itself, and the decision to dispatch one belongs elsewhere. Write it where an executor would otherwise have to guess; it costs nothing when unread |
| `subject:` | the SUBJECT of the commit that implements this task | **written by the tool, never by hand** (`task --check --subject`), so code and spec stay linked without a trailer inside the commit message. Known before the commit exists, which is what lets the box travel inside it |

Write the first five where they earn their place — a task touching three known files with an
obvious test command deserves them; a one-line doc edit deserves none. Metadata that restates
the task text is noise.

**`files:` is the exception to "where it earns its place."** It is not a convenience on a delegable
task — it is the permission. Omit it and the task can never be handed to an executor sub-agent and
can never carry `[P]`, whichever way the work would actually have gone. Decide that deliberately or
declare the paths; do not leave it to whether the line felt worth typing.

**Every test whose diff the task produces belongs in its `files:`.** The verifier compares the
branch's changed paths with the union of the tasks' `files:`, so a test edited beside the code it
covers and left out of the declaration is an out-of-scope diff and a FAIL — the most common one, and
the reason the question *which tests does this touch?* belongs beside `[P]`, `files:` and `pattern:`
while the task is being written. Declare the test beside the implementation it proves.

**A task that writes no repository file declares `files: (operational)`.** The marker IS the whole
`files:` — never combined with a path, which `validate` refuses as `sp-files-operational` — and its
only proof is the `verify:`, whose absence is the `sp-operational-no-verify` warning. It is how a
redeploy, a `systemctl is-active` or the repository gate is declared without inventing a path, and
the verifier accepts such a task without a commit sha because it has no path to hang one on. The
readers that hand a task on carry `operational: true` beside `files: []` (`.agents/quenching/bin/cq specs next`,
`.agents/quenching/bin/cq specs show --task`), and that flag — never the empty list — is what excuses the sha: a task with
no `files:` line parses to the same `[]`, and only the flag tells the two apart.

**Declare every generated mirror the diff changes.** A task that edits a translated source also
rewrites what the generator emits from it. List each such derived output in the task's `files:`
beside the source, whichever of these the diff changes: the surface a repository deploys into
itself (`.agents/quenching/**`, `.agents/skills/**`, `.claude/skills/**`, `.opencode/agents/**`,
`.agents/.quenching-deploy.json`), any other generated bundle with its `.generated-files.json` and
`.generated-from.json`, `plugins/README.md`, and the goldens. The verifier executes nothing from the branch and discounts only the paths its audit
proved a byte-for-byte copy of their source in a manifest-declared module: an undeclared derived
output that is no such copy (a manifest, a hash index, a rewritten `plugin.json`, a golden) is an
out-of-scope diff. Run the generators in the task, read what changed, and declare exactly that set.

**Scope each `verify:` to what its own task could break** — not to what the repo can check. A gate
that re-runs a check whose inputs the section could not have touched is a `verify:` written too
wide, not a policy to be filtered at build time.

**A Python test `verify:` takes the gate's form** — `python3 -m unittest discover -s <tests dir> -p test_x.py`, run from the repo root. `unittest <path to .py>` and `unittest tests.test_x` fail with `ModuleNotFoundError` when the test imports a sibling helper (`_paths`), because only `discover -s` puts the tests directory at the head of `sys.path`.

And **a `verify:` that cannot fail proves nothing when it passes.** Run each one against the tree
*before* the fix and require it to exit non-zero; only then does its later exit 0 mean the task did
something. A check over prose reads the whole file and normalizes markup and whitespace before
matching.

**`[P]` marks a task parallel-eligible**, written right after the id:

```markdown
- [ ] 3.3 [P] Add the rate-limit config loader
      files: src/config/limits.ts
```

Set it **here, at definition time** — execution never infers it. It is honoured only when the
marked tasks' `files:` sets are provably disjoint, none waits `after:` another task of the same
group and none writes into `knowledge/`, which
`.agents/quenching/bin/cq specs parallel` checks mechanically. Serial is the default and needs no marker: without proven
disjunction, parallel execution trades wall-clock for merge conflicts and loses on both.

**A blocked task is a visible marker, not a hidden counter** —
`- [!] 2.3 <title> — blocked: <reason>`, written by `task --block --reason`, skipped by `next`.

<!-- rationale -->
Measured: on a 13-task run the same three selftests ran
at the close of section 1 and again at task 5.1, because tasks in two sections each declared all
three, while the spec itself stated no script changed in between. The body obeyed exactly what was
written. Fixing it at authoring needs no judgment while building and holds for every spec; the
alternative — a gate that skips a declared check because it judges the inputs unchanged — is a
correctness judgment made mid-build, which this front refuses everywhere else.

Over prose this is not hypothetical: a check has passed while its target was untouched
because the phrase wrapped across a line, and again because inline `**` sat between two words.

### The verification policy is the spec's, not the task's

<!-- rules -->
`per-task` / `per-section` (default) / `end-of-plan`, recorded in frontmatter. It answers *when* the
checks run; `verify:` answers *what* runs. Declaring it during definition is what keeps
`/quenching-specs execute` from having to guess, or from stopping mid-build to ask.

## `## Discoveries` and `## Outcome`

**`## Discoveries`** has no gate — it is appended to during execution, one line per finding, by
`.agents/quenching/bin/cq specs discover`. Captured **indiscriminately**: whether a discovery is worth acting on is a
later judgment, and asking the executor to make it mid-task is how a finding gets dropped for being
inconvenient. Each line is resolved **in place** by `/quenching-specs develop`'s discoveries stage, so
provenance is never lost:

```markdown
- the rate limiter double-counts retries -> promoted: fix-retry-accounting
- the config loader is slow on cold start -> dismissed: acceptable, runs once
```

**`## Outcome`** is the archive gate, written at close-out: what shipped, what was left out, what
the next reader needs to know — **including the merge strategy**, since a squash changes what a
future reader can resolve from a `subject:` field. For an abandoned spec, the reason it will not be
built is the whole content.
