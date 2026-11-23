---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*), Bash(py:*)
---

# /quenching-knowledge status — read the bundle, change nothing

**Input**: `$ARGUMENTS` (optionally a home or path to focus; omit to read the whole bundle).

The **read-only** view of the `docs` front, and the honest preview of what
`/quenching-knowledge align` would do: the plan you would be authorizing, before you authorize it.

The conformance codes and their severities live in
[knowledge-align/conformance.md](.agents/quenching/references/knowledge-align/conformance.md);
the finding → owning-command routing in
[knowledge-align/cycle.md](.agents/quenching/references/knowledge-align/cycle.md);
the homes and the insert procedure in
[knowledge-add/homes.md](.agents/quenching/references/knowledge-add/homes.md).

## Doctrine

- **Zero writes, no exceptions.** No stamp or index regeneration.
- **Report in the validator's vocabulary.** Every finding carries the code
  [conformance.md](.agents/quenching/references/knowledge-align/conformance.md) defines and the command that
  closes it. Never invent a code, never soften one, and never report a finding the sweep would not
  raise — the value is that the two agree.
- **Density is reported, never coded as a finding.** Empty homes, thin homes, and a one-entry
  glossary appear as a **table of figures with no code** (§5).
- **Distinguish "would fix" from "would only report".** Split the output the way the sweeps split
  it: what `/quenching-knowledge align` fixes on one OK, what its later stages then drive, and what
  neither closes because it needs a human.
- **Cheap by construction.** One `.agents/quenching/bin/cq knowledge validate --json`, one glob, and reads of the files the report names.
- **Never judge, never rank, never infer.** An empty home is not a defect, an unlinked glossary
  entry is a valid permanent state, and resource activity is a figure — never a verdict that a
  doc is wrong.

## Workflow (one read, one report)

### 1. Resolve the bundle
Resolve the bundle at its fixed root `/docs/` at the repo root — the root is never read from
config. Resolve `.agents/quenching/bin/cq knowledge` per
[align/tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool. Invoke via `python3`/`py`; branch on the **exit code** and the `--json`,
never on prose.

**No bundle at all** is a complete, valid answer: report that `/docs/` is absent and that
`/quenching-knowledge align` would install it, then stop. A `/docs/` that exists without an `okf_version` root
`index.md` is an un-installed tree, not a broken bundle — say which.
**Done when:** the bundle root and the checker are resolved, or their absence recorded.

### 2. Collect (read-only)
- `.agents/quenching/bin/cq knowledge status docs --json` — the status payload, a superset of validate: `findings`, `findingCodes`, `ok`, `controls`, `density`. It is the only source of the controls report (§4 item 6) and of every conformance finding.
- `.agents/quenching/bin/cq knowledge validate docs --activity --json` — the resource-activity figure, per doc and
  per `resource:` entry (CLI only; per [conformance.md](.agents/quenching/references/knowledge-align/conformance.md)
  §Resource activity it emits no finding and never runs in the hook path).
- `Glob` `/docs/**/*.md` for the density counts (the payload's `density` is not used in its place), and read `/docs/index.md`, each home's `index.md`,
  and `/docs/glossary.md`.
- `Glob` `~/.claude/projects/<cwd>/memory/*.md` and read the root `AGENTS.md` size (and a legacy root
  `AGENTS.md`'s when it carries more than `@AGENTS.md`) —
  the two out-of-band stores whose content the cycle would pull in.
- `Glob` `/docs/**/log.md` — a figure for §5, never a finding. If `$ARGUMENTS` names a home or
  path, restrict the density and named document reads to that scope while keeping the validator's
  result whole-bundle.

If the checker is unavailable, collect what the frontmatter supports and mark
every conformance row as unverified rather than reporting a clean bundle.
**Done when:** every source is read and nothing has been written.

### 3. Classify against the sweeps' own codes
Map each finding onto a code from
[conformance.md](.agents/quenching/references/knowledge-align/conformance.md), and route it with
[cycle.md](.agents/quenching/references/knowledge-align/cycle.md)'s routing table — which
already says, per row, whether the align auto-closes it. A row the table does not cover is reported under "closed by neither" with
the reason.

Keep the severity split the contract draws: the **must-fix** WARNs (`dir-no-index`,
`index-broken-link`, `index-orphan`, `glossary-broken-link`, `resource-unresolved`,
`resource-self`) are what a verify gate blocks on; **resource activity is not a finding at all**,
so it is never reported as though it were one.
**Done when:** every observation carries a code and a routing.

### 4. Report
One report, in this order:

1. **Header** — the resolved bundle root, whether it is an installed OKF bundle (`okf_version` on
   the root `index.md`), and the checker's version and exit code verbatim.
2. **Density** — the table §5 defines, *before* the findings.
3. **Would be fixed by `/quenching-knowledge align`** — the codes
   [cycle.md](.agents/quenching/references/knowledge-align/cycle.md)'s table marks auto-closed by
   stage 1 (`dir-no-index`, `index-broken-link`, `index-orphan`, un-stamped or mis-stamped
   frontmatter, variant folder names, prefix-clusters, non-English slugs), with counts. Name which
   would be **code-coupled** and so confirm on its own. This list is what a single OK would
   authorize.
4. **Would then be pulled in by `/quenching-knowledge align`'s later stages** — the content they carry: undrained `~/.claude` memory files (count), durable knowledge still inlined in the harness,
   and terms in the bundle absent from the glossary. These are the rows the table marks auto-closed
   by `/quenching-knowledge import-memory`, `/quenching-components harness-align` and
   `/quenching-knowledge glossary-backfill`.
5. **Closed by neither** — every row the table marks **No**, each with the command that closes it:
   `resource-unresolved` and `resource-self` (→ `/quenching-knowledge add` to restamp),
   `glossary-broken-link` (→ `/quenching-knowledge define`), coverage-ledger deferrals, and
   anything a human has not yet stated. Report the widest few resource-activity intervals as a
   figure in their own line — commits in the radius of a `resource:` glob, never drift of the doc.
6. **Catalog controls** — the `controls` block of the §2 status payload, never inferred when the payload is missing (report the controls as unverified) — each catalog control id, its requirement ids with `present`, `missing`, `not-measured`, `refused` or `retired`, and the evidence paths. `present` is candidate evidence, never conformance; the block never changes `ok` or the exit code.

Close with the single most useful next command for this repo's actual state, and nothing else.

**A section with nothing in it is reported as empty, never omitted.**
**Done when:** all six sections are reported and no file has changed.

### 5. The density table — figures, not findings

Density says whether the bundle *knows anything*, beyond whether it is well-formed. Report one
table, and give **no row a finding code**:

| Figure | How it is counted |
| --- | --- |
| Concept docs, total | `.md` files that are not `index.md` or an EXEMPT basename (`AGENTS.md`, `AGENTS.md`) |
| Concept docs **per home** | the same count, grouped by top-level home, with **installed-but-empty homes shown as `0`** — never omitted, since the zero is the signal |
| Glossary terms | entries under `## Terms` in `glossary.md`; note separately when the shipped **seed placeholder** is still the only one |
| Unlinked glossary entries | terms with no concept doc yet — a **valid permanent state**, reported as a figure and never as a defect |
| Standards subjects | subject subfolders under `standards/`, and how many hold at least one doc |
| Last activity | the newest `generated.at` (or legacy `timestamp`) across the bundle's concept docs |
| Retired `log.md` | how many survive, and where — see below |

**A surviving `log.md` is a figure, not a defect.** The artifact is retired: no command or
validator code touches it. Report where it is and that keeping or deleting it is the repo's call.

**Never infer a target.** There is no correct number of concept docs, no minimum glossary size and
no ratio to hit; a bundle with four docs may be complete.

**Done when:** the table is reported with every home present, zeros included, and no row carries a
code.

## Invariants to never violate

- Never write, anywhere, for any reason. Name the command that fixes what looks wrong and stop.
- Never report a finding with a code the validator does not define, and never state a finding the
  sweep would not raise.
- Never give a density figure a finding code, and never present an empty home as a defect.
- Never call a resource-activity interval a violation or fold it into the must-fix set.
- Never report a bundle as conformant on a run where the checker could not be resolved.
- Never fan out sub-agents, and never hand this command file `context: fork` when it is invoked as a
  sweep's preview — the report has to land in the conversation where the OK will be given.
