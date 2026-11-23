# Conformance — the exact checks

The executable `.agents/quenching/bin/cq knowledge validate` implements **exactly** these
checks; the skills apply the same rules by hand when they cannot shell out, and their self-check
steps **cite this file** rather than restating the rules. Severities: **ERROR** fails conformance
(validator exit 1); **WARN** is a recommendation (exit 0 unless `warnAsError`).

## File classification (by basename)

<!-- rules -->

| Basename | Kind | Checked as |
| --- | --- | --- |
| `index.md` | reserved listing | `check_index` |
| `log.md` | reserved, **retired** | nothing — recognized, never judged (see below) |
| `AGENTS.md` (legacy `AGENTS.md`) | harness pointer | **exempt** (skipped; honesty checked by `/quenching-components harness-align`, not the validator) |
| `README.md` | migration nudge | WARN "convert to index.md" |
| any other `*.md` | concept doc | `check_concept` |

## Concept docs (`check_concept`)

<!-- rules -->

- **ERROR `no-frontmatter`** — no `---` YAML block at the top.
- **ERROR `broken-frontmatter`** — opens `---` but never closes.
- **ERROR `missing-type`** — frontmatter has no non-empty `type`.
- **WARN `missing-<field>`** — a recommended field is absent: `title`, `description`,
  `resource`, `generated`. A legacy `timestamp` satisfies the recency recommendation (OKF v0.2
  fallback) and is never reported as missing.

### OKF v0.2 optional families (`check_concept`)

These fire only when the family is **present**; absence is not a finding. All are **WARN** —
they narrow the spec's SHOULDs, they are not conformance errors.

- **`generated`** — must be a mapping with a non-empty `by` (actor) and, when present, an `at`
  that is an ISO 8601 datetime with an explicit UTC offset.
  - `generated-malformed` — not a mapping, no `by`, or a `verified` entry without `by`/`at`.
  - `generated-by-invalid` / `generated-at-invalid` — actor or datetime outside the convention.
  - `generated: true` is the plugin's projection marker, not the family: it is ignored here.
- **`verified`** — one `{ by, at }` mapping or a list of them; a bare mapping is a one-element
  list.
  - `verified-malformed`, `verified-by-invalid`, `verified-at-invalid`.
- **`status`** — `draft` | `stable` | `deprecated` (`status-invalid`).
- **`stale_after`** — ISO 8601 datetime (`stale-after-invalid`).
- **`sources`** — a list of mappings, each with a non-empty `resource`; duplicate `id` warn;
  `last_modified` must be an ISO 8601 datetime. `usage_window` must be a `{ from, to }` pair.
  - `sources-malformed`, `source-missing-resource`, `source-id-duplicate`,
    `source-last-modified-invalid`, `usage-window-invalid`.
- **`Attested Computation`** — `runtime` is required for the type; `executor`/`attester`, when
  present, are mappings with a non-empty `resource` (`attested-computation-no-runtime`,
  `attested-computation-malformed`).

## `index.md` (`check_index`)

<!-- rules -->

- **ERROR `index-has-type`** — carries a concept `type` (an index is a listing, not a concept).
- **Non-root** `index.md`:
  - **ERROR `index-has-frontmatter`** — carries **any** frontmatter (must be a bare listing).
- **Root** `index.md` (at the bundle root):
  - **WARN `root-no-okf-version`** — does not declare `okf_version`.
  - **WARN `root-okf-version-mismatch`** — declares a version this plugin neither writes nor
    reads (it writes `0.2` and still reads `0.1`).
  - **WARN `root-extra-keys`** — carries keys other than `okf_version`.

## `log.md` (retired — no checks)

<!-- rules -->

No codes. The validator recognizes the name and emits nothing about the file. Retired is not
unreserved.

<!-- rationale -->

**The reservation is what makes that true**, and it is load-bearing in a way the silence hides:
drop `log.md` from the validator's `RESERVED` tuple and every log surviving in an already-aligned
bundle falls through to `check_concept` — `missing-type` at ERROR, and denied writes under the hard
gate. `RetiredLogChecker` in `tests/test_knowledge.py` holds the line with a fixture bundle carrying two surviving logs.

## Bundle level

<!-- rules -->

- **WARN `bundle-no-index`** — the bundle root has no `index.md`.

## Pre-rename layout (whole-tree — every mode; ERROR)

<!-- rules -->

A target that has not run `/quenching-knowledge align` since a plugin release renamed one of its own
declared roots (the bundle root moved twice: `.docs/` → `.knowledge/` → `docs/` — see
[migration.md](.agents/quenching/references/knowledge-align/migration.md) §1g). Each
finding looks at exactly one site and is **idempotent** — migrating that one site clears it, whether
or not the others have migrated yet. **ERROR**, decided in `renomear-docs-para-knowledge` `##
Open Decisions`: nothing enforces it as a block — the plugin's self-installed enforcement hook, the
one thing that could have denied a write on it, was retired.

- **ERROR `okf-legacy-root`** — the new root is absent and a pre-rename root (`.docs/`,
  `.knowledge/`) sits where it should be. One finding per former name found, each independent.
- **ERROR `okf-legacy-home`** — a pre-rename home name (`knowledge/`, `reference/`,
  `explanation/`) sits at the bundle root instead of its canonical name (`concepts/`, `external/`,
  `documentation/` — migration.md §1c-ter).
- **ERROR `okf-legacy-documentation-home`** — a `documentation/` that still holds the retired
  wrapper's quadrant subfolders, instead of `tutorials/`, `how-to/` and `project/` at the bundle
  root and the explanation pages flat in `documentation/` (migration.md §1c-bis).
- **ERROR `okf-legacy-doc-quadrant`** — a pre-rename Diátaxis quadrant (`getting-started/`,
  `concepts/`) sits under the wrapper; its pages belong in `tutorials/` and `documentation/`.
- **ERROR `okf-legacy-glossary`** — `glossary.md` sits inside a home instead of at the bundle root.

**ERROR `not-an-okf-bundle`** is the sibling of these, not one of them: it fires on a directory
that exists but carries no OKF **signature** (a root `index.md` declaring `okf_version`), and it
**withholds every per-file OKF verdict** for that tree — `no-frontmatter`, `broken-frontmatter`,
`missing-type`, the recommended-field WARNs and the `index-*` frontmatter rules. Structural and
`okf-legacy-*` findings still fire, because those are what `align` reads to migrate the target.
The gate exists because the root is named `docs/`: a name no repository holds by accident became
the commonest documentation folder there is, and without it a target's ordinary `docs/` answers a
validate run with one ERROR per document instead of one about the tree.

## Structural integrity (whole-tree — CLI + `Stop` only)

<!-- rules -->

Deterministic directory/index checks the validator runs over the **whole tree**. All **WARN**; the
**skills treat them as must-fix** in their own verify gate. Dirs whose name starts with `_` or `.`,
and asset dirs (`img/`, `assets/`, `static/`, `node_modules/`, `__pycache__/`, …), are pruned from
this walk.

- **WARN `dir-no-index`** — a directory directly holds ≥1 concept doc but has no `index.md`
  listing (the concrete "índice faltando": a subject folder like `standards/code/` with docs and
  no front door). Every folder that holds knowledge gets a regenerated `index.md`.
- **WARN `index-broken-link`** — an `index.md` links to a `.md` file or subfolder that does not
  exist on disk (a "lying index"). Only **within-bundle** links written in the bundle's own form
  are judged; external URLs, anchors, non-`.md` assets, `../`-climbs that leave the bundle, and
  repo-absolute `/…` links (e.g. `/.claude/…`) are ignored.
- **WARN `index-orphan`** — a concept doc that **no** `.md` in the bundle links to (unlisted / not
  reachable by browsing). The fix is to add it to its folder's `index.md` (or the derived
  standards zone). Link language/wording is **not** machine-checked — the English-slug rule is a
  skill-applied convention (the validator cannot reliably detect a document's natural language).
- **WARN `glossary-broken-link`** — the same link rule applied to the bundle-root `glossary.md`, whose
  links **are** its content: an entry pointing at a deleted doc is a dead lookup. It needs its own
  code because `index-broken-link` is only ever judged on an `index.md`, and the glossary is a
  concept doc. Same resolver, so the two never diverge on what a link means.
- **WARN `generated-listing-missing`** — a `standards/**` doc no row inside the
  `<!-- BEGIN GENERATED -->` zone of `standards/index.md` links. Distinct from `index-orphan`,
  which any sibling citation disarms: a doc cited elsewhere and absent from the listing is
  unreachable by browsing and passes every other check. The fix is to regenerate the zone.
- **WARN `generated-listing-drift`** — a row inside that zone whose text no longer equals the
  `description:` of the doc it links (whitespace-insensitive). The zone is derived from disk, so
  the comparison is exact rather than a judgement; the fix is the same regeneration.

## Resource integrity (per-doc — every mode)

<!-- rules -->

A doc that is provably **lying about itself**. These join the structural set the skills treat as
**must-fix** in their verify gate.

- **WARN `resource-unresolved`** — a path- or glob-shaped `resource` entry matching nothing on
  disk, reported **per entry** so a comma-separated list names which one is broken. A `uri` entry
  is never resolved, and an entry carrying glob syntax the validator does not implement (braces,
  character classes, `?`) is classified `unknown` and **never reported** — only `*` and `**` are
  implemented, and flagging syntax nobody writes would make the must-fix set unusable.
- **WARN `resource-self`** — the doc's own path falls inside the scope its `resource` declares.
  Such a doc governs nothing, so its activity is unmeasurable and the figure says so.
  Matching is **segment-wise**: a single `*` does not cross a `/`, so `/docs/*` does not contain
  a deeper path like `/docs/standards/<subject>.md`.
  - **The bundle-aggregate exemption.** An entry whose scope contains the bundle **root** is an
    aggregate, not a mistake, and never raises this. The bundle-root `glossary.md` really does govern
    the whole bundle, so `resource: /docs/**` is truthful and narrowing it would be the
    fabrication. This is `TYPES_WITHOUT_RESOURCE` generalized — one exemption mechanism, not two.

## Resource activity (CLI only — a figure, never a finding)

<!-- rules -->

- **`.agents/quenching/bin/cq knowledge validate <bundle> --activity`** prints, per doc and **per `resource:` entry**,
  the stamped `generated.at` (or the legacy `timestamp`), the last commit touching that entry
  (`git log -1 --format=%cI` with explicit **`:(glob)`** pathspec magic, so a single `*` does not
  cross a `/` here either) and the interval between them, widest first. `--json` gives the same
  rows as data.
- **No finding code appears on any line, and none is emitted anywhere.** There is no interval that
  is a failure, and the command exits 0 whatever it prints.
- A doc whose `resource:` resolves to nothing measurable — every entry a `uri`, an `unknown` or a
  bundle aggregate — is printed as **scope not measured**, never dropped.

It shells out to `git` once per `resource:` entry. A tree that is not a git checkout **measures nothing**
rather than reporting a finding it cannot compute.

<!-- rationale -->

Shelling out to `git` per doc is fine on demand, which is the only way this check ever runs now
that the plugin's self-installed enforcement hook — the one caller with a latency deadline to miss
— was retired.

## Running it

<!-- rules -->

```bash
.agents/quenching/bin/cq knowledge validate <repo>/docs        # human report; exit 0/1
.agents/quenching/bin/cq knowledge validate <repo>/docs --json # machine-readable findings
```

Config block `okfValidate` in `hooks-config.json` (`warnAsError`, plus `ignoreGlobs` alongside it)
tunes the CLI's own behaviour — treating a warning as a failure, and pruning regenerable/vendored
paths from the scan.

## Verify gate (Step 5 of /quenching-knowledge align)

<!-- rules -->

A bundle is **aligned** when `.agents/quenching/bin/cq knowledge validate docs` exits 0 **and** the structural-integrity and
resource-integrity WARNs are all cleared — **zero** `dir-no-index`, `index-broken-link`,
`index-orphan`, `glossary-broken-link`, `generated-listing-missing`, `generated-listing-drift`,
`resource-unresolved`, `resource-self`. (These are WARN,
so they do not fail exit-0; the skill reads them from `--json` and treats them as blocking.)
**Resource activity is not part of this gate**, and cannot be: it emits no finding to gate on.
The skill also confirms the method-level completeness the validator can't see:
applicable homes present, each standards subject's **coverage/deferral ledger** filled (every
candidate present or listed), and `backlog/index.md`'s task listing rebuilt exclusively from
the frontmatter on disk.

**`standards/index.md`'s "Current docs" zone left that prose list on 2026-08-17.** Its membership
and its descriptions are now decided by `generated-listing-missing`/`generated-listing-drift`,
which are in the blocking set above — so the gate no longer claims a match a human was asked to
eyeball. A clause a program can evaluate is never handed back to prose.

<!-- rationale -->

The structural set is **WARN** because OKF says a consumer MUST tolerate broken links and MAY
synthesize a missing `index.md`, so these never fail conformance. The resource set is `WARN`
because OKF does not govern `resource` at all, blocking because the plugin does.

Resource activity emits nothing because the comparison cannot support a verdict. The integrity
codes describe a doc that is **provably** wrong; a doc whose scope has seen commits may be
perfectly correct, because code moves under a rule that did not change. This was a `stale-doc`
WARN until 2026-08-27, when it was retired: a `resource:` glob is deliberately wide, so what the
comparison detects is activity in the radius of the glob and not drift of the content. Measured
the day it went: **50 of the 95 docs** in this repo's own bundle carried it, and a signal more
than half the corpus raises is scrolled past — taking the findings printed beside it along.

The numbers survive as a figure precisely because they are worth having: a reader asking which
docs sit next to the most movement gets a ranking. What no longer happens is being told a doc is
wrong on evidence that cannot say so.
