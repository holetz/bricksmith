# `standards/` — current/active reference, OUR contracts

The current/active **standard/contract** — "how it should be and why", versioned, the
shared source of truth. One standard per file; subfolder names by **subject**. Each
concept doc carries `type: standard` + a derived `resource:` (the repo scope it governs).

**Boundary:** `standards/` = "how **WE** do it (current/active)". Distinct from
[external/](/docs/external/index.md) (external facts we consume) and
[catalog/](/docs/catalog/index.md) (our data). Direction lives in
[vision/](/docs/vision/index.md). An **agreed-but-unproven** rule sits here as
`authority: background` and graduates to `current` once proven — there is no separate decisions
home.

Keep only the subtopics that apply to the repo; within each, break standards **one
concept per file** by considering the candidate sub-standards catalog (a
**consideration** checklist, evidence-gated generation, recorded deferral — not a blind
generate list). The **agent-facing pointer** for this home is [AGENTS.md](AGENTS.md)
(auto-loaded).

## Subtopics

* [agents/](agents/index.md) — how we instruct agents: the language our prose is written in, and the conduct expected of the agent writing it
* [architecture/](architecture/index.md) — system structure + architectural patterns (patterns live here)
* [code/](code/index.md) — code conventions, imports, lint, pins, SYMBOL naming
* [naming/](naming/index.md) — DATA naming (tables/columns/descriptions)
* [data-modeling/](data-modeling/index.md) — grain, key, joins, catalog/schema choice
* [ci-cd/](ci-cd/index.md) — build/deploy, "code defines YAML", manifest generation
* [workflows/](workflows/index.md) — job/task/schema framework, job parameters
* [mlops/](mlops/index.md) — model lifecycle, lineage, regulatory interface
* [quality/](quality/index.md) — data quality, drift/stability, model monitoring
* [platform/](platform/index.md) — deploy targets, permissions/governance, external services

## Current docs

<!-- BEGIN GENERATED: rebuilt from disk by `quenching-knowledge-align`/`quenching-knowledge-add` — DO NOT edit by hand.
     Scans standards/**/*.md, reads title/description/generated.at (or legacy timestamp)/type, grouped by subject subfolder.
     Row model per subfolder:
       ### code/
       | Doc | Covers |
       | --- | --- |
       | [imports.md](code/imports.md) | <the doc's description:> |
-->
### agents/

| Doc | Covers |
| --- | --- |
| [communication.md](agents/communication.md) | The two languages a repo declares for what its agents write — the conversation tag on the root harness line, and an artifact language declared by a standard or the artifactLanguage config key — and the conduct contract that holds whether or not either is declared |
| [ephemeral-writes.md](agents/ephemeral-writes.md) | Where an agent puts a file it creates — the three destinations, the one question that sorts them, and the session-start declaration that makes the durable-ignored path present before the first write |

### ci-cd/

| Doc | Covers |
| --- | --- |
| [test-cost.md](ci-cd/test-cost.md) | What a run may write, how many workers it may take and how much of the suite each tier runs — one temp root outside the repository, a git configuration of the run's own, a shared worker budget and three scopes, with a narrow local gate that never replaces CI |

_(other subjects fill in as standards are created — these tables are regenerated deterministically from the real files)_
<!-- END GENERATED -->
