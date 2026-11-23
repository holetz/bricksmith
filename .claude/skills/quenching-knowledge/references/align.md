---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash, Write, Edit, Task, Skill, AskUserQuestion
---

# /quenching-knowledge align — force the knowledge base into OKF shape, and keep filling it

**Input**: `$ARGUMENTS` (optionally a `/docs/` path or a scope; omit to align the whole bundle).

The **`docs` front's one entry point**. It installs and enforces a single canonical OKF v0.2
bundle, pulls in the durable content outside it (project memory, a fat harness) and backfills the
glossary, looping until a pass changes nothing.

The payload (skeleton, molds, validator) lives at `.agents/quenching/assets/`; the contract at
`.agents/quenching/references/knowledge-align/`:

- [knowledge-align/okf-spec.md](.agents/quenching/references/knowledge-align/okf-spec.md) §Normative rules §Bundle & conformance — the normative OKF v0.2 rules (MUST/SHOULD/MAY).
- [knowledge-align/taxonomy.md](.agents/quenching/references/knowledge-align/taxonomy.md) §The canonical tree (locked) §The `type` vocabulary §Boundary rules (memorable summary) §The homes, one by one — the canonical tree, homes, `type` vocabulary, boundaries.
- [knowledge-align/migration.md](.agents/quenching/references/knowledge-align/migration.md) §1. Map variant → canonical §3. Measure and SURFACE the blast radius BEFORE executing §4. Propose the migration; NEVER rename/delete without OK — variant→canonical map + blast-radius doctrine.
- [knowledge-align/conformance.md](.agents/quenching/references/knowledge-align/conformance.md) §Bundle level §Structural integrity (whole-tree — CLI + `Stop` only) §Resource integrity (per-doc — every mode) — the exact checks the validator applies.
- [knowledge-align/cycle.md](.agents/quenching/references/knowledge-align/cycle.md) §The stage pipeline §Finding → owning-command routing table — the stage pipeline, the parallel-prep flow, and the finding → owning-command routing table.

The executable checker is the target-local `.agents/quenching/bin/cq` (`.agents/quenching/bin/cq knowledge validate docs` → exit 0 = conforms), the one
form every body writes —
[align/tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool.

## Doctrine (non-negotiable)

Read [align/sweep-doctrine.md](.agents/quenching/references/align/sweep-doctrine.md)
§1. Probe before the inventory §2. Convergence, not accommodation §3. Force with ONE confirmation §4. The blast-radius sweep §5. MERGE, never clobber; never delete on a guess §6. Align conformance; report the cycle §7. Citing a command — three forms, one condition each §8. End honest.
What follows is specific to `/docs/`:

**Why `Bash` is unrestricted here.** This front probes and updates the target's bundle, harness,
memory store, Git refs and site layer, which share no prefix to enumerate in `allowed-tools`; each
write stays gated by the workflow below.

- **This is the one front with a real loop**: a fact the harness MOVEs in is a term the glossary
  must then index. The loop ends at a **fixpoint** — a pass that changed nothing with the validator
  clean — bounded by a pass cap and a no-progress guard
  ([convergence.md](.agents/quenching/references/align/convergence.md) §The convergence contract).
- **Conduct the content stages, never reimplement them.** Steps 6–7 **invoke**
  `/quenching-knowledge import-memory`, `/quenching-components harness-align` and `/quenching-knowledge glossary-backfill` through the `Skill` tool
  under their registry names (`quenching-knowledge import-memory`, …); each runs under its own
  doctrine and code-coupled confirmations.
- **The expensive stage is offered, never assumed.** The glossary sweep reads the whole bundle and
  has no cheap signal, so it is gated on a free proxy and **offered** with its cost
  (sweep-doctrine §Probe before the inventory).
- **Per-item commands are stage tools, not stages.** `/quenching-knowledge add`,
  `/quenching-knowledge learn` and `/quenching-knowledge define` each act on ONE item a human
  states, and a loop pass has no fresh human input. A content gap only they can close is
  **surfaced** in the report, never fabricated.
- **Completeness of what fits, evidence-gated.** A repo without data gets no `catalog/`; a
  standard is generated only with observed `file:line` evidence; the rest is a recorded
  deferral, never a silent skip.
- **Residuals are classified, not hidden.** Deterministic local defects are `error-corrigible`;
  absent checkout evidence is `source-gap`; absent installed assets are `plugin-package-gap`;
  disagreement with the accepted publication map is `plan-drift`; and a disabled publication or
  capability is `not-applicable`. The final report names the evidence, owner and closing command
  for every residual class.
- **Folders over prefix-clusters.** Sibling files sharing a subject prefix
  (`nomenclatura-classes.md`, `nomenclatura-funcoes.md`, …) are promoted into a subfolder
  (canonical English), prefix stripped, with a generated `index.md`
  (`code/symbol-naming/{classes,functions,modules,…}.md`). Keep flat files when a folder adds
  ceremony without aiding disclosure (a lone file, an incoherent prefix, two short siblings).
- **English canonical surface — structure + frontmatter; content may be local.** Folder names
  **and concept-doc file slugs**, frontmatter keys, enum values, and the `type` vocabulary are
  canonical English, cross-repo greppable (`nomenclatura-variaveis.md` → `naming/variables.md`,
  `validacao-desenvolvimento.md` → `development-validation.md`). The **body prose MAY follow the
  repo's language** — only the content, never the surface.
  **Exception — identifier-derived names are verbatim, never translated:** a catalog
  `<schema>`/`<table>` slug mirrors the real object, `external/repositories/<repo>` the real
  repo.

## Workflow (probe → ONE OK → pass → re-probe → loop)

### 1. Probe — the three reads that decide whether anything else runs
Resolve the bundle at its fixed root `/docs/`, then read all three signals and nothing
else:
```bash
.agents/quenching/bin/cq knowledge validate docs --json          # structure: exit 0 = conformant
ls ~/.claude/projects/<cwd>/memory/    # out-of-band store 1: any undrained memory?
```
plus one `Read` of each harness file that exists (`AGENTS.md`, and a legacy root `AGENTS.md` that
carries more than `@AGENTS.md`) — a fat one inlines
durable knowledge; a thin one points. The glossary is **not** probed: it has no cheap signal, so
it is proxied in step 7 instead.

Branch as sweep-doctrine §Probe before the inventory prescribes:

| Probe result | What happens |
| --- | --- |
| validator exit 0 with no findings, memory dir empty, harness thin | **STOP.** Report "`/docs/` conformant, N docs, nothing out-of-band, nothing to align" and end. No inventory, no plan, no confirmation. |
| exit 0 and the only findings are ones this command **surfaces** rather than closes (cycle.md's routing table, rightmost column `No`) | STOP the same way, then list them with the command that closes each. |
| any signal shows work | Continue to step 2. |

**No bundle at all** (no `index.md` / `okf_version`) is not a failure — it is the
install case, and step 4 scaffolds it.
**Done when:** the three signals are in hand and the run has either stopped or committed to a pass.

Before the inventory, run a **preflight** whenever the pass can use them: the source checkout
contains the code, bundles and services the findings name, the installed plugin contains the
translation and design payloads it claims, and every checker has an applicable publication or
capability row. A missing source is a `source-gap`; a missing installed payload is a
`plugin-package-gap`; neither is repaired by inventing a file, asset, route or resource — report
the evidence and the closing command. A checker whose publication the accepted map or capability
register disables is `not-applicable`.

When a site layer exists the preflight also reads the accepted `.quenching/documentation/plan.md`;
its `### Publication map` is the authority for homes, routes and derived pages. A disagreement
between that map and `nav` or `site-source/` is `plan-drift`, closed by regenerating through the
documentation planning/build command. Semantic resource ambiguity stays a human-pending
`source-gap`.

### 2. Inventory + map → the alignment plan (read-only)
When `$ARGUMENTS` names a home or subtree, keep the fixed-root probe whole-bundle but restrict this
inventory, plan, and structural write set to that scope; report out-of-scope findings without touching
them. With no argument, use the whole bundle.
Detect the existing sections, which docs carry frontmatter, and match each section to a canonical
home via the variant→canonical map
([knowledge-align/migration.md](.agents/quenching/references/knowledge-align/migration.md) §1. Map variant → canonical) —
top-level (`arquitetura/`→`standards/`) and subfolder (`codigo/`→`code/`). Read
[knowledge-align/conformance.md](.agents/quenching/references/knowledge-align/conformance.md)
§Structural integrity (whole-tree — CLI + `Stop` only) §Resource integrity (per-doc — every mode) and
route every probe finding to its owner via
[knowledge-align/cycle.md](.agents/quenching/references/knowledge-align/cycle.md) §Finding → owning-command routing table. Produce
the plan — enumerate:
  - **(a)** homes to scaffold (only those that apply);
  - **(a2)** pre-rename plugin-layout sites to migrate (`okf-legacy-root`/`-home`/
    `-documentation-home`/`-doc-quadrant`/`-glossary` findings) — the bundle root, a home, the
    retired `documentation/` wrapper, a quadrant under it, or the glossary still sitting under a
    name a plugin release retired; resolved via
    [knowledge-align/migration.md](.agents/quenching/references/knowledge-align/migration.md)
    §1g, each swept for blast radius like any other rename (f);
  - **(b)** variants to migrate/rename (with per-item destination);
  - **(b2)** **prefix-clusters to fold into subfolders** (`nomenclatura-*` siblings → a
    `symbol-naming/` folder, prefix stripped) — one folder per coherent cluster; note the ones
    kept flat and why;
  - **(b3)** **non-English slugs to translate** — every concept-doc file slug on a technical home
    that is not canonical English, with its English target (`convencoes.md` → `conventions.md`);
    leave identifier-derived names (catalog tables, repo names) verbatim;
  - **(c)** misfiled docs to relocate (semantic placement, per item);
  - **(d)** frontmatter to stamp/normalize — add non-empty `type`, migrate `summary:`→
    `description:` and `updated:`→`generated.at` when the actor is known (otherwise keep the
    legacy `timestamp` — never invent a `by`), normalize enums to canonical English;
  - **(e)** `index.md` files to (re)generate (reserved listings) — **one per directory that
    holds concept docs** (the `dir-no-index` set from the checker), plus any `index.md` that
    wrongly carries frontmatter, or `README.md` to convert to `index.md`;
  - **(f)** the **blast radius** of every code-coupled rename (a slug translation or a
    cluster-fold is a rename — sweep its references like any other);
  - **(g)** **which content stages will run this pass** — memory (dir non-empty), harness (fat),
    each with its count. A stage the probe found empty is skipped, not run to confirm it is empty.

On a later pass, re-derive only what the previous pass could have changed.
**Done when:** one plan enumerates (a)–(g), with a per-item destination and a scope per rename.

### 3. Present the full plan → gate on ONE OK
Show the whole plan, including which content stages will run and the pass cap the OK authorizes:
*"this authorizes up to `<cap>` passes over the stages below; any item that touches product code
still pauses for its own confirmation, always."*

For each variant rename, sweep references per sweep-doctrine §The blast-radius sweep and **report
the scope**: how many files, which reach **product code**, which non-`/docs/` referrers (commands,
`AGENTS.md`, prose links) the rename edits. When the rename set is more than a handful, delegate
the mechanical collection to **one read-only `Task` sub-agent** (`model: haiku`, `effort: low`)
returning `rename → [file:line, …]` and classify each hit yourself. The batch OK covers exactly the
enumerated docs-only set. Each **code-coupled** rename is its **own** confirmation item.

This gate runs **once per run**, before pass 1 — later passes narrate their plan and do not re-ask
([convergence.md](.agents/quenching/references/align/convergence.md)
§The cycle-authorization contract).
**Done when:** one OK covers the batch, or each code-coupled item was answered on its own, or the
plan was rejected and nothing was written.

### 4. Execute the structural pass (invasive)
- **Scaffold** missing homes from `.agents/quenching/assets/knowledge/` (copy the applicable
  `index.md` listings, e.g. `tutorials/**`; adapt boundary
  lines to the repo). When scaffolding `concepts/`, also copy
  its **fixed `glossary.md` seed** — the repo's A–Z term lookup — to the bundle root, and list it in
  `concepts/index.md` (it is the only pre-seeded concept doc the skeleton ships).
- **Migrate** variants: move the folder, update every cross-ref found in step 3 (relative
  within a home, absolute `/docs/...` across homes).
- **Resolve `okf-legacy-*` sites** ([migration.md](.agents/quenching/references/knowledge-align/migration.md)
  §1g): `git mv` the root/home/quadrant/glossary to its canonical name, root first, then repoint
  every code-coupled reference the blast-radius sweep found (a path default, a docstring, a hook's
  own root constant) — each such site is its **own** confirmation from step 3, never folded into
  the batch OK.
- **Stamp/merge** frontmatter with the molds in `.agents/quenching/assets/templates/`
  (`standard-front.md` for standards, `concept-front.md` otherwise).
- **Regenerate** every `index.md` deterministically — **one for every directory that holds
  concept docs** (create the missing ones the `dir-no-index` check reports; a subject folder,
  a freshly folded cluster, and a catalog schema dir all get a front door). Each listing links
  its real children (no `dir-no-index`, no `index-broken-link`, no `index-orphan` left behind);
  strip stray frontmatter; for `standards/index.md` rebuild only the
  `<!-- BEGIN/END GENERATED -->` zone with `.agents/quenching/bin/cq knowledge listing --write`, then confirm it with
  `.agents/quenching/bin/cq knowledge listing --check`.
- **Write** `okf_version: "0.2"` into the root `/docs/index.md` frontmatter.
- **Never create a `log.md`, and never touch one that is already there.**

**Done when:** every approved (a)–(f) item is on disk and no unapproved item was touched.

### 5. Install the language declaration and the site layer — pass 1 only, offered
Both are one-shot scaffolding, not loop stages; skip this step entirely on later passes.

**The language declaration.** Ask **once**, and only when the target's **root** harness file
(`AGENTS.md`; a legacy root `AGENTS.md` only while no `AGENTS.md` exists) carries no declaration
yet. Ask for one BCP-47 tag — `pt-BR`, `en`,
`ja` — and write a single line into that root file:

    Language: <tag> — the contract is /docs/standards/agents/communication.md

That line carries **a value and a citation, and nothing else**: never a paraphrase of the rule, and
never a second configuration key. The rule itself belongs to
`/docs/standards/agents/communication.md`, which the structural pass (step 4) has already put on
disk — so the citation resolves the moment it is written.

**Declining is a complete answer.** A repo that declares nothing is under no constraint, and
nothing becomes non-conformant for it. Never infer a tag from the prose already sitting in the
bundle, never write the line into a nested harness file — only the root one is in context at
session start, which is the whole reason this form was chosen — and on a repo that already declares
one, read it and move on rather than asking again.

**The documentation site.** Zensical builds every Markdown below `docs_dir`, so it receives a
bounded generated sibling of the bundle that keeps high-volume data and external research out. First
inspect the target root's `pyproject.toml`, `uv.lock` and `requirements.txt` and preserve the
dependency manager in use. When the project declares uv (`uv.lock` exists or `pyproject.toml` has a
`[tool.uv]` table), add `zensical>=0.0.57` with `uv add --dev` when absent, refresh with `uv lock`,
install with `uv sync` and create no `requirements.txt`. A `pyproject.toml` owned by another manager
stays with it. When no `pyproject.toml` exists, merge/copy the payload's pinned
`requirements.txt`. A `uv.lock` is authoritative: verify with `uv sync --locked` and report a stale
lock. If the selected manager is unavailable, report the missing toolchain and leave the project
files untouched.

Offer to copy from `.agents/quenching/assets/zensical/`: `zensical.toml.tmpl` → the repo
**root** as `zensical.toml` **only if absent** (never clobber a customized one — show a diff and
let the user merge), filling `site_name`/`site_description`; `quenching.css` → the bundle's own
`assets/stylesheets/quenching.css`, the path `extra_css` names; on
request, `ci-github-pages.yml` → `.github/workflows/docs.yml` (opt-in, platform-specific), or
`azure-pipelines-docs.yml` → `azure-pipelines-docs.yml` only when the target remote is Azure DevOps.
The first install also stages the bounded source with
`.agents/quenching/bin/cq knowledge site-source docs site-source --write`; `catalog/` and `external/` are absent from
that tree.

This is the **first install only**; every later update, dependency sync, nav regeneration, config
merge and build verification is `/quenching-knowledge documentation-build`'s. Anything beyond
stamping absent files — a customized config to merge, a `docs_dir` pointing elsewhere, a stale nav
or `uv.lock` — hands off to that command.
**Done when:** the two offers have been made once and answered, or the pass is >1 and this step
was skipped.

### 6. Run the content stages that have work, in order
Invoke each through the `Skill` tool under its **registry name** — `quenching-knowledge import-memory`,
`quenching-components harness-align`. Which of the three citation forms is correct, and the condition on each,
is [sweep-doctrine.md](.agents/quenching/references/align/sweep-doctrine.md) §7. Citing a command. Declare the cycle-authorization mode to each
([convergence.md](.agents/quenching/references/align/convergence.md)
§The cycle-authorization contract), and **skip any stage the probe found empty**:

1. `quenching-knowledge import-memory` — drain the project's memory dir into its homes, clearing each
   memory once its doc lands and passes conformance.
2. `quenching-components harness-align` — thin `AGENTS.md` (legacy pointer-only `AGENTS.md` removed), MOVEing durable knowledge into homes.

The order is
[knowledge-align/cycle.md](.agents/quenching/references/knowledge-align/cycle.md) §The stage
pipeline's. When **both** have work this pass, follow its §Parallel prep —
harness's read-only discovery runs in a background `Task` agent while the drain executes inline.
**Writes to `/docs/` are one stage at a time, always.**

Record what each stage reports it changed; the loop decision in step 8 reads it.
**Done when:** each non-empty stage has run and reported, or every stage was empty and skipped.

### 7. Offer the glossary sweep — never automatic
Read the free proxy: the count of concept docs in the bundle against the number of entries in
`glossary.md`, plus whether steps 4 and 6 created any docs this pass. A bundle that grew
and a glossary that did not is the signal.

Offer `quenching-knowledge glossary-backfill` with what it costs (a whole-bundle sweep, fanned out per
home) and what the proxy shows. Put it as a decision sheet ([specs-develop/questions.md](.agents/quenching/references/specs-develop/questions.md) §1; A recommended) when the answer is a plain yes/no. A
declined offer is a complete answer — record it and do not re-offer on a later pass of the same
run. Never run the sweep to discover whether it had work.
**Done when:** the offer has been made once per run and answered, or the proxy showed no gap and
the offer was deliberately not made.

### 8. Verify, then decide: loop or stop
Re-run the applicable convergence checks in this order: `.agents/quenching/bin/cq knowledge validate docs --json`,
`.agents/quenching/bin/cq knowledge project docs --check`, `.agents/quenching/bin/cq knowledge nav docs --check`,
`.agents/quenching/bin/cq knowledge site-source docs site-source --check`, a strict site build, and the rendered site
checker (`documentation-site-check.py`) immediately after that build. The projection, navigation and bounded-source checks are
skipped only when their site layer is absent or the accepted map marks that publication
`not-applicable`; a catalog checker runs only when the map exposes a catalog route. When the
glossary is mandatory, pass an explicitly evidenced renderable term with `--glossary-term` (for
example `SCD2`), never the first lexical glossary entry. This sequence is the convergence gate:
there must be no errors, no stale projections and no applicable checker failure.

Also confirm: every non-reserved doc has frontmatter and a non-empty `type`; every `index.md` is
frontmatter-free (root only `okf_version`); and the **structural-integrity WARNs are cleared —
zero `dir-no-index`, `index-broken-link`, `index-orphan`** (these are WARN, so exit 0 alone does
not prove them clean — inspect the findings).

Then re-run step 1's probe — never the step 2 inventory — and decide by the four outcomes in
[convergence.md](.agents/quenching/references/align/convergence.md)
§The convergence contract: **progress** → another pass from step 2 under the same OK, narrating its
plan; **converged** → step 9 only when the full applicable sequence above is clean; **residue** →
stop and report each `error-corrigible`, `source-gap`, `plugin-package-gap`, `plan-drift` or
`not-applicable` finding; **pass cap reached** → stop and report what remains. The guards there
(pass cap, no-progress, never-widen-scope) apply unchanged.

**Done when:** the run has converged, hit residue, or hit the cap — and which one is recorded.

### 9. Report
Summarize the whole run: N passes, what each stage did across all of them, the final validator
state, and — explicitly — what was **deliberately not closed**, each with its classification,
evidence, owner and closing command. Do not call any of these converged.

**Done when:** the report states the final convergence outcome and names any residue with its owning
command, or explicitly states that no residue remains.

## Invariants to never violate
- Never inventory before the probe, and never run a stage to find out whether it had work — the
  one stage with no cheap signal is **offered**, not entered.
- Never put a concept `type` on an `index.md`; never leave a concept doc without one.
- Never invent a `resource:` — derive it as a **glob set** of what the doc governs (standards) or
  the asset URI (catalog/external); empty is disallowed, and self-pointing (`resource-self`) is
  too, except a bundle-level aggregate like `glossary.md`.
- Never delete or rename without OK; code-coupled renames get their own confirmation. A slug
  translation and a cluster-fold are renames — same rule. A cycle-authorized run replaces only the
  batch gate with narration — never a code-coupled item's own OK, and never widens to product code.
- Never translate an **identifier-derived** slug (catalog table/schema, repo name) — it mirrors
  a real asset; anglicizing it breaks the greppable tie.
- Never leave a directory that holds concept docs without an `index.md`, and never leave a
  listing that links to a nonexistent file (a lying index).
- Never hand-edit a `<!-- BEGIN/END GENERATED -->` zone — regenerate it from disk.
- Never reimplement a content stage's logic here — **invoke** it, and never let two stages write
  `/docs/` concurrently.
- Never author content to close a gap that needs human input — **surface** it with its per-item
  command, never fabricate a standard, a concept, or a term.
- **Fix every deterministic, safe residue automatically.** Classify each remaining residue as
  `error-corrigible`, `source-gap`, `plugin-package-gap`, `plan-drift` or `not-applicable`. Never
  accept convergence while the editorial plan, the navigation, the `site-source` projection and the
  checkers disagree about the published homes.
- Never loop past the pass cap, never re-run a no-progress pass, and never treat validator exit 0
  alone as converged.
