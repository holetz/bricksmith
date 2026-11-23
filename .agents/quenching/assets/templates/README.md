# `assets/templates/` — the molds

Frontmatter + body molds the commands stamp/apply. They live **outside** the `/docs/` bundle
(so they are not themselves validated), and each produces an OKF-conformant concept doc when
filled — except the `harness/` and `automation/command|agent|hook` molds, which produce files
that are not OKF concepts at all. `/quenching-knowledge add` picks the mold by home → `type` (see
`.agents/quenching/references/knowledge-add/homes.md`).

A mold is **applied per insert, never copied wholesale**: its content reaches the target repo,
the file itself does not.

| Mold | Produces | `type` |
| --- | --- | --- |
| `concept-front.md` | any concept doc (generic frontmatter) | *(per home)* |
| `standard-front.md` | `standards/**/<concept>.md` | `standard` |
| `catalog/system.md` | `catalog/<system>/access.md` | `system` |
| `catalog/schema.md` | `catalog/<system>/<catalog>/<schema>.md` | `schema` |
| `catalog/table.md` | `catalog/<system>/<catalog>/<schema>/<table>.md` | `table` |
| `vision/area.md` | `vision/<area>.md` | `vision` |
| `sidecar.md` | `external/regulations/**` extract | `sidecar` |
| `index.md.tmpl` | a folder's reserved `index.md` listing (no frontmatter; root carries only `okf_version`) | *(reserved)* |
| `harness/agents-root.md` | a repo-root `AGENTS.md` thin pointer over the bundle | *(harness — exempt)* |
| `harness/agents-subfolder.md` | a subfolder `AGENTS.md` nearest-file pointer | *(harness — exempt)* |
| `automation/command.md` | `.agents/skills/<folder-path>/<verb>.md` — the whole entry point (**outside** the OKF bundle) | *(not an OKF concept)* |
| `automation/agent.md` | `.claude/agents/<name>.md` — a subagent definition (**outside** the bundle) | *(not an OKF concept)* |
| `automation/hook.md` | a hook **wiring** — prose mold carrying the three shapes, narrowest first; the chosen one lands in `.claude/settings.json`, command frontmatter, or `.claude/hooks/` | *(not an OKF concept)* |
| `automation/registry.md` | `project/automation.md` (the derived command registry) | `documentation` |
| `automation/skills-standard.md` | `standards/automation/skills.md` (the command taxonomy rule, born `authority: background`) | `standard` |
| `automation/agents-standard.md` | `standards/automation/agents.md` (subagent authoring, born `authority: background`) | `standard` |
| `automation/hooks-standard.md` | `standards/automation/hooks.md` (the hook scope ladder and policy defaults, born `authority: background`) | `standard` |

The **harness molds** (`harness/`) produce **exempt** files — `AGENTS.md` carries **no
frontmatter and no `type`** (harness pointers, not OKF concepts — okf-spec strict-7; the validator
skips them). `/quenching-components harness-align` applies them when it refactors a repo's harness files into thin
pointers over the bundle.

The **automation molds** (`automation/`) are the `/quenching-components-*` family's —
`/quenching-components command-new`, `/quenching-components agent-new`,
`/quenching-components hook-new` mint from the three artifact molds, and
`/quenching-components align` maintains the registry and the three `*-standard.md` rules in the
target's bundle. There is
ONE command mold, not a skill-plus-wrapper pair: the host merged commands into skills, so a
single `.agents/skills/<path>.md` carries both the description that routes to it and the body
that runs. It lands under `.claude/` (outside the bundle — never validated), while the registry
and taxonomy-standard molds are OKF concept docs the pair maintains in the bundle; the
registry's `<!-- GENERATED:BEGIN/END -->` zone is derived from `.agents/skills/**/*.md` and
only those two commands write inside its markers (taxonomy owner:
`.agents/quenching/references/components-command-new/taxonomy.md`).

## Stamp discipline

- **`type` non-empty** on every concept — the one OKF hard requirement.
- **`resource` derived, never invented** — for a standard, from the doc's `file:line`
  anchors; empty/self-pointing is disallowed.
- **MERGE, never clobber** — fill a missing key, preserve a filled or third-party one.
- **Legacy field migration** — `summary:` → `description:`, `updated:` → `generated.at` when the
  actor is known (otherwise keep a legacy `timestamp`, the OKF v0.2 fallback).
- **Canonical English enums** — `audience: both|agent|human`, `authority: current|background`.
