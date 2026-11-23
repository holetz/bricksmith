# AGENTS.md — <repo-name>

<one line: what this repo is. The harness auto-loads this file on EVERY turn — it earns each line.>

## Operating this repo

Commands, env vars, ports, and etiquette the agent needs on **every** task — keep them verbatim.

```bash
<build / run / test / lint — the fenced, copy-pasteable commands>
```

- <env var / port / local quirk needed every turn>
- <agent etiquette: a permission, a tool rule, a "never do X" — harness-operational only>

## Where knowledge lives

Knowledge is **NOT** in this file — it lives in the OKF bundle at [/docs/](/docs/index.md).
Point, never paraphrase; each bullet lists a home THAT EXISTS with its boundary one-liner.

**Resolving a term.** Hit an unfamiliar repo word, acronym, or codename? Look it up in the
**glossary first** → [/docs/glossary.md](/docs/glossary.md): the A–Z lookup,
one entry per term with a link to its full doc when one exists. `grep -i '<term>' /docs/glossary.md`.

- [/docs/standards/](/docs/standards/index.md) — how WE build (current, proven contracts).
- [/docs/concepts/](/docs/concepts/index.md) — generic understanding we hold (concepts, learnings);
  its [glossary.md](/docs/glossary.md) is the term lookup.
- [/docs/external/](/docs/external/index.md) — facts about what we consume (tools, libs, regulations).
- [/docs/<other-home>/](/docs/<other-home>/index.md) — <boundary one-liner>.
- [specs/backlog/](specs/backlog/index.md) — task inbox (park now, triage later); a
  quenching-managed sibling of the plan folders in `specs/`, **outside** the `/docs/` bundle. Drop
  this bullet if the repo has no `specs/`.

<!-- HOW TO FIND A TERM: the glossary is the repo's A–Z vocabulary index at
     /docs/glossary.md — a flat, alphabetically sorted bullet list (the same syntax
     every index.md uses), linked when a concept doc exists. To resolve a word: `grep -i
     '<term>' /docs/glossary.md` (or Ctrl-F it). A matching entry gives the local
     meaning and, when linked, its full concept doc; no entry means the term is not yet
     defined — add it with the `quenching-knowledge-define` skill (one term), backfill many at once
     with `quenching-knowledge-glossary-backfill`, or capture the concept with `quenching-knowledge-learn` (which
     enters the term as its tail step). -->

To **create / edit / move** knowledge (keeping the listing in sync), use the
`quenching` skills: `quenching-knowledge-add` to add one, `quenching-knowledge-define` to add a glossary
term, `quenching-knowledge-align` to migrate/normalize, `quenching-components-harness-align` to keep this file thin.

## Agent etiquette

<Harness-operational rules only — how to behave in this repo, not what the domain is.
Anything that is durable knowledge belongs in a /docs/ home above, cited here, never inlined.>

- **Every development request goes through a spec**, and the spec runs the flow as far as the
  request authorizes. A request that names a change — "implement X improvement", "fix Y" — is a
  request to build it: develop, execute, conclude, open the PR, without stopping to ask whether to.
  Only a request that names the artifact — "create a spec for X" — stops at the spec. **Merge and
  deploy are not implied by a change request**: they run only when the human declares autonomy in
  the request itself ("do it autonomously", "PR and merge", "all the way to deploy"). Without that
  word the run ends at the open PR, named for the human to decide. The approval a spec carries
  covers every step inside that scope — no later step asks the human again.
- **A new skill joins its family's router** (`.agents/skills/<family>/SKILL.md`) when the family has
  three or more model-invocable skills — never a loose entry, and never an edit to an agent that
  came from the plugin (`.claude/agents/quenching-*.md`).

<!-- Root harness pointer, auto-loaded by every supported harness on every turn. Keep it a thin pointer:
     repo-wide operations + the /docs/ home map. Knowledge is MOVED into /docs/, never copied here.
     Installed/maintained by the quenching plugin; this file is a harness pointer, not an
     OKF concept. -->

<!-- MOLD (quenching · root harness pointer — do NOT copy this note into the produced file):
     Produces the repo-root AGENTS.md as a THIN, HONEST navigation pointer over the OKF bundle.
     • NO frontmatter, NO `type` — a harness file is EXEMPT from OKF (okf-spec §strict-7); the
       validator skips it. Pointer honesty is verified by the `quenching-components-harness-align` skill, not the
       validator: EVERY link here MUST resolve before the run ends.
     • KEEP only what the agent needs on EVERY task (commands, env, etiquette). MOVE durable
       knowledge into its /docs/ home (via the quenching-knowledge-add procedure) and leave a one-line
       pointer that CITES the doc — never a paraphrase (a restated rule is drift, like a lying index).
     • List ONLY homes that exist in this repo — drop the bullets for absent homes; do not invent
       a home. Structure/links are canonical English; prose MAY follow the repo's language.
     • KEEP the "Resolving a term" pointer + the HOW-TO-FIND-A-TERM comment when the repo has a
       `glossary.md` — term resolution via the glossary is a habit worth one line every
       turn. Drop both only if the repo has no glossary.
     • KEEP the spec-first development rule when `.agents/quenching/bin/cq specs config` reports a store: it is the line
       that tells the agent a change request means build it (develop → execute → conclude → PR) and
       that merge and deploy wait for the human to declare autonomy. Drop it only where the repo has
       no spec store.
     • Size budget: aim ≤ ~60 lines. If it is longer, more knowledge still needs to move out. -->
