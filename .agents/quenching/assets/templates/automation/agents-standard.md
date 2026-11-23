---
type: standard
title: Subagent authoring
description: When work becomes a subagent, the definition contract for .claude/agents/, and how the surface is inventoried
resource: <the surface this rule governs — .claude/agents/>
tags: [automation, agents, delegation]
generated: { by: human:<author>, at: <ISO 8601 UTC — e.g. 2026-07-06T00:00:00Z> }
audience: both
authority: background   # born background; graduates to current once the surface follows it
source: <who agreed the rule — e.g. skill-agent-new first run, team decision>
maintainer: <owner>
---

# Subagent authoring

A **subagent** is a delegation: work runs in a fresh context and only its result returns.
One definition per file at `.claude/agents/<name>.md`, minted by `/quenching-components agent-new` under
one plan → one OK, inventoried (report-only) by `/quenching-components align`.

## When work is a subagent — and when it is not

Delegate when the returned summary is **much smaller than the work** that produced it (a
repo-wide sweep, a many-file audit, a read that ends in one table), when slices run in
parallel, or when the tool set must be narrower than the conversation's. Keep work inline
when its output is as large as itself (a rewrite lands in context anyway), when it is one
quick lookup, or when the handoff context would cost what the isolation saves. A recurring
*workflow* is a command; a delegated *unit of work* is an agent — the two are not rivals,
and a command may invoke an agent as one step.

## The definition contract

- **`description` is always-on context** — the same cost discipline and caps as a
  command's, counted on the parsed value. It states what the agent does **and when to invoke
  it**; an empty one is `sk-agent-no-description` (the agent can never be delegated to).
- **`tools` is scoped** to the narrowest set — read-only (`Read, Grep, Glob`) for a
  verifier; omitting the field grants everything, which is a choice a reader must be able to
  see was made.
- **`model`/`effort` pins** are cache-safe here (the agent owns its context) but follow the
  same policy as everywhere: mechanical extraction may run cheap; judgment — anything that
  gates a deletion, ranks, or authors — inherits the session's.
- **`skills`, `memory`** are opened deliberately, never defaulted into: a
  preloaded skill's full body persists for the whole run, and agent memory is a recurring
  per-run cost.
- **The body is a system prompt, in second person** ("You are…") — a command body is
  imperative; the two voices never mix.

## The verifier shape

The highest-value agent for a kept-conformant repo: it **inspects and reports; it never
edits**. Its definition carries numbered check areas, an explicit **"not checked here"**
list (the false-positive control), *if-present* guards on optional features so minimal
artifacts pass clean, and a fixed report — status, critical findings, warnings, passed
checks, recommendations citing the standard each applies. Fixes re-enter through the
command that owns authoring, under its own confirmation.

<!-- MOLD (quenching · agents standard) → becomes `/docs/standards/automation/agents.md` in a
     target repo's OKF bundle, offered by /quenching-components agent-new on first run (never created
     without an OK). Fill the frontmatter placeholders and keep the body; add repo-specific
     deltas below the standard text rather than rewriting it. Born `authority: background`.
     STAMP = MERGE, never clobber. The full doctrine lives in the plugin
     (`.agents/quenching/references/components-command-new/capabilities.md` §Subagents); this
     standard is the target-repo projection of it. -->
