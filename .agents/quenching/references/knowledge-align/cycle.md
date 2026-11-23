# The `knowledge/` front's cycle — pipeline and routing

Everything that is **not** front-specific lives elsewhere and is cited, never restated here: the
probe-before-inventory rule, the one-plan-one-OK model and the blast-radius procedure in
[`align/sweep-doctrine.md`](.agents/quenching/references/align/sweep-doctrine.md);
the cycle-authorization contract, the convergence condition and the anti-spin guards in
[`align/convergence.md`](.agents/quenching/references/align/convergence.md).

## The stage pipeline

<!-- rules -->
A **pass** is a dependency chain — each stage's output is the next
stage's input. Stage 1 is `/quenching-knowledge align`'s own work; stages 2–4 are commands it invokes.

| # | Stage | Concern | Why here in the order |
| --- | --- | --- | --- |
| 1 | the align's own structural pass | **structure** | Nothing can be filed, listed, or validated into a tree that isn't there. Scaffold homes, migrate variants, stamp frontmatter, regenerate every `index.md`, run the checker. Everything downstream assumes an aligned bundle. |
| 2 | `/quenching-knowledge import-memory` | **content in from memory** | The project's the host memory is one of two out-of-band stores of durable facts. Drain it into homes **before** the glossary sweep, so terms it introduces are in the bundle when stage 4 reads. |
| 3 | `/quenching-components harness-align` | **content in from the harness** | `AGENTS.md` (and a legacy root `AGENTS.md` with its own content) is the other out-of-band store. Harness MOVEs inlined durable knowledge into homes (delegating each MOVE to `/quenching-knowledge add`), leaving thin pointers. Runs after memory so both content feeders finish before the glossary sweep. |
| 4 | `/quenching-knowledge glossary-backfill` | **glossary from the whole bundle** | Now the bundle is structurally sound and both feeders have landed, sweep the **complete** bundle for repo-specific terms and backfill the bundle-root `glossary.md`. Swept earlier, it would miss terms stages 2–3 were still importing. |

**Stages 1–3 are probed; stage 4 is offered.** Gate stage 4 on a free proxy (concept-doc count
against glossary size, plus whether this pass created any docs) and **offer** it with its cost,
once per run.

A stage the probe found empty is **skipped** this pass. Re-validation is implicit: stage 1 ends by
running `.agents/quenching/bin/cq knowledge validate`, and the align re-probes in its loop decision.

<!-- rationale -->
The align knows before it starts whether stages 1–3 have work — a validator exit code, a directory
listing, one file read. Stage 4 has no such signal: proving the glossary is complete costs the same
whole-bundle sweep as backfilling it. Running it to find out would spend the exact budget the probe
rule exists to protect.

### Parallel prep: overlap harness discovery with the memory drain

<!-- rules -->
When both content feeders have work in the same pass (stages 2 AND 3 non-empty), overlap stage 3's
read-only prefix with stage 2's execution — plan in parallel, write in series:

1. Stage 1 completes first, as always.
2. Dispatch **one read-only `Task` sub-agent in the background** (`model: sonnet` — MOVE/KEEP
   classification is real judgment) instructed to execute steps 1–4 of `/quenching-components harness-align`
   **as written there** (inventory, unit parse, classification, blast-radius sweep) and return the
   draft `file → unit → verdict → destination` table.
3. In parallel, run `/quenching-knowledge import-memory` to completion, inline.
4. Then invoke `/quenching-components harness-align`, handing it the pre-collected table **plus the list of docs stage 2
   just created**. Harness runs its **staleness delta-recheck** before writing: a doc stage 2
   landed can flip a unit's verdict from MOVE to **DEDUPE** (the fact now has a doc) — re-verify
   (a cheap `Grep` for coverage) only the units whose home/subject intersects the new docs; the
   rest of the table stands.
5. **Writes to `knowledge/` are one stage at a time, always** — harness's writes start only after the
   memory drain's writes have finished.

When only ONE feeder has work, run it in the normal serial flow. The stages' own internal fan-outs
(memory slice classification, knowledge-scan home slices) are unchanged.

<!-- rationale -->
The logic stays in its owner; only its read-only prefix runs ahead of schedule. Two stages
read-modify-writing the same shared files (a home's `index.md`, the bundle-root `glossary.md`) is a race
with no lock. Dispatching a discovery agent with nothing to overlap only costs tokens.

## Finding → owning-command routing table

The probe and the inventory produce findings; each maps to exactly one owner. A finding with **no**
stage owner is a **per-item** or **unroutable** opportunity — surfaced in the report, never
auto-closed. The rightmost column is also what the probe reads: a run whose only findings say
**No** has nothing to align and stops there.

| Finding (read-only signal) | Owner | Auto-closed by the align? |
| --- | --- | --- |
| Variant folder name (`docs/arquitetura/`), missing applicable home | stage 1 | Yes |
| Un-stamped / mis-stamped frontmatter, `summary:`→`description:`, enum drift | stage 1 | Yes |
| Prefix-cluster to fold (`nomenclatura-*.md`), non-English slug to translate | stage 1 | Yes |
| `dir-no-index` / `index-broken-link` / `index-orphan` (validator WARN) | stage 1 | Yes |
| `okf-legacy-root` / `okf-legacy-home` / `okf-legacy-doc-quadrant` / `okf-legacy-glossary` — the bundle's own root/home/quadrant/glossary sits in a pre-rename layout | stage 1 | Yes |
| Undrained facts in `~/.claude/projects/<cwd>/memory/` | `/quenching-knowledge import-memory` | Yes |
| Fat harness — durable knowledge inlined in `AGENTS.md` or a legacy root `AGENTS.md` | `/quenching-components harness-align` | Yes (MOVE, via `/quenching-knowledge add`) |
| Broken / lying harness pointer | `/quenching-components harness-align` | Yes |
| Repo-specific term in the bundle, absent from the bundle-root `glossary.md` | `/quenching-knowledge glossary-backfill` | **Offered** — no cheap signal proves the gap, so the human is asked once per run |
| `resource-unresolved` — a `resource` entry matching nothing on disk | *(surface → `/quenching-knowledge add` to restamp)* | **No** — only a human knows what the doc now governs; reported with the entry |
| `resource-self` — the doc sits inside its own declared scope | *(surface → `/quenching-knowledge add` to restamp)* | **No** — narrowing a scope is a judgement, and a bundle aggregate is legitimate; reported |
| `glossary-broken-link` — a glossary entry points at a deleted doc | *(surface → `/quenching-knowledge define`)* | **No** — the backfill stage ADDS missing terms, it never prunes a dead one; reported |
| Coverage-ledger deferral — a candidate sub-standard not yet written | *(surface → `/quenching-knowledge add`)* | **No** — needs human-provided evidence; reported |
| A generic concept / standard / term a human has NOT yet stated | *(surface → `/quenching-knowledge learn` / `/quenching-knowledge add` / `/quenching-knowledge define`)* | **No** — no fresh human input this pass; reported |
| `user` memory, secret, or unroutable harness fact | *(kept in place)* | **No** — flagged and kept, never deleted |

The rightmost column is the anti-fabrication boundary: the align closes only what a **sweep** can
close deterministically from what the repo already contains. Content that requires a human to
state something new is **surfaced**, never invented.
