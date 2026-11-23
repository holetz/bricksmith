# Sources — scoping, bounded ingestion, unit extraction, attribution, dedup

The **per-doc** procedure (classify → stamp → index → log → glossary → self-check) is **not**
here — it lives once in
[`docs-add/homes.md`](.agents/quenching/references/knowledge-add/homes.md), which this
skill cites.

## What a source can be

- **Local files/folders** — read with `Read`/`Glob`/`Grep`. Anchor each unit to `path:line`.
- **URLs** — fetched with `WebFetch`. Anchor each unit to the URL (+ heading/anchor when
  addressable).

## Bounded ingestion (discipline, not code)

<!-- rules -->

A source — especially a web source — is bounded **before** the first fetch:

- **Seed list.** The explicit set of files/URLs the user named. Ingest only what is on the seed
  list or reached from it under the rules below.
- **Host allowlist.** For web sources, the set of hosts a link may be followed to. Default:
  the hosts of the seed URLs only. A link to any other host is recorded, never fetched.
- **Page cap.** A hard maximum number of pages/files to fetch this run (state it in the plan).
  When the cap is hit, stop and **report what was left unfetched**.
- **One hop by default.** Follow links only one level from a seed, and only within the
  allowlist, up to the cap. Deeper crawls are a fresh, re-scoped run.
- **No auth walls, no downloads of binaries.** A binary (PDF/slide deck) becomes a `sidecar`
  extract per homes.md, not an ingested body.

State the seed list, allowlist, cap, and hop depth in the Step-3 plan so the user sees the
blast radius before the one OK.

<!-- rationale -->

This echoes the caps the upstream `enrich` enforced in code; here it is operator discipline.

## Extracting knowledge units

A **unit** is one concept — the granularity homes.md files as a single doc. Split the source
along its natural concept boundaries (a heading section, a table, a defined term, one rule,
one decision), not by page. Heuristics:

- One **rule / convention**, or a **decision with alternatives** → a `standards/` unit
  (unproven in the target's code ⇒ `authority: background`; there is no separate decision home).
- One **explanation / concept / term** → a `knowledge/` unit (+ a glossary entry if it names a
  repo-specific term).
- One **fact about a named external tool/lib/regulation** → a `reference/` unit.
- One **how-to / procedure** → a `how-to/` unit.
- **Data objects** (systems/schemas/tables) → `catalog/` units in the catalog path shape.

Keep units atomic — if a section carries two concepts, emit two units. Prefer **fewer,
well-scoped** units over shredding prose into fragments.

## Attribution

<!-- rules -->

Every minted doc records where it came from, in **two frontmatter fields that answer different
questions** — one written to be searched, one written to be read:

- **`source_uri:`** — the exact URI or path of the *source unit*. One value on one line: an
  absolute URL (`https://host/page#heading` when the heading is addressable) or the path as the
  user named the source, anchored where the unit starts (`docs/api/auth.md:120`). No prose, no
  parenthetical, no list.
- **`source:`** — unchanged, and still prose: which spec, person, or body of work originated the
  rule.

**Only `/quenching-knowledge import` writes `source_uri:`.** No other command mints it, infers it, or backfills
it, and a doc with no external origin simply has no such key.

The body keeps its attribution line when the source is a stable URL, and that line carries **the
date the source was read**. `source_uri:` records *where*, and `generated.at` (or a legacy
`timestamp`) already means something else entirely — the doc's own last change.

**OKF v0.2 provenance.** The spec's own provenance field is `sources:` — a list of
`{ id, resource, title, ... }` entries, with a body footnote per claim keyed to `id`. An import
SHOULD add one entry per source unit alongside `source_uri:`, and attribute claims with
`[^<id>]`; the body `# Citations` list is the v0.1 form, still readable but no longer the target.
`source_uri:` is the plugin's exact-match key for dedup, not a replacement for `sources`.

Never present imported material as proven local practice: that is the `authority: background`
rule.

<!-- rationale -->

`source_uri:` is the key a later run greps for an exact match on, so an approximate value is worse
than no value at all.

`source:` stays prose because a bundle's own authored docs already use it that way, and importing
must not overload one key with two meanings.

Inventing a `source_uri:` where there is no external origin fabricates a provenance that never
existed. The shared frontmatter mold in
[`docs-add/homes.md`](.agents/quenching/references/knowledge-add/homes.md) deliberately
does not carry it, so the commands citing that mold are never invited to fill it in.

A URI with no age ages badly: a reader deciding whether a dead link matters needs to know how old
the reading was, and that costs a sentence in the body rather than a second key to parse.

## Dedup — within the source and against the bundle

<!-- rules -->

1. **Within the source.** Collapse two sections describing the same concept into one unit
   (merge their detail).
2. **Against the existing bundle.** Before minting, look for the doc that already covers this
   unit — in this order:

   1. **Exact, by origin.** `grep -rn 'source_uri: <the unit's URI>' knowledge/`. A hit is a doc
      minted from *this very unit* — on an earlier run, or earlier in this one when a previous
      slice already minted it. Two **overlapping seeds** are not this case: they carry different
      URIs, so this lookup can never match them.
   2. **Approximate, by concept.** No hit → `Grep` the bundle by title, slug, and any
      repo-specific term. A doc found this way covers the same concept from a *different* origin,
      or from none at all, so calling it the same concept is a judgement — present it as one.
   3. **Contradiction, by concept.** A doc found in (2) whose content the unit asserts the
      opposite of is a **contradicts** verdict, a judgement like (2). It blocks the MERGE: the
      unit is never fused into that doc and never overwrites it. It is its own plan item showing
      both passages, entering only on the human's choice, else going to the residue.

   Otherwise, either way the unit is a **MERGE** target — enrich that doc (fill missing keys, add detail,
   add the source link), never a second doc for the same concept. Record every MERGE-vs-mint
   decision in the plan, **and which of the two lookups produced it**.
3. **Glossary.** A term the source defines that the glossary already lists is a MERGE into the
   existing entry (sharpen/keep the link), per homes.md §Enriching the glossary.

<!-- rationale -->

The two lookups run in that order because they do not carry the same confidence. On the exact
one, nothing was recognised and nothing was judged: the URIs are equal or they are not. Two
overlapping seeds carry different URIs, and item 1 above has already collapsed them by prose
before it runs.

Recording which of the two lookups produced each MERGE-vs-mint decision is what lets the human
read an exact match and a resemblance as the different claims they are.

## Fan-out for scale

<!-- rules -->

For a large source, one `Task` sub-agent per **source slice** (a folder, a URL group)
returns **compact unit candidates** — `{home, type, path, one-line, source-anchor}`, never
full bodies. The orchestrator merges the candidate lists, runs the cross-slice dedup above,
and builds the single plan. Extraction/executor sub-agents may run on a **cheap model/effort**.
See the model policy in [README.md §model-policy](.agents/quenching/README.md#model-policy).

<!-- rationale -->

A cheap model is safe here because enrich deletes nothing, so a misclassification only misfiles a
doc (a correctable move), unlike `/quenching-knowledge import-memory` where a misclassification deletes
a memory.
