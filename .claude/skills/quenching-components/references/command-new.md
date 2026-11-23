---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Bash(python3:*), Bash(py:*), Read, Grep, Glob, Write, Edit, AskUserQuestion
---

# /quenching-components command-new — mint ONE conformant command, registry included

**Input**: `$ARGUMENTS` (the command to create or edit — a name or a description of what it should do).

Creates or edits **ONE FILE** in the **target repo's own** automation surface — a
`.agents/skills/<name>/SKILL.md` carrying both the description that routes to it and the body that
runs, invocable as `/<name>`. `.agents/` is the only editable source: the host does not read
`.agents/skills/`, so `.agents/quenching/bin/cq components bridge` generates its `.claude/skills/<name>/` copy, never
edited by hand. `<name>` is **one segment** — the host does not discover a nested
`.claude/skills/<a>/<b>/SKILL.md` — and it IS the skill's identity. The axis, naming, and
registry format live in
[components-command-new/taxonomy.md](.agents/quenching/references/components-command-new/taxonomy.md); how the
body itself is written lives in [components-command-new/doctrine.md](.agents/quenching/references/components-command-new/doctrine.md);
what a command may strategically use — fork, pins, hooks, the invocation controls — and what
each lever costs lives in
[components-command-new/capabilities.md](.agents/quenching/references/components-command-new/capabilities.md).
Molds live at
`.agents/quenching/assets/templates/automation/`.

## Doctrine

- **The rule governs; the plan proposes.** In a target repo the taxonomy rule is
  `/docs/standards/automation/skills.md` — read it before classifying and follow it when
  present (a repo-specific delta there beats the plugin default). Absent + OKF bundle
  present → the plan offers creating it from the mold, born `authority: background`;
  never created without the OK.
- **No bundle, no tail — but the mint proceeds.** `/docs/index.md` without `okf_version`
  (or absent) means: write the command file only, skip registry/glossary, report that the bundle
  tail did not apply, and suggest `/quenching-knowledge align` **once**.
- **One plan, one OK, nothing before.** Classification, names, every file to be written,
  and the OKF tail appear in ONE plan; no file is created or modified before the single
  confirmation. A declined plan writes nothing.
- **MERGE, never clobber.** An edit preserves the skill's body and any hand-written
  content; only the gap being fixed changes. This skill never deletes a skill.
- **The registry zone is regenerated, never composed.** `.agents/quenching/bin/cq components registry reindex` owns the
  zone's format; the zone is never written between the markers by hand.

## Resolving the tool

Resolve `.agents/quenching/bin/cq components` per
[align/tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool §Write the resolved path literally on every invocation; branch on the
**exit code** (0 ok · 1 findings · 2 refusal) and the `--json`, never on prose. Findings carry
`sk-*` codes: an `error` is fixed, a `warn` is reported with its code.

## Workflow

### 1. Read the rule
Read `/docs/standards/automation/skills.md` and confirm the bundle
(`/docs/index.md` carries `okf_version`). Rule present → it governs. Rule absent, bundle
present → add "create the rule from `automation/skills-standard.md`" to the plan. No
bundle → note the tail as skipped and plan the `/quenching-knowledge align` suggestion.
**Done when:** the governing rule (or its planned creation, or the no-bundle note) is fixed.

### 2. Classify — category first, then the axis
Apply the classification test ([components-command-new/taxonomy.md](.agents/quenching/references/components-command-new/taxonomy.md) §The single axis) in order:

- **Category/subject.** `Glob` `.agents/skills/*/` for the `<categoria>-` prefixes already in use
  in this repo target, and ask which one this command belongs to (`git`, `deploy`, `tests`, ...) —
  offering the existing names, and free text for a new one. No clean, evident subject → there is
  no category; the rest of this step runs exactly as before this rule.
- **The axis.** Name the one folder the command acts on — one folder → domain-bound; "the repo"
  → generic; several unrelated folders → stop and ask the user which folder it serves (or
  whether it is generic) instead of forcing a value.
- **Nest vs replace, only with both a category and a bound folder.** Read the convention already
  established for this category in this repo (taxonomy.md §Reading the nest-vs-replace
  convention): `Glob` `.agents/skills/<categoria>-*/` and infer **nest** (a real-folder subpath
  already folded into the name) or **replace** (`<categoria>-<verb>` alone) from what is already there.
  Nothing there yet, or the two shapes are mixed → ask the human once — the first command minted
  under that answer becomes the convention for every later command of the same category in this
  repo, with nothing else recorded.

For an **edit**, re-derive category, axis and (if both apply) the nest-vs-replace read, and diff
them against the current path; a difference is a rename (step 3).
**Done when:** the category (or its absence), the axis
value and bound folder (if any), and the nest-vs-replace read (if it applies) are fixed.

### 3. Derive the name — which is the path
The segments of the classification join with `-` into one folder name under `.agents/skills/`.
No category: domain-bound → `<folder>-<subfolder>-<verb>`; generic → `<verb-object>`. Category, no
bound folder, or the convention reads **replace** → `<categoria>-<verb>`. Category, bound folder,
convention reads **nest** → `<categoria>-<subfolder>-<verb>`. The file is
`.agents/skills/<name>/SKILL.md`, its frontmatter `name:` equals `<name>` (OpenCode requires the
match), and the invocation is `/<name>`. There is no second name to derive. `Glob`
`.agents/skills/*/SKILL.md` for collisions — an existing path is a MERGE target (edit), never
silently overwritten. A target still on the legacy `.claude/commands/` layout is
`/quenching-components align`'s to migrate first; never mint into it. Shared procedure lives in
`.agents/references/<name>/` or beside `SKILL.md` in the skill's folder, cited by path.

**A name inside a family adds a flow, not a skill.** When the first kebab segment of the name is a
router of the target (`.agents/skills/<family>/SKILL.md`), the mint writes
`.agents/skills/<family>/references/<flow>.md` and adds one line to the router's flow table (and the
flow's scoped grant to the router's union); it never writes a loose `<family>-<flow>` skill and never
a shortcut. With no router yet, the mint writes the loose skill and `/quenching-components align`
asks the human about the family once three exist.

**Rename sweep (an edit whose derived path differs from the old path).** The path is the name:
`Grep` the old `/<name>` and the old path in `.agents/skills/**`, `.agents/references/**`,
`AGENTS.md`, `docs/`, `.claude/settings*.json` and `.agents/evals/`. Each hit is a plan entry with a
disposition (rewrite, keep, ask); the mirrored `evals/<old name>/` moves in the same plan; the old
folder is reported as residue, never deleted, and the bridge reports its copy as orphaned.
**Done when:** the path is fixed, collision-free or resolved as an edit, and a rename carries every
hit with a disposition (or states the sweep found none).

### 4. Choose the execution profile
Walk [components-command-new/capabilities.md](.agents/quenching/references/components-command-new/capabilities.md):
the default profile is **all levers off**, and each departure needs a stated buy — `context:
fork` only for a self-contained, noisy, summary-out run with **no mid-flow gate**; an
`effort`/`model` pin only for genuinely mechanical work; `paths` to bind a domain-bound command to
its folder; `disable-model-invocation` only for a human-must-choose command; frontmatter `hooks:`
only for a check tied to this command's own workflow. For an **edit**, re-derive the profile and flag any
lever whose original buy no longer holds. **Done when:** each non-default lever is listed
with its one-line reason — or the profile is stated as default.

### 5. Draft under the doctrine
Fill `automation/command.md` per
[components-command-new/doctrine.md](.agents/quenching/references/components-command-new/doctrine.md): description front-loads the leading
concept, one verbatim trigger per branch in the second sentence, `Not for:` boundary;
steps end in checkable **Done when** criteria; every line passes the no-op test;
prescriptions positive; body well under 500 lines; the description within the 1,536-char cap.
The description is the **only** always-on text there is — its triggers and its boundary are what
route a spoken request here. For an edit, apply the doctrine to the diff — cure the named failure mode
(sediment, sprawl, negation, …), keep the rest. List the old description's quoted triggers first;
each survives the draft, or is reported with the `/quenching-components command-eval <command>`
that decides its retirement — a quoted trigger is never cut for length.
**Done when:** the draft passes the doctrine's failure-mode table read top to bottom, and every
quoted trigger is present or reported.

### 6. Present ONE plan → gate on the OK
Show: axis value + bound folder, the command path, **the execution profile** (each
non-default lever with its reason, or "default"), every file (the skill, its generated
`.claude/skills/<name>/` bridge copy, rule if planned, registry create-or-regenerate), and the
tail steps. For an edit, also: the rename hits and, when `evals.json` exists
in the mirrored tree (`.agents/evals/<name>/`; `.agents/quenching/assets/evals/<path>/` here),
its case count and that the rerun is yours to run, not this command's. Wait for the single confirmation.
**Done when:** the user has answered; declined → report "nothing written" and stop.

### 7. Write, then bridge
Write `.agents/skills/<name>/SKILL.md` from `automation/command.md` — frontmatter and body in the
one file; write the rule if planned. Then generate the Claude copy from the source:
```bash
.agents/quenching/bin/cq components bridge --target . --write --json
```
Exit 2 `ct-bridge-unowned` names a `.claude/skills/<name>/` the bridge did not generate: report it
and stop the bridge there, never overwrite it. **Done when:** every planned file exists with its
planned content and `bridge --write` exited 0.

### 8. OKF tail (bundle present)
Create `/docs/project/automation.md` from `automation/registry.md` first if it is
absent (it was in the plan). Then regenerate the zone:
```bash
.agents/quenching/bin/cq components registry reindex --json
```
Update `project/`'s `index.md` per the procedure in
[knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md). If the
command coined a new repo-specific term, **offer** ONE `glossary.md` entry — the user
decides. **Done when:** `registry reindex` exits 0 and the index is honest.

### 9. Self-check
Ask the tool, do not read for it:
```bash
.agents/quenching/bin/cq components lint <skill-file> --json     # this skill's conformance
.agents/quenching/bin/cq components doctor --json                # the surface invariant it just changed
.agents/quenching/bin/cq components bridge --target . --check --json  # exit 0 — the Claude copy matches the source
.agents/quenching/bin/cq components registry reindex --json      # `changed: false` — nothing wrote inside the markers after the description pass
```
Fix every `error`; report every `warn` with its `sk-*` code. What no parser can decide — the no-op
test, sediment, sprawl, positive prescription — is read by eye against
[components-command-new/doctrine.md](.agents/quenching/references/components-command-new/doctrine.md). Any OKF doc touched passes
[knowledge-align/conformance.md](.agents/quenching/references/knowledge-align/conformance.md).
An edit's `evals.json` (step 6's path), when present, is reported "present, not rerun", with the next
human action `/quenching-components command-eval <path>` — this command holds no `Task` and cannot rerun it.
Assertions on changed branches are reported stale, never rewritten.
So this step, not a later one, owns the functional proof: **where the repo ships a harness that
spawns a fresh session and asserts on captured tool calls, run it now** — scoped to what this mint
changed. In this plugin that is
`assets/checks/functional-checks.sh` (default subset for a body; `/quenching-components command-eval` for a description).
**Where the repo ships none, say plainly that the command is written but unproven until a fresh
session** — never report a linter's exit 0 as evidence that the surface loaded.

Report what was written, the invocation (`/<name>`), the functional result or its absence, and
any residue. **Done when:** `lint` and `doctor` exit 0, `bridge --check` exits 0, `registry reindex` reports `changed: false`,
the surface was either functionally proved or reported unproven, an edit's `evals.json` was reported
with its eval command, and every remaining `warn` is named in the report with its code.

## Invariants

- Never write before the single OK; a declined plan leaves the repo untouched.
- Never delete a command, and never clobber an existing body — MERGE.
- Never path a generic command under a folder; never mint a directory-scoped surface
  (accepted variation when found, never generated — taxonomy.md §Placement).
- Never write under `.claude/skills/` by hand — it is the bridge's generated copy; edit the
  `.agents/skills/<name>/` source and rerun `bridge --write`.
- Never write a nested skill folder: `<name>` is one segment.
- Never hand this command file `context: fork` — the plan gate is mid-flow.
