# The automation taxonomy — one axis, canonical paths, one file per entry point

In a target repo this rule is stamped to `docs/standards/automation/skills.md` from
`assets/templates/automation/skills-standard.md`; this file is the plugin-side owner.

**Owned elsewhere — cite, never restate.** The
invocation/permission decision table, the scoped-`allowed-tools` rule, and the description
caps all live in `docs/standards/automation/skills.md`; the registry zone's row format in
`.agents/quenching/bin/cq components registry reindex`. Findings arrive as `sk-*` codes and are named by code, never by
restated threshold.

## The single axis

Every skill is classified on **exactly one axis: what it acts on** — asked as up to two
questions, in order, each with a single honest answer. The first question is a
subject/category; the second is the older domain-bound-vs-generic test, now read against
that category instead of standing alone.

**1. Category/subject — the first question.** Name the one subject the skill belongs to
(`git`, `deploy`, `tests`, ...). A clean, evident answer wins. No evident subject, or
several unrelated ones, means there is no category: fall straight through to the second
question exactly as if this step did not exist — **categorization only applies when it
fits; it is never forced.**

**2. Domain-bound vs generic — the second question, read against the category.**
- **No category** — unchanged from before this rule: **domain-bound** when the skill
  serves ONE folder subtree of the repo (`communications/teams/`, `pipelines/ingest/`);
  **generic** when it serves the repo as a whole; **neither** (keep-and-report) when
  several unrelated folders answer at once.
- **Category, no single real folder tied to the skill** — e.g. `git` for a `commit`
  skill: a subject, not a repo folder. The skill nests flat under the category. **This
  example stays valid even in a repo that also has this plugin installed** — a target's own
  bare-cited `git`-category `commit` command and this plugin's `/quenching-git commit` coexist
  rather than collide, because the citation forms already separate them
  (`naming/command-surface.md` §Two citation forms, and the condition on each): the example is
  about the target's own category, and this plugin's commands are never bare-cited in a repo that
  did not vendor them.
- **Category, and a real folder tied to the skill** — the folder either **nests** inside
  the category or is **replaced** by it, per the convention already established for that
  category in *that* target repo (below). Several unrelated folders still fit neither and
  keep-and-report, exactly as the no-category case.

No category is ever invented to force a classification a skill does not otherwise earn —
the same keep-and-report exit the old single test used still applies whenever the honest
answer is "several" or "none".

### Reading the nest-vs-replace convention

The convention lives entirely inside the **target repo's own** `.agents/skills/<categoria>-*/`
— never in the plugin, and never in a dedicated registry file of its own:

- Skills already named `<categoria>-<folder>-…-<verb>` (the real folder folded into the
  name) → the convention already in force is **nest**.
- Skills already named `<categoria>-<verb>` with no real-folder segment → the convention
  already in force is **replace**.
- The category has no files yet in that repo, or the two shapes are already mixed
  (ambiguous) → ask the human once. The physical structure that first answer produces
  *becomes* the record — reused in silence for every later skill of that category in that
  repo, with nothing else written down anywhere.

Two repos may read opposite conventions for the same category name without conflict: the
convention is never shared, catalogued, or asked twice for the same category in the same
repo.

## Naming

| Case | Name pattern | Example |
| --- | --- | --- |
| No category, domain-bound | flattened folder path + verb, kebab-case | `communications/teams/` + create → `communications-teams-create` |
| No category, generic | verb-object (or the object alone when the verb is implied), no folder path | `release-notes`, `generate-changelog` |
| Category, no folder tied | `<categoria>-<verb>` (or `<categoria>-<verb-object>`) | `git` + commit → `git-commit` |
| Category, folder nests | `<categoria>-<folder-path>-<verb>` | `deploy` + `infra/terraform/` + apply → `deploy-infra-terraform-apply` |
| Category, folder replaced | `<categoria>-<verb>` | `deploy` + `infra/terraform/` + apply → `deploy-apply` — the category takes the folder's place |

Every name is **one segment**: the host discovers `.claude/skills/<name>/SKILL.md` only flat,
and the bridge copies the source folder as it is. The name alone tells where the skill acts: a
reader scanning `.agents/skills/` reconstructs the monorepo map from the domain-bound and category paths, and anything at the
top level is repo-wide by declaration.

## Placement — the path IS the identity

<!-- rules -->
- **One file per entry point.** A `.agents/skills/<name>/SKILL.md` carries both the
  description that routes to it and the body that runs, invocable as `/<name>`; its frontmatter
  `name:` equals the folder name. The name is the whole identity, and nothing derives a second
  name that could disagree with it. Mold: `assets/templates/automation/command.md`.
- **`.agents/` is the only editable source.** `.claude/skills/<name>/` is the copy
  `.agents/quenching/bin/cq components bridge` generates from it, marked as generated and pinned by a sha256
  manifest; it is never edited, and `bridge --check` names its drift.
- **A generic skill with no evident category is a flat** `<verb-object>`, never prefixed with a
  folder or category it does not serve.

**A skill's folder holds only that skill**: `SKILL.md` and the files it alone reads. Shared
procedure lives in `.agents/references/<name>/` and eval cases in `.agents/evals/<name>/`, cited
by path. A legacy `.agents/skills/` surface is `/quenching-components align`'s to migrate
(`.agents/quenching/bin/cq components migrate`); the mint never writes into it.

**A family is a router, not a set of skills.** Three or more skills of the target sharing the first
kebab segment are one vertical: `.agents/skills/<family>/SKILL.md` with the flow table and
`references/<flow>.md` inside the skill. The mint adds a flow to an existing router; the grouping of
loose skills is `/quenching-components align`'s, with the human's answer per family.

**Accepted variation (documented, never generated):** directory-scoped surfaces
(`<folder>/.agents/skills/`) also bind a command to a folder — the sweep classifies them in
place. The mint always writes into the repo-root `.agents/`, keeping the surface auditable in
one place.

<!-- rationale -->
the host merged custom commands into skills, and OpenCode reads `.claude/skills/` and
`.agents/skills/` alike. A flat `.agents/skills/<name>/` source copied verbatim into
`.claude/skills/<name>/` is the one shape all three harnesses discover, with no link, so it holds
on Windows without administrator rights.

Discovery story: `/` + tab walks the command tree in folder order, so the paths make the
automation surface navigable the same way the repo is.

## The registry and its GENERATED zone

<!-- rules -->
The registry at `docs/project/automation.md` (`type: project`,
stamped from `assets/templates/automation/registry.md`) is the bundle's authoritative
listing of the local automation surface.

**`.agents/quenching/bin/cq components registry reindex` owns the zone's format.** The row shape, the column
order, the sort, and the placeholders are the tool's. To see the current shape, run the
tool; to change it, change the tool.

This file owns, because no tool decides it:

- **Where the zone may live.** Between `<!-- GENERATED:BEGIN -->` and
  `<!-- GENERATED:END -->`, and nowhere else. **Curated prose** — how the surface is
  organized, a link to the rule, pointers to installed plugins — lives **outside** the
  markers and is never touched by regeneration. A registry with no markers is
  `sk-no-zone`.
- **What the zone is derived from.** The local entry points' frontmatter
  (`.agents/skills/*/SKILL.md`; `.agents/skills/**/*.md` on a legacy layout),
  and only that: the zone lists the repo's **own** surface. Commands contributed by
  installed plugins (`/quenching-specs-*`, marketplace plugins) stay out of it and may be pointed
  at from the curated prose.
- **Who may run the regeneration.** `/quenching-components command-new` (in its OKF tail) and
  `/quenching-components align` (in its verify step). Neither writes between the markers by
  hand, and neither composes the table itself. Both end their run on a second
  `registry reindex` reporting `changed: false`.

<!-- rationale -->
The format is stated in one place that executes, not in prose two skills reproduce by hand.
On `sk-no-zone` the tool refuses rather than placing a table at a guessed anchor inside prose a human wrote. A skill
that generates a derived table and then diffs it against its own source is one reader
checking its own arithmetic, which is the failure this tool exists to remove; a hand edit
inside the markers is therefore repaired, not accumulated.
