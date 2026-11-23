---
description: >-
  <Leading concept first: what this command does and to what, in one sentence, before any
  qualifier.> Use when the user asks to "<trigger 1>", "<trigger 2>", or "<one verbatim
  trigger per distinct branch — a branch without a trigger never fires>". <One sentence on
  what exists when it finishes: files written, zones regenerated, checks run.> Not for:
  <adjacent job> → <owning command>.
argument-hint: [<the input this command accepts — e.g. subject, target file, options>]
allowed-tools: <only the tools the steps actually use, each SCOPED — e.g. Read, Grep, Glob,
  Write, Edit, Bash(python3:*), Bash(git status:*). A bare `Bash` grants the whole shell for
  the turn and is reported as `sk-unscoped-bash`; it is legitimate only for a command running
  the TARGET repo's own toolchain, whose body says so and says why — on a line of its own
  opening `**Why `Bash` is unrestricted here.**`, which is what `lint` observes.>
# effort: <low | medium — omit to inherit the session's. Set it only when the work is
#   genuinely cheaper or genuinely harder than the default. An inline pin invalidates the
#   session's prompt cache — price the switch against what it saves.>
# disable-model-invocation: <true blocks programmatic invocation, so a conductor can no
#   longer reach this command by name and a spoken trigger can no longer route to it — AND
#   removes the description from always-on context entirely. Use it only
#   for a command whose cost or blast radius means a human must choose it.>
# context: <fork runs the body in a forked context — self-contained, noisy, summary-out
#   work ONLY; never beside a mid-flow gate or an AskUserQuestion grant (`sk-fork-gate`).
#   `agent:` picks the runner (Explore for read-only); `background: false` waits for the
#   result.>
# paths: <glob patterns binding AUTONOMOUS firing to matching files — the natural
#   reinforcement for a domain-bound command; typed invocation is unaffected.>
# hooks: <a check tied to THIS command's own workflow, scoped to its lifecycle — the
#   narrowest rung of the hook ladder. Wired by /quenching-components hook-new from the hook mold.>
---

# /<front>:<verb> — <one-line role>

**Input**: `$ARGUMENTS` (<what the caller passes; say "none" if the command takes nothing>).

<Context paragraph: where the command acts (the bound folder for a domain-bound command),
what it assumes exists, and which bundled references or sibling docs it cites instead of
restating.>

## Workflow

### 1. <Step name>
<What to do. **Done when:** <a checkable, observable condition — a file exists, a diff is
empty, a command exits 0>.>

### 2. <Step name>
<...each step ends with its own **Done when:** criterion...>

### N. Self-check
<Diff the result against the description's promise: everything the description says will
exist, exists; every step's criterion held. Report what was written.>

<!-- MOLD (quenching · automation command) → becomes
     `.agents/skills/<folder-path>/<verb>.md` in a target repo, invocable as
     `/<folder>:<subfolder>:<verb>` — native `:` separator, one per path segment — and minted
     by `/quenching-components command-new` under the taxonomy rule (`/docs/standards/automation/skills.md`) and the
     writing doctrine (predictability as the root virtue).

     ONE FILE PER ENTRY POINT. the host merged custom commands into skills, so this file
     carries BOTH the description that routes to it and the body that runs. There is no
     `SKILL.md` half and no wrapper to mirror: the command's PATH is its whole identity, so
     nothing derives a second name to keep in step with it.

     THE PATH IS THE ONLY THING UNDER `commands/`. That tree is the only one the host
     registers, so anything that is not an entry point — shared procedure, references,
     fixtures, eval cases — lives OUTSIDE it. A `references/` folder beside this file would
     register every reference as a phantom command like the four-segment
     `knowledge:align:references:conformance`.
     Cite shared procedure by absolute path instead; in a plugin that is
     `.agents/quenching/references/<name>/<file>.md`, which substitutes inside a
     command body.

     VERIFY IT, don't eyeball it: `.agents/quenching/bin/cq components lint <this-file> --json` decides every mechanical
     rule and names each gap by a stable `sk-*` code; `.agents/quenching/bin/cq components doctor --json` decides the
     surface-wide invariant (every command has a non-empty description, no two resolve to the
     same `/` path, every segment kebab-case). Thresholds live in
     `/docs/standards/automation/skills.md` — this mold never restates a number.

     What lint checks (fix an `error`, report a `warn` by its code):
       • the two caps — the description against `sk-metadata-cap` (error) and against
         `sk-description-portable` (warn) and the 250-character budget (`sk-description-budget`, warn), all counted on the PARSED value, never the YAML
         source lines.
       • triggers in the SECOND sentence (`sk-trigger-position`), so truncation never eats them.
       • the `Not for:` boundary present (`sk-no-boundary`).
       • body length (`sk-body-length`) — on-demand doctrine goes to a bundled reference.
       • a `**Done when:**` criterion per numbered step (`sk-step-criterion`) — that literal
         marker is what the tool counts.
       • scoped tool grants (`sk-unscoped-bash`) and coherent invocation control
         (`sk-unreachable`, `sk-invocation-value`).

     What no tool checks — the reason the doctrine still gets read:
       • every line passes the no-op test (name an input the line changes; else delete it);
         prescriptions are positive; negation is reserved for hard invariants.
       • the description is the ONLY always-on text, and it is now the only one there is.
         Its triggers and its `Not for:` boundary are what route a spoken request here; a
         description trimmed to a `/`-menu label routes nothing.
       • never `context: fork` on a command that gates on a mid-flow confirmation — a forked
         context cannot present the plan whose OK the run depends on.
       • THE EXECUTION PROFILE IS BOUGHT, NEVER COLLECTED. The default is every lever off;
         each commented field above is opened only with a stated buy, priced in
         `.agents/quenching/references/components-command-new/capabilities.md` and shown in
         the mint's plan. -->
