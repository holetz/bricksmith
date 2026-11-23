---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Bash(python3:*), Bash(py:*), Read, Grep, Glob, Write, Edit, AskUserQuestion
---

# /quenching-components agent-new — mint ONE subagent definition

**Input**: `$ARGUMENTS` (the agent to create or edit — a name or a description of the work
to delegate).

Creates or edits **ONE FILE** in the target repo's agent surface — `.claude/agents/<name>.md`,
a definition whose description routes delegation to it and whose body is its system prompt.
The delegation economics and the definition contract live in
[components-command-new/capabilities.md](.agents/quenching/references/components-command-new/capabilities.md)
§Subagents. The mold is
`.agents/quenching/assets/templates/automation/agent.md`.

**Why `.claude/agents/` and not `.agents/`.** Skills, references and instructions live in the
generic `.agents/` layer, but a subagent has no generic home any harness reads: verified on Claude
Code 2.1.312, a definition in `.agents/agents/` does not appear as a subagent type, and OpenCode
reads `.opencode/agent`, not `.agents/agents/`. So the definition is Claude's own interface and is
written where Claude reads it, with no bridge copy.

## Doctrine

- **The rule governs; the plan proposes.** In a target repo the rule is
  `/docs/standards/automation/agents.md` — read it before drafting and follow it when present.
  Absent + OKF bundle present → the plan offers creating it from
  `automation/agents-standard.md`, born `authority: background`; never created without the OK.
- **No bundle, no tail — but the mint proceeds.** `/docs/index.md` without `okf_version` (or
  absent) means: write the definition only, skip the OKF tail silently, suggest `/quenching-knowledge align`
  **once**.
- **One plan, one OK, nothing before.** The delegation buy, the name, the profile, every
  file, and the tail appear in ONE plan; a declined plan writes nothing.
- **MERGE, never clobber.** An edit preserves the body and any hand-written content; only the
  gap being fixed changes. This skill never deletes an agent.
- **An agent's description is always-on context.** The same two caps as a command's, counted
  on the parsed value.

Resolve `cq` per
[align/tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool; branch on the **exit code** (0 ok · 1 findings · 2 refusal), never on prose.

## Workflow

### 1. Read the rule
Read `/docs/standards/automation/agents.md` and confirm the bundle (`/docs/index.md` carries
`okf_version`). Rule present → it governs. Rule absent, bundle present → add "create the rule
from `automation/agents-standard.md`" to the plan. No bundle → note the tail as skipped and
plan the `/quenching-knowledge align` suggestion. **Done when:** the governing rule (or its planned
creation, or the no-bundle note) is fixed.

### 2. Apply the delegation test
Per [capabilities.md](.agents/quenching/references/components-command-new/capabilities.md)
§Subagents: the work qualifies when its returned summary is much smaller than the work
itself, when slices run in parallel, or when its tool set must be narrower than the
conversation's. Work that fails the test is a command or an inline step — say so and route
to `/quenching-components command-new`. **Done when:** the delegation
buy is stated in one line, or the request is rerouted.

### 3. Derive the name, check collisions
Kebab-case identity naming what the agent *is* (`migration-verifier`), never `helper`.
`Glob` `.claude/agents/*.md`: an existing path is a MERGE target (edit), never silently
overwritten. **Done when:** the path is fixed, collision-free or resolved as an edit.

### 4. Draft from the mold
Fill `automation/agent.md`: a description carrying the work **and the timing** ("after X is
modified", "when asked to audit Y") within the caps; `tools` at the narrowest set (spawning is controlled by `Agent` in `tools`);
`model`/`effort` per the pinning rules (mechanical extraction may run cheap — judgment never
downgrades; a pin here is cache-safe); `skills`, `memory` only with a
stated reason. The body is a **system prompt in second person**. Inspection work takes the
verifier shape: numbered check areas, a "not checked here" list, if-present guards, a fixed
report format — and it never edits. **Done when:** the draft passes
[capabilities.md](.agents/quenching/references/components-command-new/capabilities.md)
§Subagents read top to bottom.

### 5. Present ONE plan → gate on the OK
Show: the delegation buy, the name, the profile fields with reasons, every file (the
definition, the rule if planned, and the tail steps. Wait for the single
confirmation. **Done when:** the user has answered; declined → report "nothing written" and
stop.

### 6. Write
Write the definition (and the rule if planned). **Done when:** every planned file exists
with its planned content.

### 7. OKF tail (bundle present)
The registry's GENERATED zone lists commands only. Offer, as follow-up commands rather than writes
in this run, one curated registry-prose line for the agent surface and `/quenching-knowledge define`
for a coined repo-specific term; the user decides both. **Done when:** each offer is answered.

### 8. Self-check
```bash
.agents/quenching/bin/cq components doctor --json
.agents/quenching/bin/cq components lint --json
```
`lint` takes no argument here: `lint <file>` never reaches the agents. Keep every finding whose
`path` is this file, from either payload, whatever its code (`sk-cq-flag` included). Then read the definition against the contract by eye: tools scoped, voice second-person, a
verifier states it never edits. Report what was written and the always-on cost the
description adds. **Done when:** neither `doctor` nor `lint` reports any finding for this file and
the report states the description's parsed character count.

## Invariants

- Never write before the single OK; a declined plan leaves the repo untouched.
- Never delete an agent, and never clobber an existing body — MERGE.
- Never leave `tools` unstated by accident — omitting it grants everything, and the plan
  says so when that is the choice.
- Never mint a verifier that edits; findings re-enter through the command that owns
  authoring.
- Never hand this command file `context: fork` — the plan gate is mid-flow.
