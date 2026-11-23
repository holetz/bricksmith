---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Bash(python3:*), Bash(py:*), Bash(git grep:*), Bash(grep:*), Bash(mkdir:*), Bash(mv:*), Bash(rm:*), Read, Grep, Glob, Write, Edit, Task, AskUserQuestion
---

# /quenching-components align — force the automation surface onto the taxonomy

**Input**: `$ARGUMENTS` (an optional scope; omit to sweep the whole automation surface).

The sweep counterpart of `/quenching-components command-new`: where the mint keeps each **new** command
conformant, this one converges everything that **already exists** — including a surface still
built as `skills/<name>/SKILL.md` + a mirrored wrapper, which it **collapses to one file per
entry point** (§6 below) — then **reads every surviving body against the writing doctrine** (§7)
and **reviews every description against it in one surface-wide pass** (§8).

The axis, naming, placement, and registry format live in
[components-command-new/taxonomy.md](.agents/quenching/references/components-command-new/taxonomy.md);
the writing doctrine judged against is
[components-command-new/doctrine.md](.agents/quenching/references/components-command-new/doctrine.md);
the capability levers the wider inventory reads against are
[components-command-new/capabilities.md](.agents/quenching/references/components-command-new/capabilities.md).
Molds live at
`.agents/quenching/assets/templates/automation/`.

## Doctrine

Read [align/sweep-doctrine.md](.agents/quenching/references/align/sweep-doctrine.md) as
this skill's doctrine. What follows is specific to the automation surface:

- **The legacy `openspec-*` surface is not this sweep's.** `.claude/skills/openspec-*/` and
  `.claude/commands/opsx/` are left by a prior `openspec init`; clearing them is a human's call
  ([specs-align/conformance.md](.agents/quenching/references/specs-align/conformance.md)
  §Finding policy). Inventory them only to **note** them; never classify them onto the axis,
  rename them, or remove them here.
- **The wider `.claude/` is inventoried, never migrated.** `.claude/agents/*.md` and the
  hooks wired in `settings.json` and in command frontmatter are **report-only** surfaces:
  the tool names their findings (`sk-agent-*`, `sk-hook-*`) and each is routed to the mint
  that owns it (`/quenching-components agent-new`, `/quenching-components hook-new`).
- **`.agents/` is the source; `.claude/skills/` is the generated bridge.** A target's skills live
  in `.agents/skills/<name>/SKILL.md` (one segment, `/<name>`), its references in
  `.agents/references/**`, its instructions in `AGENTS.md`. `.agents/quenching/bin/cq components migrate --target .`
  plans the move off the legacy layout (`.claude/commands/**`, `.claude/references/**`, the old
  Codex translation under `.agents/`); `.agents/quenching/bin/cq components bridge --target . --check` names the drift
  of the generated `.claude/skills/<name>/` copies. **Conformant means both**: an empty migrate
  plan (`conformant: true`) and `bridge --check` exit 0.
- **A family is a router, with no shortcuts.** Three or more skills of the target sharing the first
  kebab segment are one vertical: `.agents/skills/<family>/SKILL.md` with a flow table, each flow
  in `references/<flow>.md` inside the skill, the grant the union of the flows'. The migrate's
  `routers` block lists the candidates; `migrate --write --router <family>` applies the ones the
  human names in §4. What the deploy installed (`quenching-*`, listed in
  `.agents/.quenching-deploy.json`) is never grouped, moved or rewritten.
- **The `.claude → .agents` translation is the legacy layout `migrate` retires.** No verb writes it
  any more; never hand-copy it back over the `.agents/` source.

## Resolving the tool

Resolve `.agents/quenching/bin/cq components` per
[align/tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool §Write the resolved path literally on every invocation; branch on the
**exit code** (0 ok · 1 findings · 2 refusal) and the `--json`, never on prose.

**Every shell grant is scoped**, per `/docs/standards/automation/skills.md`
§`allowed-tools` is always scoped: `python3`/`py` for the tool, `git grep` and `grep` for the
blast-radius sweep (§3), `mkdir`/`mv` for the renames, `rm` for a
confirmed legacy tool copy.

**What the tool decides, and what it does not.** `doctor` and `lint` decide everything mechanical
(descriptions, `/` paths, kebab-case segments, caps, trigger position, `Not for:`, body length and
size, step criteria, unscoped `Bash`). Neither decides the **axis**: classification stays a read.

## Workflow (probe → ONE OK → migrate → audit → re-probe)

### 1. Probe — the checks that decide whether anything else runs
Before any inventory, ask the tool whether there is work at all:
```bash
.agents/quenching/bin/cq components migrate --target . --json
.agents/quenching/bin/cq components bridge --target . --check --json
.agents/quenching/bin/cq components doctor --json
.agents/quenching/bin/cq components lint --json
```
plus one `Glob` for the legacy pairs the tool cannot see (below). Branch as
[sweep-doctrine](.agents/quenching/references/align/sweep-doctrine.md) §1. Probe before the inventory prescribes:

| Probe result | What happens |
| --- | --- |
| all four exit 0 with no findings (`migrate` `conformant: true` and `routers` empty, the bridge in step) and no legacy pair | **STOP.** Report "`.agents/` conformant, N skills, bridge in step, nothing to align" and end. No inventory, no plan, no confirmation. |
| all four exit 0, `routers` empty, and the only findings are the report-only wider surface (`sk-agent-*`, `sk-hook-*`) | STOP the same way, then list them with the mint that closes each. Nothing here is this sweep's to write. |
| the only findings are description codes (`sk-metadata-cap`, `sk-description-portable`, `sk-trigger-position`, `sk-no-boundary`) | **Skip to §8.** There is nothing to migrate, and an inventory, a plan and a confirmation for zero renames is ceremony — §8 carries its own gate. |
| `migrate` exit 1 (a non-empty plan) or `bridge --check` exit 1 (drift), and nothing else | **Skip to §4** with only that block: the `operations`, `conflicts` and `renames` of the plan and the bridge's `changes`. |
| everything exits 0 but `migrate` `routers` lists a family | **Skip to §4** with only the family decisions: `routers` never moves `conformant`, so the layout is green and the grouping is still the human's to answer. |
| `migrate` or `bridge` exit 2 | Report the refusal with its code (`ct-bridge-unowned`: a `.claude/skills/<name>/` the bridge did not generate) and stop; it is a human's call. |
| anything else exits 1 or 2, or a legacy pair exists | Continue to step 2. |

An **empty** surface (no commands, no skills) also stops. Note whether an OKF bundle exists
(`/docs/index.md` with `okf_version`) and say so once — without one the rule and registry stay out
of scope.

**Done when:** the four payloads and the glob are in hand, and the run has either stopped,
committed to a full sweep, or entered §8 directly.

### 2. Inventory the surface (read-only)
`Glob` for legacy pairs:
`.claude/skills/*/SKILL.md` and directory-scoped `**/.claude/skills/*/SKILL.md`, **minus every
folder `.claude/skills/.generated-files.json` lists** — those are the bridge's copies, never a
pair. **Every remaining one is a pair awaiting collapse** — pair each with the wrapper whose body invokes it (the
`quenching:<name>` or bare `<name>` reference), and record a skill with no wrapper, or a
wrapper naming no skill, as an item needing a human decision rather than a mechanical merge.
Set aside every legacy `openspec-*` skill and `opsx/` wrapper (Doctrine's legacy `openspec-*`
surface); list them as *out of scope, owned by no command — a human's call* and drop them from the
working set, including from the tool's findings.

For each remaining item, make the **axis classification** per the
test ([taxonomy](.agents/quenching/references/components-command-new/taxonomy.md) §The single axis — several unrelated folders
→ unroutable). Read `/docs/standards/automation/skills.md` if present — it governs; note whether
the rule and the registry (`/docs/project/automation.md`) exist.

**Where an item classifies with an evident category, also read the nest-vs-replace convention**
already established for that category in this repo (taxonomy.md §Reading the nest-vs-replace
convention): `Glob` `.agents/skills/<categoria>-*/` (legacy: `.claude/commands/<categoria>/**`) and infer it from what already sits there.
Derive the canonical path the classification produces and diff it against the item's current
path — a mismatch is a relocation candidate, fed into §4's existing renames row like any other,
never a new plan section or a new confirmation of its own.

**A category whose convention is not readable yields no candidate** (nothing under
`.agents/skills/<categoria>-*/` yet, or the two shapes mixed) — record the classification and move on.
**Done when:** the inventory table (item · classification · canonical path if it diverges ·
`sk-*` gap) covers every item in the working set, and no file changed.

### 3. Sweep the blast radius
Run the shared procedure in
[align/sweep-doctrine.md](.agents/quenching/references/align/sweep-doctrine.md)
§4. The blast-radius sweep — two repo scans for the whole set, never two per rename — over every name slated for
rename or removal: skill names **and** command paths. **A collapse retires a skill name**, so
every site naming it (a conductor invoking it via the Skill tool, a runbook, a registry row) is a
hit the same way a rename is. **This front's delta:** a hit that is only
documentation (`/foo:bar` in a README, runbook or CI comment) is workspace-internal; a hit in a
script that *invokes* the command is code-coupled.
**Done when:** each planned rename is marked coupled or free, with its hits.

### 4. Present ONE consolidated plan → gate
One table: **the migration block first** — `migrate`'s operations (move · remove · pointer),
its renames (`/<a>:<b>` → `/<a>-<b>`) and conflicts, and the bridge changes — then **pairs to collapse** (skill + wrapper → the one command file that survives, with
the skill name being retired), renames (old → canonical new, coupled ones marked), commands to
create or rewrite (and wrongly nested generic commands to flatten), rule + registry creations
from the molds when missing (rule born `authority: background`), unroutables kept-and-reported
with reasons, obsolete-suspect flags (no deletion proposed without the human's word), and —
labelled **"reported, not applied"** — the wider-surface findings (`sk-agent-*`, `sk-hook-*`),
each with the mint that closes it.
**One decision per family** from the migrate's `routers` block (the same candidates `families` with
`candidate: true` names in `status --json`): the family, its `kind` (`new` or
`existing`), the members and the flows, with **A** (group under `.agents/skills/<family>/`,
recommended) and **B** (keep the skills loose). A declined family is not touched and is offered
again on the next pass. A family with `conflicts` (a `quenching-*` name, a taken destination, a
shortcut with no reference) is listed and cannot be answered A until the human settles it.
A migrate **conflict** (two editable copies that disagree) is listed and never confirmed: `--write`
refuses while one stands, so it is a human's to settle first.
Wait for the single confirmation; code-coupled items each await their own.
**Done when:** the user has answered; declined → nothing written, run ends.

### 5. Apply
Execute the confirmed plan: **the migration first** — `.agents/quenching/bin/cq components migrate --target . --write
--json` plus one `--router <family>` per family answered A, which leaves every `openspec-*`
skill and `opsx/` command where it stands — then
**collapse each pair per §6**, rename command paths, create/rewrite
commands from `automation/command.md`, fix frontmatter gaps per the doctrine (description within
the cap, triggers second sentence — bodies untouched), write rule and registry from their molds
when planned, update each code-coupled reference site alongside its individually confirmed
rename.

**Done when:** every confirmed row is applied.

### 6. Collapse each confirmed pair — the merge, key by key
For every `skills/<name>/SKILL.md` paired with a wrapper in §2, the surviving file is the
**wrapper's path**, so nothing a human types today changes. Frontmatter is a merge with one rule
per key, not a judgement call:

| Key | Source |
| --- | --- |
| `description` | the **wrapper's** |
| `argument-hint` | the wrapper's |
| `allowed-tools` | the **skill's** |
| `effort` | the skill's |
| `name` | **dropped** |
| `when_to_use` | **dropped** |
| `user-invocable` | **dropped** |

The body is the **skill's, moved verbatim** — retitle its `# <skill-name> — …` heading to
`# /<command:path> — …` and fold the wrapper's `$ARGUMENTS` sentence in as the input contract.
Keep the body otherwise unchanged.

Then **re-home what sat beside the skill.** A `references/` folder cannot follow the body into
`commands/`, where it would register as a phantom command — move it outside (in a target repo,
`.agents/references/<name>/`) and rewrite every citation to that absolute path. Same for `evals/`.
Delete `skills/<name>/` only once its body is in place and its neighbours are re-homed.

**A pair that is not a clean pair is reported, never guessed:** a skill with no wrapper needs a
path chosen (a human decision), and a wrapper naming no skill has no body to take.
Then regenerate the bridge from the edited bodies: `.agents/quenching/bin/cq components bridge --target . --write --json`.
**Done when:** every confirmed pair is one file, `skills/` holds nothing that was migrated, the
bridge was written after the last body edit, and every unclean pair is listed with what it needs.

### 7. Audit every body against the doctrine — read-only, always
Read every body at the path it will keep. This stage **writes nothing** and needs no authorization.

Keep these evidence types separate:

- **What the tool decided.** `lint`'s per-body codes carry the mechanically decidable half —
  `sk-body-length`, `sk-step-criterion`, `sk-trigger-position`, `sk-no-boundary`,
  `sk-description-portable`, `sk-metadata-cap`.
- **What only a read can judge.** For each remaining body: the no-op test, sediment, sprawl,
  positive prescription, and whether shared procedure is **cited rather than restated**
  ([doctrine](.agents/quenching/references/components-command-new/doctrine.md)).

**Collection may be delegated; judgment may not.** On a surface too large to read whole, dispatch
read-only `Task` collectors — one per slice — that report *what each body contains* (levers in
its frontmatter, what it cites, where its numbered steps end) and nothing else; every doctrine
verdict stays with the orchestrator.

**A local family** is no finding of §7: `families` from `.agents/quenching/bin/cq components status --json`
and `routers` from `migrate --json` are decided in §4 and applied in §5, per
`/docs/standards/automation/skills.md` §A target extends a vertical with its own router.
A family the human answered B stays as loose skills and is not reported as a defect.

Report each finding as `command · violated rule · one-line evidence · the /quenching-components command-new invocation
that opens the edit`, labelled **"reported, not applied"**. Skip every legacy `openspec-*` body —
that surface is out of scope here as everywhere. Never rewrite a body to close a finding.
**Done when:** every surviving body carries a verdict — a finding with its fix invocation, or
clean — and nothing was written.

### 8. Review every description — ONE table, its own OK
Review every description across the whole surface in **one pass**.

Read **frontmatter only**. Judge descriptions against the other descriptions, not bodies. §1's
`doctor` payload counts the surface, and its `lint` payload names the description codes:
`sk-metadata-cap`, `sk-description-portable`, `sk-trigger-position`, `sk-no-boundary`.

**Which class a description is in decides which verdicts apply.** A router's description is routed
like any other; the flows inside its `references/` carry no description of their own to review.
A command or skill with `disable-model-invocation: true` has left the routing surface: `lint` reports neither routing code
against it, so there is nothing to buy by shortening it and no trigger to demand. Its description
keeps all three slots at full length for the human picking it from the `/` menu
(`/docs/standards/automation/skills.md` §The admission criterion). The class is read from the
frontmatter, never guessed.

Judge each description against the three slots and the competitor test in
[components-command-new/doctrine.md](.agents/quenching/references/components-command-new/doctrine.md)
§The three slots. What this stage does with each verdict:

| Verdict | Action |
| --- | --- |
| prose about **how** the command works — step order, tool names, call counts, the reasoning behind a rule | **cut** on a routed description; it is body. On a typed-only one, cut it only where it confuses the human reader — length is not the reason, since the command is charged 0 |
| the leading concept is a `/`-menu label, or absent | **rewrite** the first sentence in the command's own vocabulary |
| no trigger, or a trigger after the second sentence (`sk-trigger-position`) | **add** one verbatim phrase per branch that has none; move them into the second sentence. **Routed only** — on a typed-only command an absent trigger is not a defect, and adding one buys nothing that routes |
| `Not for:` naming a command that fails the competitor test | **cut**, and record the waiver with the competitor set checked |
| a real competitor with no `Not for:` (`sk-no-boundary`) | **add** the one clause that routes: `Not for: <job> → <command>` |
| a quoted trigger that looks like sediment | **report**, never cut — `/quenching-components command-eval <command>` decides it on a measured miss |
| over a cap (`sk-metadata-cap`, `sk-description-portable`) after all of the above | **report** the residue with its code; a cap is not closed by deleting a trigger |

The competitor test runs **against the surface in hand**: §1's `lint` paths and the descriptions
just read.

Present ONE table — command · class · current chars → proposed chars · what changed · the slot that
earned it — with the description-code count before → after from §1's `lint` payload, the
mechanically decided half of what this stage changes. **Gate on its own
OK**, separately from §4's: that plan was confirmed before any description had been read, and an
edit nobody saw is not an edit anybody authorized. Declined → nothing is written here and the run
continues to §9 reporting the review as proposed-and-declined.
**Done when:** every description carries a verdict, each applied edit is in the confirmed table,
every waived boundary names the competitor set that was checked, and no trigger phrase was
deleted.

### 9. Verify, decide, report
Regenerate the zone, then let the tool judge the surface the migration produced:
```bash
.agents/quenching/bin/cq components registry reindex --json   # the zone, from the post-migration surface and §8's descriptions
.agents/quenching/bin/cq components bridge --target . --write --json # the bridge, from §8's descriptions
.agents/quenching/bin/cq components migrate --target . --json        # `conformant: true` — an empty plan
.agents/quenching/bin/cq components bridge --target . --check --json # exit 0 — the bridge matches .agents/skills/
.agents/quenching/bin/cq components doctor --json             # the surface invariant the migration just changed
.agents/quenching/bin/cq components lint --json               # the gaps the migration was supposed to close — §8's codes
.agents/quenching/bin/cq components registry reindex --json   # `changed: false` — the zone now matches disk
```
Every renamed reference site greps clean, and no citation still points into a deleted
`skills/` tree. `reindex` runs **after** §8: the zone's `Typical trigger` column is each
description's **first quoted phrase**, which §8 may move.

Then re-run §1's probe and decide by the four outcomes in
[convergence.md](.agents/quenching/references/align/convergence.md)
§The convergence contract: **progress** → another pass from §2 under the same OK, narrating its
plan; **converged** → report; **residue** → stop and report; **pass cap reached** → stop and report
what remains. §7's findings are **not** progress: they are read-only and carry forward unchanged.
**Neither is §8's edit**: a second pass runs §8 only to confirm it has nothing left to say, never
to re-open a table the human already answered.

In an OKF repo, confirm the registry is indexed, per
[knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md). The migration
counts go in the report below, not into the bundle.
Report: passes run; collapsed / renamed / created / flattened / rule+registry created / unroutable /
flagged; every surviving `sk-*` finding, every migrate conflict and every bridge change still standing, by code; §7's doctrine findings, listed
apart, each with its `/quenching-components command-new`; and §8's line — descriptions reviewed, edited,
declined; the description-code count before → after from `lint`; every waived boundary with the
competitor set checked (and its accepted `sk-no-boundary`); and every trigger handed to
`/quenching-components command-eval`. **Done when:** `migrate` reports an empty plan, `bridge --check` exits 0, `doctor` and `lint`
exit 0 or each surviving finding is named with its code, the second
`registry reindex` reports `changed: false`, and the counts, the doctrine findings and §8's line
are reported.

## Invariants

- Never modify anything before the plan's OK; a code-coupled rename never rides the batch. A
  cycle-authorized run replaces only the batch gate with narration — never a code-coupled
  item's own OK.
- Never rename, reclassify, or remove a legacy `openspec-*` skill or an `opsx/` wrapper — no
  command of this plugin owns that surface; note it and move on.
- Never alter a body's prose — only its title line, its input contract, its citation paths,
  its placement, and its frontmatter conformance. A collapse MOVES a body; it never edits it, and
  §7 only reads it.
- Never inventory before the probe, and never treat §7's read-only findings as progress that
  justifies another pass.
- Never delete a quoted trigger phrase — not for length, not for looking redundant. A trigger
  retires on a measured miss, which is `/quenching-components command-eval`'s; here it is reported
  with that invocation.
- Never write a `Not for:` clause naming a command that fails the competitor test, and never cut
  one without stating the set that was checked.
- Never apply §8's edits under §4's OK.
- Never delete a command without the human stating it is obsolete; never force an
  unroutable item onto the axis. Deleting a `skills/<name>/` folder whose body has just been
  moved into its command file is not a deletion in this sense, and happens only after the move.
- Never write anything but an entry point under `commands/`: a `references/` or `evals/`
  folder moved there would register every file in it as a phantom command.
- Never leave the GENERATED zone stale, and never write inside its markers by hand — the run
  ends on a `registry reindex` that reports `changed: false`.
- Never edit a `.claude/skills/<name>/` the bridge generated; edit `.agents/skills/<name>/` and
  rerun `bridge --write`.
- Never hand this command file `context: fork` — both gates are mid-flow.
