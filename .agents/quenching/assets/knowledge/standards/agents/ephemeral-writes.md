---
type: standard
title: Agent ephemeral writes
description: Where an agent puts a file it creates — the three destinations, the one question that sorts them, and the session-start declaration that makes the durable-ignored path present before the first write
resource: AGENTS.md, /docs/standards/agents/ephemeral-writes.md
tags: [agents, files, ephemeral, scratchpad, harness]
generated: { by: quenching/6.13.0, at: 2026-08-31T00:00:00Z }
audience: both
authority: current
source: spec 1058 — declare where an agent's ephemeral writes live and put the contract in session context
maintainer: quenching
---

# Agent ephemeral writes

An agent has three honest destinations for a file it creates. The destination is decided at the
moment of writing, because that is when its reproducibility and audience are known. The test is
one question: **can I make it again?**

This standard governs the choice of destination, not the target's retention policy, backup, or
filesystem layout. A target that adopts it declares its durable-ignored path on the root harness so
the pointer is present in session context before the first write.

## The three destinations

| Condition | Destination | Why |
| --- | --- | --- |
| another person must read it from a clean clone | versioned, in the tree | it is reviewed and survives the machine that created it |
| it cannot be reproduced identically, but is too large or sensitive for git | the target's declared durable-ignored area | it is irreproducible, but is not a disposable session artifact |
| it can be remade in minutes | the session scratchpad | it may die with the session at no cost |

Ask **can I make it again?** first. A reproducible file belongs in the session scratchpad unless
someone else must read it from a clean clone; that audience makes it a versioned artifact. An
irreproducible file belongs in the durable-ignored area when the target declares one, and in the
versioned tree when it must travel with the repository or be reviewed there.

## Declaring the durable-ignored area

The root harness (`AGENTS.md`; `AGENTS.md` is legacy) may carry one line:

```
Ephemeral writes: <path> — the contract is /docs/standards/agents/ephemeral-writes.md
```

`<path>` is the target's own durable-ignored area. The line carries the path and the citation; it
does not restate this standard or invent a retention policy. Only the root harness counts, because
only it is in context at session start. A target that declares no durable-ignored area writes no
line and keeps the ambient default.

The declaration names a destination; it does not create one. In particular, the durable-ignored
area does not yet survive a worktree: declaring it does not declare `sharedPaths`, materialise a
link, or provide history and backup. A worktree carries only tracked files, so the ignored
destination remains local unless a separate target-owned mechanism makes it shared. That plumbing
and the policy for the path belong to the target, or to a separate contract that owns them.

## Loops that outlive their command

A loop started with `&` has no owner: the command that spawned it returns, the session ends, and the
loop keeps polling, writing and holding temporary files nobody will read. A gate measured while
orphaned `until ...; sleep` loops from an earlier session were still running took 399 s for work
that finished in 227 s once they were gone, and the load outlived the session that started it.

So a command does not leave a loop behind. A wait that must repeat belongs in the foreground with a
deadline — `timeout`, a bounded retry count — and a background job is waited for before the command
returns. A loop that cannot be bounded that way is a job, and a job belongs to the scheduler that
owns its lifecycle, not to a shell that happened to start it.

## The boundary

This standard owns the routing test and the declaration's shape. It does not own:

- the mechanism that makes an ignored area shared across worktrees;
- the path, retention, backup, or sensitivity policy of a target's ignored area;
- the scratchpad behaviour of the agent runtime; or
- the command that aligns a harness and preserves this line.

Those concerns may cite this standard, but none of them changes the three destinations or the one
question that sorts a write.
