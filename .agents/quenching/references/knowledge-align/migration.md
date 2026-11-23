# Migration — variant → canonical, with blast-radius safety

In an existing repo with variant names, `/quenching-knowledge align` **proposes convergence to the canonical
name**.

## Contents

`.agents/quenching/bin/cq components read .agents/quenching/references/knowledge-align/migration.md` returns the heading index;
`--sections` addresses one.

## 1. Map variant → canonical

Each existing section matches a canonical home **by function**:

The canonical column is written **relative to the bundle root**, never with the root's own name
in it. That is not a style choice: the root has been renamed twice (§1g), and a table spelling it
out would have to be rewritten each time. It also keeps the table readable now that the root is
`docs/` — the same name as the commonest variant in the left column, which is a target's ordinary
documentation folder. Source and destination genuinely can be the same folder; the migration is
then in place, and what changes is the shape inside it.

| Variant (examples) | Canonical (inside the bundle root) |
| --- | --- |
| `docs/arquitetura/`, `docs/architecture-docs/` | `standards/` (or `standards/architecture/` if only that) |
| `docs/adr/`, `docs/decisions` (ADRs) | `standards/<subject>/` — restamp `type: decision` → `standard` (§1f) |
| `docs/backlog/`, `BACKLOG.md`, `docs/tarefas/` | `specs/` — **outside** the bundle (§1e) |
| `VISION.md`, `ROADMAP.md`, `docs/direcao/` | `vision/` |
| `docs/catalogo_dados/`, `docs/dominio/`, `docs/data/` | `catalog/` |
| `docs/normativos/`, `docs/regulamentos/` | `external/regulations/` (content) |
| `docs/guias/`, `docs/howto/`, `docs/how-to/` | `how-to/` |
| `docs/tutoriais/`, `docs/tutorials/`, `docs/getting-started/` | `tutorials/` |
| `docs/documentacao/`, `docs/user-docs/`, `docs/site/`, `docs/manual/`, `docs/wiki/` | `documentation/` |

### 1a. Subfolder-level map (inside `standards/`)

Convergence applies one level down — a variant **subfolder** is a smell too:
`codigo/`→`code/` · `modelagem/`→`data-modeling/` · `nomenclatura/`→`naming/` ·
`plataforma/`→`platform/` · `arquitetura/`→`architecture/` · `qualidade/`→`quality/` ·
`servicos/`→ the fitting subject (usually `platform/`, or split by content). The folder name +
frontmatter (keys, enums, and the `title:`/`description:` free-text on this agent-facing
surface) become canonical English. **A migration never translates body prose** — which language it
is written in is owned by the bundle's `docs/standards/agents/communication.md`.

### 1b. File-slug translation + prefix-cluster folding

Convergence reaches the **filename** too — a non-English concept-doc slug and a prefix-cluster
are both smells `/quenching-knowledge align` resolves as renames (each swept for its blast radius, ⇒ §3–4).

- **Translate non-English slugs** on the technical homes (`standards/`, `vision/`,
  `documentation/`, `external/` non-identifier) to canonical English describing the concept:
  `convencoes.md`→`conventions.md` · `hierarquia-tasks.md`→`task-hierarchy.md` ·
  `validacao-desenvolvimento.md`→`development-validation.md` · `notebooks-spark.md`→`spark-notebooks.md`.
- **Fold a prefix-cluster into a subfolder** (prefix stripped, English leaf names, generated
  `index.md`):

  ```
  standards/code/nomenclatura-classes.md      standards/code/symbol-naming/classes.md
  standards/code/nomenclatura-funcoes.md   →  standards/code/symbol-naming/functions.md
  standards/code/nomenclatura-modulos.md      standards/code/symbol-naming/modules.md
  standards/code/nomenclatura-variaveis.md    standards/code/symbol-naming/variables.md
  standards/code/nomenclatura-simbolos.md     standards/code/symbol-naming/symbols.md
  ```

- **Identifier-derived slugs are verbatim — never translate them.** A catalog `<schema>`/
  `<table>` mirrors the real object (`dim_associado.md` stays `dim_associado.md`);
  `external/repositories/<repo>` mirrors the real repo. The
  slug is the greppable key to the asset — anglicizing it is data loss.

### 1c. Retired canonical home — `guides/` → `documentation/`

<!-- rules -->
OKF v0.9 retired the `guides/` home; its content now lives in the `documentation/` home. A repo
already conformant on the **old** canonical (a `guides/` home under the retired bundle root) is therefore a migration candidate
too — not a variant name, but a retired home. Scaffold the `documentation/` skeleton (its
`index.md` + the four section listings + `.pages`), restamp `type: guide` → `type: documentation`,
and relocate each doc under that retired `guides/**` home **by shape** — per item, like Content relocation below,
because a legacy `guides/` folder mixes both quadrants: a **task recipe / how-to** ("how do I do
X") → `docs/documentation/how-to/`; a **learning-oriented tutorial** → `docs/documentation/tutorials/`.
Sweep the blast radius like any rename (its **own** confirmation when links reach product code).

<!-- rationale -->
Without this rule `align` would read a conformant `guides/` and never migrate it.

### 1c-bis. Dissolved wrapper home — `documentation/` → four homes at the bundle root

<!-- rules -->

`documentation/` held the four Diátaxis quadrants one level below the bundle root. It existed
because the site generator needed a non-hidden subtree to point at; the bundle root itself became
`docs_dir`, and a level whose only job was that stopped earning it. Since 2026-11-04
`documentation/` is a canonical home again (§1c-ter), so the wrapper is recognised **by
structure**: a `documentation/` that still holds a quadrant subfolder (`tutorials/`, `how-to/`,
`explanation/`, `project/`, `getting-started/`, `concepts/`) reports **ERROR
`okf-legacy-documentation-home`** — structural, one site, idempotent. A flat `documentation/` is
the home and is silent.

Resolve it in this order:

1. `documentation/tutorials/` and `documentation/how-to/` move to the bundle root under the same
   names; the pages of `documentation/explanation/` flatten into `documentation/` itself, the
   canonical home. Run §1c. FIRST if the target still has a `guides/` home: a
   quadrant has to exist before it can be promoted.
2. `documentation/reference/` becomes **`project/`** — this repository's own manual. **Never
   `reference/`**: that is a retired home name `okf-legacy-home` still claims, and reclaiming it
   would make an already-migrated bundle report as unmigrated
   (`docs/standards/architecture/retiring-a-reserved-artifact.md`).
3. **Restamp `type:` in the same commit as the move** — `tutorial`, `how-to`, `documentation`,
   `project`. `docs/standards/architecture/type-follows-home.md` is the rule and it is not
   advisory: a home's name and its docs' `type:` are two spellings of one fact, and leaving the old
   value recreates the naming complaint one level down, in the most greppable field of the bundle.
4. The wrapper's `documentation/index.md` content that mapped the bundle merges into the root
   `index.md`, which then serves the site's front door and the bundle map on one page — the
   `audience: both` every doc already declares; `documentation/index.md` stays as the home's own
   listing of its pages.
5. A generated `documentation/reference/glossary.md` is **deleted**, not moved. It was a copy that
   existed only because the canonical glossary sat outside `docs_dir`; the canonical file now
   publishes itself, and a second copy is exactly the `okf-legacy-glossary` the validator forbids.
6. `documentation/assets/` moves to the bundle root, and any `.md` in it becomes `.txt`: every
   `.md` under `docs_dir` is a page, and a generated term list is not one.

Then sweep the blast radius like any rename (§3), regenerate the allowlisted nav with
`.agents/quenching/bin/cq knowledge nav --write`, and stage `site-source/` with `.agents/quenching/bin/cq knowledge site-source docs
site-source --write`. The root `zensical.toml` must point `docs_dir` at `site-source`, never at the
complete bundle.

<!-- rationale -->

Without this rule `align` would read a conformant `documentation/` — every quadrant correctly
named, every doc correctly stamped — and never migrate it, because nothing about it is misspelled.
The home is not wrong; it is retired, which no name check can see.

### 1c-ter. Renamed home — `explanation/` → `documentation/`

<!-- rules -->

On 2026-11-04 the reader-facing explanation home renamed to `documentation/`: the readers of
business-rule pages outside engineering look for that name. The home still covers the Diátaxis
**explanation** quadrant; the name diverges from OKF v0.2, which calls it `explanation/`. A bundle
on the old name reports **ERROR `okf-legacy-home`** with the rename as its remedy.

`git mv explanation documentation`, restamp every page `type: explanation` → `type: documentation`
in the same commit (`docs/standards/architecture/type-follows-home.md`), then sweep the links and
the nav like any rename (§3).

### 1d. Renamed backlog item — `idea` → `task`

<!-- rules -->
OKF v0.11 renamed the backlog item concept: a `backlog/*.md` carrying the legacy
`type: idea` restamps to `type: task` (same minimal stamp; the optional
`priority`/`tags` keys are NOT backfilled — an untriaged legacy item simply stays
untriaged). The backlog `index.md` heading **"Developed ledger" renames to "Completed
ledger"** with columns `Task | Outcome | Date` — **existing rows preserved** (map
`Idea` → `Task`, `Developed into` → `Outcome`). **No GENERATED zone is installed or regenerated
in it.** A legacy mold reference `backlog/idea.md` maps to `backlog/task.md`. This restamp applies
**before** the hand-off in §1e, while the files are still OKF-stamped task docs.

<!-- rationale -->
§1e moves this folder into the `specs/` front, which carries no listing file at all
(`.agents/quenching/bin/cq specs list` derives what `plans/` holds from disk), so a zone written here would be a
listing nobody produces and nobody reads.

### 1e. Backlog leaves the OKF bundle — the bundle's `backlog/` home → the `specs/` front

OKF v0.13 moved parked work out of the `docs/` bundle, and it now lands in the `specs/` front as
**specs**, not as OKF docs — `.agents/quenching/bin/cq knowledge validate` no longer scans it, and a spec carries no OKF
`type:` at all.

The move is two hops, and this sweep performs only the first:

1. **`/quenching-knowledge align` moves the files.** Every task doc under the bundle's
   `backlog/*.md` moves into the **legacy `backlog/` folder inside `specs/`**,
   applying the `idea`→`task` restamp (§1d) on the way. That folder is a staging area for hop 2,
   not a destination. This is its **own** confirmation, blast-radius swept (§3–4): the move rewrites
   every cross-link into that bundle `backlog/` home.
2. **`.agents/quenching/bin/cq specs migrate` converts them.** That legacy folder is exactly the tool's input: each task
   file becomes a **captured-stage spec** in `specs/plans/`, with its `priority` / `tags` /
   `complexity` preserved as a line in `## Problem`. Name that second hop in the report and leave
   it to `/quenching-specs create` — **never hand-convert a task into a spec here**, which would be
   this sweep authoring content.

After both hops, `/quenching-specs create` and `/quenching-specs triage` own that work.

### 1f. Retired home — `decisions/` → `standards/`

<!-- rules -->
OKF v0.13 removed the standalone ADR home. A target's existing bundle `decisions/*.md`
(`type: decision`, usually `NNNN-slug.md` ADRs) migrates by restamping `type: decision` →
`type: standard` with `authority: background` (or `current` if the decision is clearly
implemented in the code), and relocating to the fitting `standards/<subject>/` — naming the
concept in an English slug, dropping the `NNNN-` prefix. This is a **per-item
semantic-placement call with its own OK**, blast-radius swept (§3–4) — never a bulk move.

<!-- rationale -->
A decision's rationale and still-open alternatives belong in an OpenSpec change's `design.md`,
not a docs home.

### 1g. Root migration — the bundle's own root changed name between releases

<!-- rules -->

A plugin release may rename a root it itself declares. The bundle root has moved **twice**:
`.docs/` → `.knowledge/`, and then `.knowledge/` → `docs/`. Nothing here is written against either
pair specifically — a future release could rename `.specs/` the same way, and this procedure has to
hold without being rewritten. Detection is **structural, sítio a sítio, never `okf_version`-gated**
— a version bump does not imply a rename happened, and a rename can land without one.

The second move was forced from outside: a dot-prefixed directory cannot be a Zensical `docs_dir`
at all (it builds zero pages and exits `0`), so a bundle root keeping its dot could never be the
documentation site's own source. That is a measured external constraint, not a naming preference.

**The root's name no longer distinguishes it from anything.** While the root was dotted, the dot
did that work — `.knowledge/` was the root, undotted `knowledge/` was a home's old name one level
inside it, and the two could not be confused. `docs/` has no such marker, and it is the single
commonest documentation folder name in the wild: the left column of the `§1. Map variant → canonical`
section's own table is full of it.
Two consequences follow, and both are load-bearing:

- **A directory is not a bundle.** What identifies an OKF bundle is its **signature** — a root
  `index.md` carrying `okf_version` — never the folder's name. `.agents/quenching/bin/cq knowledge validate` reports
  `not-an-okf-bundle` for a directory without it and withholds every per-file OKF verdict, so a
  target's ordinary `docs/` answers with one finding instead of one per document.
- **Migration is often in place.** When a target's variant folder and the canonical root are the
  same `docs/`, nothing moves at the top level; the sweep restructures inside it. Do not read a
  matching name as "already migrated" — read the signature.

`.agents/quenching/bin/cq knowledge validate` emits four independent findings for a target that has not run this sweep
since the rename, each looking at only its own site:

| Finding | Fires when |
| --- | --- |
| `okf-legacy-root` | the new root is absent and a pre-rename root (`.docs/`, `.knowledge/`) sits where it should be — one finding per former name present |
| `okf-legacy-home` | a pre-rename home name (`knowledge/`, `reference/`, `explanation/`) sits at the bundle root |
| `okf-legacy-doc-quadrant` | a pre-rename Diátaxis quadrant sits under `documentation/` |
| `okf-legacy-glossary` | `glossary.md` sits inside a home instead of at the bundle root |

Each is **idempotent** — migrating one site clears exactly its own finding, so a bundle migrated
halfway (an interrupted previous run) converges the same way `align` already converges everything
else: run it again.

**Resolve root-first, then the rest, same order every time**: `git mv` the root itself (§1. Map variant → canonical, same
mechanism as any other rename), then the homes, quadrants and glossary underneath it — each swept
for blast radius (§3) and each gated on its OWN confirmation when it reaches product code (§4). A
root rename is *never* a bare `git mv`: the root's name is also a **path constant** hard-coded into
the tool that validates the bundle (a default argument, a docstring, a hook's own root variable), so
the rename's blast radius always reaches code, and §4's code-coupled confirmation always applies —
it is never folded into the bulk "align all" opt-in.

**No dual-root compatibility window.** The old root and the new one are never both read at once;
the migration is one rename per site, immediately superseding the old name, exactly as `align`
already treats every other variant → canonical convergence in this file.

### Content relocation (distinct from rename)

A doc filed under the **wrong subject** moves to its subject home — a job/task framework under
`code/` → `workflows/`; "code defines YAML" under `platform/` → `ci-cd/`. Relocation is a
**semantic placement** call: propose it **per item, with OK**, and measure the referrer blast
radius — never fold it into a bulk opt-in.

## 2. Flag the variant as DEPRECATABLE

A variant name enters the plan's migration list with its proposed canonical destination.

## 3. Measure and SURFACE the blast radius BEFORE executing

The procedure is the shared one in
[`align/sweep-doctrine.md`](.agents/quenching/references/align/sweep-doctrine.md)
§The blast-radius sweep. Never run a scan per rename.

**This front's delta:** a variant *path* is often load-bearing beyond `docs/` in a way a bare
name is not — path constants, imports, and **docstrings** all embed it — so the alternation is
built from full path fragments (`docs/arquitetura`), not just folder names, and a hit inside a
docstring counts as product code.

**Report the scope in the proposal:** how many files, which reach **code**, and which
non-`docs/` referrers (skills, `AGENTS.md`, prose links) the rename will edit. The OK covers
exactly this **enumerated** set, shown in the proposal — never deferred to an after-the-fact
wrap-up.

## 4. Propose the migration; NEVER rename/delete without OK — code-coupled ⇒ its OWN confirmation

A migration whose blast radius reaches **product code**, or is otherwise irreversible, is a
**distinct confirmation item** with its scope shown — never folded into a bulk "align all"
opt-in. A rename that resolves to a code constant is a **refactor of the target's product**, not
a docs move: alert the user, never perform it silently. **Exception — cycle-authorized runs:** a run invoked as a stage of
`/quenching-knowledge align`'s cycle (or of `/align`) under the cycle-authorization contract
([convergence.md](.agents/quenching/references/align/convergence.md) §The cycle-authorization contract) replaces only the batch gate
with narration — a code-coupled rename still confirms on its own, always.

## 5. Frontmatter migration (field renames)

While aligning legacy docs, migrate field names to OKF (MERGE, never clobber):

- `summary:` → `description:`
- `updated:` → `generated: { by, at }` when the actor is known; otherwise keep a legacy
  `timestamp` (a v0.2 consumer reads it as the fallback). Never invent a `by`.
- a legacy `timestamp` stays readable; upgrade it to `generated.at` on the next substantive
  edit, when the actor is evidenced.
- add non-empty `type:` (from the home's vocabulary in [taxonomy.md](.agents/quenching/references/knowledge-align/taxonomy.md))
- rename the retired type `type: guide` → `type: documentation` (§1c.)
- rename the retired type `type: idea` → `type: task` (§1d; the files then leave the bundle per §1e)
- rename the retired type `type: decision` → `type: standard` (§1f; stamp `authority: background`, or `current` if implemented)
- normalize enums to canonical English (`authority: vigente` → `current`; `audience: ambos` → `both`)
- preserve third-party keys (a legacy `status:`, OKF-consumer keys, site-generator keys)
- convert each front-door `README.md` → `index.md` (strip its frontmatter; keep boundary +
  listing). The one `INDEX.md` of a standards layer becomes `standards/index.md` with the
  DERIVED `<!-- BEGIN/END GENERATED -->` zone.

## 6. Install only the missing canonical homes that apply

A repo without data does not receive `catalog/`; the baseline is completeness **of what
fits**, not a blind checklist. The same rule governs the **candidate sub-standards** one level
down: a consideration checklist, evidence-gated generation, recorded deferral.
