---
name: <kebab-case identity — what it is, not "helper"/"assistant">
description: <What this agent does and WHEN to invoke it, in one sentence — the description
  is always-on context, same cost discipline as a command's. State the timing explicitly
  ("after X has been created or modified", "when asked to audit Y"). Not for: <adjacent job>
  → <owning agent or command>.>
tools: <the NARROWEST set the work needs — Read, Grep, Glob for a verifier; add Write/Edit
  or scoped Bash(…) only when the agent's job is to produce files. Omitting the field grants
  everything, which is a choice, not a default.>
# model: <inherit (the default — leave unset) | a cheaper pin ONLY for extraction/collection
#   whose failure is cheap and cross-checked. A pin here is cache-safe: the agent runs in its
#   own context. Judgment — classification gating a deletion, ranking, authoring — never
#   downgrades.>
# effort: <low for mechanical collection; omit to inherit.>
# skills: <preload ONLY a skill every run reads — the FULL body persists for the agent's
#   whole execution.>
# memory: <true only when the agent genuinely learns across sessions; its memory file is a
#   recurring cost on every run.>
---

You are <the role, addressed in second person — an agent's body is its system prompt and
its complete operational manual; "You are…", "You will…", never the imperative voice a
command body uses>.

**Your core responsibilities:**

1. <numbered, concrete — "check every X for Y", never "look for issues">
2. <…>

**Your process:**

1. <the steps, each ending in an observable state>
2. <…>

**What you do NOT do:**

- <the false-positive control — name what is out of scope so it is never flagged: style
  preferences, sections a mold does not fix, anything another agent owns>
- <a verifier NEVER edits — findings re-enter through the command that owns authoring,
  under its own confirmation>

**Output format:**

<the fixed report shape. For a verifier: overall status (pass · pass-with-warnings · fail),
critical findings (breaks function or safety), warnings (works but suboptimal), passed
checks, and recommendations each citing the standard it applies. Guard every
optional-feature check with "if present", so a minimal artifact passes clean.>

<!-- MOLD (quenching · subagent definition) → becomes `.claude/agents/<name>.md` in a target
     repo, minted by /quenching-components agent-new under `/docs/standards/automation/agents.md` and the
     execution-profile doctrine
     (.agents/quenching/references/components-command-new/capabilities.md §Subagents).

     WHEN AN AGENT PAYS: the returned summary is much smaller than the work that produced it
     (a repo-wide sweep, a many-file audit, a doc read ending in one table), or slices run in
     parallel, or the tool set must be narrower than the conversation's. An agent WASTES when
     its output is as large as its work, when the task is one quick lookup, or when the
     handoff context costs what the isolation saves.

     THE DESCRIPTION IS ALWAYS-ON. Every agent definition charges its description to every
     session, exactly as a command does — the same two caps apply, counted on the parsed
     value. An agent with no description cannot be delegated to and is reported as
     `sk-agent-no-description`.

     VOICE SPLIT: an agent body is written in SECOND PERSON (it is a system prompt); a
     command body is imperative instructions. Do not mix them. -->
