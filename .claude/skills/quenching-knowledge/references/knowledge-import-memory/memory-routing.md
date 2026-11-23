# Memory routing — from a memory file to an OKF home

**Content decides the home**; the memory's `metadata.type` is only a hint. Once the home is
chosen, the concept is filed exactly as `/quenching-knowledge add` would — see
[knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md) for the
`type`/mold/path shape and the index/log procedure, and
[knowledge-align/taxonomy.md](.agents/quenching/references/knowledge-align/taxonomy.md) for the
boundaries.

This skill writes to **only three destinations** — two `knowledge/` homes, `standards/` and
`concepts/`, plus `specs/plans/` for a **unit of work** (a quenching-managed folder outside the OKF
bundle). `vision/`, `documentation/`, `external/` (and `catalog/`) are out of scope.

## The memory file

<!-- rules -->
A the host project memory lives under
`~/.claude/projects/<encoded-cwd>/memory/` as one fact per `.md` file with frontmatter:

`<encoded-cwd>` is the **native** absolute working directory — the path the OS reports, not a
shell's mount view — with every `\`, `/`, `:` and `.` replaced by `-`:

| Native cwd | Encoded directory |
| --- | --- |
| `/home/me/repos/app` | `-home-me-repos-app` |
| `/Users/me/src/app.v2` | `-Users-me-src-app-v2` |
| `c:\Users\me\repos\app` | `c--Users-me-repos-app` |

Match the encoded directory case-insensitively.

```yaml
---
name: <kebab-slug>
description: <one-line summary>
metadata:
  type: user | feedback | project | reference
---
<the fact; feedback/project add **Why:** and **How to apply:** lines; [[links]] to siblings>
```

`MEMORY.md` (same folder) is the index — one `- [Title](<file>.md) — hook` line per memory.

<!-- rationale -->
The Windows drive letter produces the **double** dash (`c:` + `\`) and its case is not stable. This
is why the path is resolved in Python rather than from `pwd`, which under Windows Git Bash reports
`/c/Users/…` and encodes to a directory that does not exist —
`/quenching-knowledge import-memory` §The memory file owns the resolver.

## Routing table — memory `type` → likely home (content overrides)

| Memory `type` | What it holds | Likely OKF home | OKF `type` |
| --- | --- | --- | --- |
| `feedback` | how you should work (a correction / confirmed approach, with a why) | a durable working rule → `standards/workflows/` (or `standards/code`, `standards/quality` by subject); a step-by-step *how WE work* → `standards/workflows/`; an explanation of *why* → `concepts/` | `standard` / `concept` |
| `project` | ongoing work, goals, constraints not derivable from code/git | a **thing to explore or build** → a spec in `specs/plans/` (**always unranked** — inventing a priority the human never stated would violate anti-fabrication); a **binding constraint/rule** or an **agreed-but-unproven** one → `standards/` (`authority: current` / `background`); **direction** (no deadline) → `concepts/` (this command emits no `vision/`) | spec / `standard` / `concept` |
| `reference` | pointer to an external resource (URL, dashboard, ticket, tool, lib) | facts about a **named tool/lib/regulation we consume** collapse into `concepts/` (this skill emits no `external/` docs) | `concept` |
| `user` | who the user is (role, expertise, preferences) | **usually not repo docs** — personal/session context. Only migrate a **durable, team-relevant** fact (a role convention, an authority) → `concepts/` or `standards/workflows/`. Otherwise **flag and ask**; never silently delete a `user` memory. | `concept` / `standard` |

### Tie-breakers (three destinations only)
- "how **WE** do it" proven → `standards/` (`authority: current`); agreed-but-unproven → `standards/` (`authority: background`) — there is no separate decision home.
- a fact about a **named external** asset we consume → **collapses into `concepts/`** (no `external/` output).
- a **thing to explore or build** → a spec in `specs/plans/` (always unranked); **direction** (no deadline) → `concepts/` (this command emits no `vision/`); a **procedure** for how WE work → `standards/workflows/`, else `concepts/` (no `documentation/` output).
- If a memory carries **several** facts, split it — one concept per file across the right destinations.
- Anything that fits **none** of the three → flag-and-keep (do not fabricate an out-of-scope doc).

## Salvaging content

When promoting a memory to a concept doc:
- **Derive `resource` honestly** — from the URL/ticket/tool the memory names, the code glob it
  concerns, or the domain. A memory with no locatable asset gets a domain descriptor as
  `resource`, or `authority: background` if it is background understanding.
- **`source`** = the memory's origin if recorded, else "project memory".
- **`generated`** = `{ by: human:<id>, at: <today, ISO 8601 UTC> }` (memories store when written;
  if the body gives a date, keep it in the body). A legacy `timestamp` is the v0.2 fallback.
- Keep the memory's `**Why:**`/`**How to apply:**` prose in the doc body.
- Resolve `[[links]]` to the sibling memories' new doc paths when those are migrated in the same
  run; leave the human a note for any that stay in memory.

## Deletion contract

A memory is removed **only after** its concept doc is written and passes the conformance
self-check. For each migrated memory: delete its `.md` file and prune its `- [..](..)` line from
`MEMORY.md`. Leave `MEMORY.md` in place even if it ends empty. A memory that could **not** be
routed (no documentary home, a `user` fact the human did not clear, or one that only fits an
out-of-scope home — `vision`/`documentation`/`external`/`catalog` — and
cannot be collapsed into one of the three) **stays** — report it, never delete it.
