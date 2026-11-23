---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*), Bash(py:*), Bash(rm:*), Write, Edit, Task, AskUserQuestion
---

# /quenching-knowledge import-memory — drain project memory into the OKF bundle

**Input**: `$ARGUMENTS` (an optional subset or scope; omit to drain all project memory).

Promotes the durable facts in **project memory** (`~/.claude/projects/<cwd>/memory/`) into the
canonical OKF `/docs/` bundle, then clears them from memory. Assumes the bundle already exists (run `/quenching-knowledge align` first if not). The memory-type → home routing
and the deletion contract are in [knowledge-import-memory/memory-routing.md](.agents/skills/quenching-knowledge/references/knowledge-import-memory/memory-routing.md)
§Routing table — memory `type` → likely home (content overrides) §Deletion contract;
the home boundaries, `type` vocabulary, and molds are shared with
`/quenching-knowledge add` ([knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md)
§Classification — one question decides the home §Boundary tie-breakers §The frontmatter stamp) and
`/quenching-knowledge align` ([knowledge-align/taxonomy.md](.agents/quenching/references/knowledge-align/taxonomy.md)
§The canonical tree §The `type` vocabulary §Boundary rules,
[knowledge-align/conformance.md](.agents/quenching/references/knowledge-align/conformance.md)
§Concept docs (`check_concept`) §Resource integrity (per-doc — every mode)). Molds live at
`.agents/quenching/assets/templates/`.

## Doctrine

- **Plan first, execute on one confirmation.** Cover **every** memory, classify each, and present
  ONE table — every memory → its target home + doc path + whether it will be deleted. Execute the
  whole batch on a single OK. **Exception — cycle-authorized runs:**
  invoked as a stage of `/quenching-knowledge align`'s cycle (or of `/align`) under the cycle-authorization contract
  ([align/convergence.md](.agents/quenching/references/align/convergence.md)
  §The cycle-authorization contract), the plan is
  presented as narration, not a gate — the write-then-verify-then-delete contract is unchanged.
- **Three destinations only.** This skill writes into exactly two `/docs/` homes — `standards/`
  and `concepts/` — plus the provider-owned `plans` phase for a **unit of work**
  outside the OKF bundle. A memory whose natural fit is a
  `vision`, `documentation`, or `external` doc is **re-routed to the nearest of the three** per the routing
  table ([knowledge-import-memory/memory-routing.md](.agents/skills/quenching-knowledge/references/knowledge-import-memory/memory-routing.md)
  §Routing table — memory `type` → likely home (content overrides)); a memory that fits none of
  them **stays** in memory and is flagged (like a `user`/unroutable fact). Never create a
  `vision/`, a reader-facing quadrant, `external/`, or `catalog/` doc from a
  memory.
- **Bounded reconnaissance — read indexes, not the whole tree.** The skill inspects only the two
  `/docs/` homes it writes to. **Never enumerate the whole bundle** (`find docs -type f`) or descend
  into `catalog/`. To learn a home's existing subjects, read its top `index.md` plus one bounded
  `Glob` of its immediate subjects. Read a concept doc's body only when MERGE-writing into it
  (Step 5).
- **Metadata-first orchestrator; bodies read lazily.** Read `MEMORY.md` (index + hooks) and each
  memory's frontmatter (`name`/`description`/`metadata.type`) up front; that routes most memories.
  Read a full body only when the hook is too thin to route — and for a large dir, defer that to the
  per-slice sub-agents (below).
- **Scale by fanning out — one plan, still.** When the memory set is too large for one pass, you
  **MAY** split it into disjoint slices (~10–15 memories per agent, or by filename type-prefix
  `feedback_*` / `project_*` / `reference_*`) and dispatch a sub-agent per slice (via `Task`) to
  read the full bodies and classify against
  [knowledge-import-memory/memory-routing.md](.agents/skills/quenching-knowledge/references/knowledge-import-memory/memory-routing.md)
  §Routing table — memory `type` → likely home, each returning a partial table. The
  orchestrator stays metadata-first — it only reads `MEMORY.md` + frontmatter to draw the slice
  boundaries and merge partials; the sub-agents are the only readers of full bodies. Dispatch
  **classification** sub-agents with `model: sonnet`, `effort: low`. Consolidate the partials into
  the **one** plan — a single confirmation still gates every write and delete.
  Execution MAY likewise fan out per slice, each agent honoring write-then-verify-then-delete; **no
  agent deletes a memory before its doc lands and self-checks**. **Executor** sub-agents (the
  write-verify-delete leg) inherit the session model — never downgrade them: their self-check
  authorizes deleting a memory.
- **Delete only after the doc lands.** Write-then-verify-then-delete, per memory: a memory is
  removed **only after** its concept doc is written and passes the conformance self-check. A failed
  insert leaves that memory untouched.
- **Never silently drop a memory.** A `user` memory usually does **not** belong in shared repo
  docs — flag it and ask; migrate only a durable, team-relevant fact. Any memory with no
  documentary home **stays** in memory and is reported.
- **Content decides the home; `metadata.type` is a hint.** Route by what the fact IS
  ([knowledge-import-memory/memory-routing.md](.agents/skills/quenching-knowledge/references/knowledge-import-memory/memory-routing.md)
  §Routing table — memory `type` → likely home (content overrides)), reusing `/quenching-knowledge add`'s home
  boundaries. Split a memory that carries several facts into one concept per file.
- **Salvage, don't transcribe.** A memory is terse; the doc is structured. Keep the
  `**Why:**`/`**How to apply:**` prose in the body, derive `resource` honestly (never invent),
  resolve `[[links]]` to the migrated docs' paths.
- **Full OKF stamp, honest attribution.** Every doc gets a non-empty `type`, the OKF recommended
  fields, and the method labels; `source` defaults to "project memory"; an unproven rule enters
  `authority: background`.
- **Feed the glossary.** When a migrated memory introduces a repo-specific term, add its entry to
  `glossary.md` (the fixed A–Z lookup) as the tail of that memory's insert — the same
  step `/quenching-knowledge add`/`/quenching-knowledge learn` run — so the term is resolvable once the doc lands.
- **One resolver, both platforms.** The memory directory is derived from the **native** working
  directory in Python, never from `pwd` or shell string-munging (Windows Git Bash reports the MSYS
  form `/c/Users/…`, whose encoding names no directory). Resolution is by data — exact match, then
  nearest ancestor, then the candidate list — and **case-insensitive**.

Resolve `cq` per
[align/tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool; branch on the **exit code** (0 ok · 1 findings · 2 refusal), never on prose.

## Workflow

### 1. Locate the memory dir
The project's memory lives at `~/.claude/projects/<encoded-cwd>/memory/`, where `<encoded-cwd>` is
the **native** absolute working directory with every `\`, `/`, `:` and `.` replaced by `-`
(`c:\repos\app` → `c--repos-app`; `/home/me/repos/app` → `-home-me-repos-app`). Run the resolver
below **as-is** on every platform:

```bash
python3 - <<'PY'
import sys
from pathlib import Path
# the host replaces \ / : . with '-'. chr(92) IS the backslash, spelled this way so no
# quoting layer can eat the escape; Path.cwd() reports the native path on every platform.
PUNCT = set(chr(92) + "/:.")
enc = lambda p: "".join("-" if c in PUNCT else c for c in str(p))
cwd = Path.cwd().resolve()
# .lower(): a Windows drive letter's case is not stable (c:\ vs C:\), the rest is exact.
have = {d.name.lower(): d for d in (Path.home() / ".claude" / "projects").glob("*")
        if (d / "memory").is_dir()}
hit = None
for p in (cwd, *cwd.parents):   # nearest first: a worktree or subdir falls back to its checkout
    if enc(p).lower() in have:
        hit = (have[enc(p).lower()], "exact" if p == cwd else "ancestor " + str(p))
        break
if hit is None:
    print("NO MATCH for", enc(cwd))
    for k in sorted(have):
        print("  candidate:", have[k])
    sys.exit(1)
mem = hit[0] / "memory"
files = sorted(f.name for f in mem.glob("*.md"))
print("dir:", mem, "| matched:", hit[1], "| memories:", len(files))
for f in files:
    print("  ", f)
PY
```

Use `py - <<'PY'` where `python3` is not on PATH (common on Windows). Branch on what it printed,
never on a guess:
- **`matched: exact`** — proceed.
- **`matched: ancestor <path>`** — the cwd is a git **worktree** or a subdirectory; memory is keyed
  to the main checkout, which is what the walk found. Confirm the path names this repo before any
  deletion, since a parent directory could carry unrelated memory.
- **`NO MATCH`** — pick the candidate whose de-encoded name is this repo, or ask. Never invent one.
- **`memories: 0`** or no directory at all — report that and stop.
**Done when:** the exact memory directory and its scope are resolved, or the run stops without
deleting anything.

### 2. Take inventory (metadata-first)
Read `MEMORY.md` (the index) and, for each memory `.md`, its **frontmatter** (`name`,
`description`, `metadata.type`) and MEMORY.md hook. Do **not** read every body here — that is what
drowns a large dir at startup. Judge the volume: if the set is small enough to read+classify in one
pass, read the bodies inline as you go; if it is large (dozens of files, or any very large ones),
switch to the fan-out posture (see the *Scale* doctrine) — slice the dir and let a sub-agent
(`model: sonnet`, `effort: low`) read the bodies for each slice. Full bodies are salvaged either
inline (small dir) or by the per-slice sub-agent (large dir) — never all at once in the
orchestrator.
**Done when:** every memory has metadata, a body-reading route, and a bounded classification plan.

### 3. Classify each → destination + `type` + mold
First **orient, bounded** — only the required `Read`s and `Glob`s, no shell, so it behaves identically on
every platform. Never enumerate the whole tree (`catalog/` and `external/repositories/` will
overflow the session):

- `Read` — `/docs/standards/index.md` and `/docs/concepts/index.md` (a missing file means that
  home is empty). For what the `plans` phase already holds, run `.agents/quenching/bin/cq specs list --json`.
- `Glob` — `/docs/standards/*/index.md` and `/docs/concepts/*/index.md` for the existing subject
  folders, so a new concept path does not collide. One level only, and never a recursive file dump.

Then apply [knowledge-import-memory/memory-routing.md](.agents/skills/quenching-knowledge/references/knowledge-import-memory/memory-routing.md)
§Routing table — memory `type` → likely home (content overrides): map by content to its
destination, `type`, and mold, within the three destinations (Doctrine). Split multi-fact
memories. Mark `user` memories and any
unroutable fact as **KEEP (ask)** — not for deletion.
**Done when:** every memory has one destination/action or an explicit KEEP reason.

### 4. Present the migration plan
Show ONE table: `memory → target home → doc path (type) → action (migrate+delete | keep, ask)`.
Fan-out partials **merge into ONE table** — never one table per slice. Note any `[[link]]` that
will dangle. **Wait for a single confirmation** before writing anything.
**Done when:** the complete table is shown and its confirmation is settled.

### 5. Per memory: write, verify, then delete
For each **migrate** row that lands in `/docs/` (`standards/` / `concepts/`), run the full insert
procedure exactly as
[knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md)
§The frontmatter stamp §Updating `index.md` (the listing) §Enriching the glossary (tail step, every capture) §Self-check before finishing specifies it —
stamp → index → glossary → self-check (against
[knowledge-align/conformance.md](.agents/quenching/references/knowledge-align/conformance.md)
§Concept docs (`check_concept`) §Resource integrity (per-doc — every mode)) —
with this skill's deltas kept inline:
- `source` defaults to "project memory"; salvage the terse body into a structured doc.
- A **unit of work** row instead follows the `/quenching-specs create` path: run `.agents/quenching/bin/cq specs new
  <name>` and write the memory's content into `## Problem` and nothing else, then `.agents/quenching/bin/cq specs
  validate --spec <id>` — the ID the create reported — as the self-check per
  [specs-develop/spec-driven.md](.agents/quenching/references/specs-develop/spec-driven.md)
  §The spec document §The gates and the stage-scoped explicit-none rule
  (`.agents/quenching/bin/cq knowledge validate` never covers the specs front); carry the memory source in the spec's
  own record.
  **Never stamp an OKF `type:` on it** — a spec is not a concept doc, and never invent a
  `priority`: an unranked spec is `/quenching-specs triage`'s to place.
- **Only after the self-check passes:** delete the memory `.md` (`rm` — the one destructive shell
  this command runs, and the only reason `Bash(rm:*)` is granted) and prune its `- [..](..)` line
  from `MEMORY.md` with `Edit`. Pass the path the Step 1 resolver printed, quoted. A failed write
  leaves that memory untouched; a per-slice executor sub-agent honors the same contract.
**Done when:** every migrated memory is deleted only after its doc self-checks, and every kept or
failed row is named.

### 6. Report
Summarize: docs created (by home), memories deleted, and memories **kept** (with the reason —
`user`/unroutable/failed insert) so the user can decide on those. Leave `MEMORY.md` in place even
if it ends empty.
**Done when:** created docs, deleted memories, and kept memories with reasons are reported.

## Invariants to never violate

- Never delete a memory before its doc is written **and** passes the self-check.
- Never delete a `user` memory or an unroutable fact without the user's explicit say-so.
- Never fabricate a `resource` or `source`; never clobber a filled key on merge.
- Never add frontmatter to an `index.md`; keep every touched index honest.
- Never skip the single up-front plan+confirmation — this writes docs and deletes memory. A
  cycle-authorized run (convergence.md §The cycle-authorization contract) replaces the gate with narration; the plan is still
  presented in full and write-then-verify-then-delete still holds.
- Never write outside the three destinations (`standards/` + `concepts/` in `/docs/`, or a provider-owned spec)
  — re-route to the nearest, or flag-and-keep; never fabricate a
  `vision`/`documentation`/`external`/`catalog` doc from a memory.
- Fan-out never fractures the single up-front plan, never skips a memory, and never lets a
  sub-agent delete ahead of a landed, self-checked doc.
- When a migrated memory names a repo-specific term, feed `glossary.md` before deleting
  the memory — but never clobber a filled glossary entry, and keep it a one-liner + link.
- Never enumerate the whole bundle (`find docs -type f`) or descend into `catalog/` /
  `external/repositories/`. Never load every memory body into the orchestrator.
