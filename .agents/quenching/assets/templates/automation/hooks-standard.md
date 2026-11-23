---
type: standard
title: Scoped hooks
description: Where a hook may be installed, what each scope and handler costs, and the policy defaults every hook obeys
resource: <the surfaces this rule governs — .claude/settings.json hooks + command frontmatter hooks + .claude/hooks/>
tags: [automation, hooks, performance]
generated: { by: human:<author>, at: <ISO 8601 UTC — e.g. 2026-07-06T00:00:00Z> }
audience: both
authority: background   # born background; graduates to current once the surface follows it
source: <who agreed the rule — e.g. skill-hook-new first run, team decision>
maintainer: <owner>
---

# Scoped hooks

A hook charges **other people's operations**: it fires on events the command that installed
it does not own, so a session-wide hook taxes every iteration in the repo — including every
one it never helps. This repo therefore installs every hook at the **narrowest scope that
still catches what it exists to catch**, minted by `/quenching-components hook-new` under one plan → one
OK, inventoried (report-only) by `/quenching-components align`.

## The scope ladder — narrowest first

1. **Skill-scoped** — `hooks:` in the owning command's frontmatter; fires only while that
   command runs. The default home for a check tied to one workflow.
2. **Operation-scoped** — a `settings.json` hook with an event + `matcher` (+ `if` gate);
   fires only on matched tool calls.
3. **Gated wide event** — a `Stop`/`UserPromptSubmit` hook made cheap by construction:
   dirty-gated by a marker file (an untouched turn costs one stat) or `once: true`.
4. **Unmatched session-wide** — a finding (`sk-hook-unmatched`) unless the reason nothing
   narrower suffices is stated where the hook is wired.

## The handler ladder — cheapest first

1. **`command`** — deterministic script; prints `{}` on no-match, so a non-firing turn costs
   process time and **zero tokens**.
2. **`prompt`** — one cheap-model judgment per firing.
3. **`agent`** — a full-model inference per firing; on a per-tool-call event this is
   `sk-hook-llm-frequent` and demands a stated reason.

Climb only when the rung below cannot express the check; a fast `command` rung deciding the
deterministic 95% may share a matcher with a `prompt` rung for the judgment tail.

## Policy defaults

- **Warn by default; block by consent** — a blocking hook is chosen by the human at mint,
  per rule.
- **Intrusive rules are born disabled**, their body saying when to enable them.
- **Fail open on infrastructure, fail closed on scope** — a crashed script never blocks the
  session; an unclassifiable event matches nothing.
- **Every hook carries a `timeout`** sized to its event's frequency.
- **`Stop` hooks honor `stop_hook_active`** — the re-fire loop guard.
- **Hooks are independent** — matching hooks run in parallel and never see each other's
  output; two hooks that need an order are one hook.
- **Every hook states its cost claim** at mint: event × frequency × handler cost × fast-path
  cost on no-match.

<!-- MOLD (quenching · hooks standard) → becomes `/docs/standards/automation/hooks.md` in a
     target repo's OKF bundle, offered by /quenching-components hook-new on first run (never created without
     an OK). Fill the placeholders, keep the body, add repo-specific deltas below it. Born
     `authority: background`. STAMP = MERGE, never clobber. The full doctrine lives in the
     plugin (`.agents/quenching/references/components-command-new/capabilities.md` §Hooks);
     this standard is the target-repo projection of it. -->
