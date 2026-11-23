# `standards/agents/`

**How we instruct agents** — what this repo's always-on surface declares to whoever reads it at
session start: the language its prose is written in, and the conduct expected of the agent writing
it. Not the definition of an agent, and not the machinery that runs one.

**Boundary:** `agents/` governs the *instructions* a repo gives an agent — what holds in every
task, whichever tool is driving. Where a repo also keeps a subject for its own automation surface
(how a command, hook or agent definition is classified and authored), that surface is a different
subject and this one never restates it. Naming lives in [../naming/](../naming/index.md); the
shape of the system in [../architecture/](../architecture/index.md). One standard per file (files,
not sub-folders); each carries `type: standard` + a derived `resource:`; add each to
[../index.md](../index.md).

## Current docs

| Doc | Covers |
| --- | --- |
| [communication.md](communication.md) | The two languages a repo declares for what its agents write — the conversation tag on the root harness line, and an artifact language declared by a standard or the artifactLanguage config key — and the conduct contract that holds whether or not either is declared |
| [ephemeral-writes.md](ephemeral-writes.md) | Where an agent puts a file it creates — the three destinations, the one question that sorts them, and the session-start declaration that makes the durable-ignored path present before the first write |

## Candidate sub-standards

Break this subject **one concept per file**. The method evaluates each candidate against
the repo, generates the applicable ones (`file:line`-anchored, full OKF frontmatter), and
records the rest below as deferrals (never a silent skip):
`communication` · `ephemeral-writes`.

## Coverage / deferred sub-standards

Per-subject ledger the verify gate reads. A subject is "done" only when every candidate is
**present or listed here** with a one-line why.

- [communication.md](communication.md) — the declared language and the conduct constant (present).
- [ephemeral-writes.md](ephemeral-writes.md) — the three write destinations and the sorting question (present).
