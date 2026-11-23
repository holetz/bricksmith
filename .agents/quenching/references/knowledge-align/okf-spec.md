# OKF v0.2 — normative rules (condensed)

The **Open Knowledge Format** models a body of knowledge as a **bundle**: a directory tree
of markdown **concept documents**, plus reserved listing/history files. Source:
`GoogleCloudPlatform/open-knowledge-format/SPEC.md` (v0.2, the canonical repository; the older
`knowledge-catalog/okf/` copy is a frozen v0.1 snapshot). This file condenses the normative
rules the plugin enforces; [conformance.md](.agents/quenching/references/knowledge-align/conformance.md)
turns them into checks.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## Reserved filenames

- **`index.md`** — a directory **listing** (progressive disclosure). It MUST NOT be used for
  a concept document. It carries no frontmatter, except a bundle-root `index.md`, which MAY
  carry `okf_version`.
- **`log.md`** — an **update history**. Reserved by OKF; the OKF-strict profile **retires**
  it (§What this plugin adds on top) without unreserving it.
- Every **other** `.md` file is a **concept document**.

## Frontmatter

- **Required:** `type` — a short string naming the kind of concept. **Non-empty on every
  concept doc.** Producers pick the type; consumers tolerate unknown types.
- **Recommended:** `title` (display name) · `description` (one-sentence summary) · `resource`
  (URI uniquely identifying the underlying asset) · `tags` (YAML list) · `generated` (how the
  current content was produced).
- Producers **MAY** add any additional keys (this plugin's `audience`/`authority`/`source`/
  `maintainer`/`source_uri` are such extra keys). `source_uri` is written by
  `/quenching-knowledge import` alone; its contract — the exact-URI value, and why it is
  separate from the authorial prose in `source` — is owned by
  [sources.md](.agents/quenching/references/knowledge-import/sources.md) §Attribution.

## Provenance, trust, and lifecycle (all optional)

Every timestamp-valued key is an **ISO 8601 datetime with an explicit UTC offset**
(`2026-06-30T14:00:00Z`), not a bare date.

- **`generated: { by, at }`** — how the current content was produced. `by` is an actor (§Actor
  convention); `at` is the last meaningful change. **Supersedes v0.1 `timestamp`**; a v0.2
  consumer MAY fall back to a legacy `timestamp` when `generated` is absent.
- **`verified`** — one `{ by, at }` mapping or a list of them, one per independent check. Trust
  tiers derive from it: absent = **unverified**; non-`human:` actors only =
  **machine-confirmed**; any `human:<id>` = **human-reviewed**. Trust is advisory, never access
  control.
- **`sources`** — the materials a concept derives from. Each entry has a **`resource`** (URL,
  bundle path, or scope descriptor) and optional `id`, `title`, `author`, `usage_count`,
  `last_modified`. A source's `id` is the join key for a markdown footnote
  (`[^ga4-schema]`); per-claim attribution uses those footnotes, not a body `# Citations` list.
  `usage_window: { from, to }` frames every `usage_count`.
- **`status`** — `draft` | `stable` | `deprecated`. Absent means `stable`.
- **`stale_after`** — an absolute instant; the concept is stale when `now >= stale_after`.

## Concept `type`

- Type values are **not** registered centrally. Producers **SHOULD** pick descriptive,
  self-explanatory values; consumers **MUST** tolerate unknown types gracefully.
- This plugin fixes a **descriptive vocabulary** per home (`standard`, `system`, `schema`,
  `table`, `vision`, `documentation`, `concept`, `external`, `sidecar`) so the surface is
  uniform and greppable. `Attested Computation` is the v0.2 type that lives under `concepts/`
  (or a subject subfolder of it) when a repo needs a sanctioned, checkable computation.

## Attested Computation

A concept of `type: Attested Computation` carries the sanctioned way to compute a value, so a
consumer can confirm the number was produced by running it — provenance answers *where a claim
came from*, attestation answers *whether the run followed the blessed path*.

Required for the type: **`runtime`** (`bigquery`, `dbt`, `python`, `Looker`, …), which defines
what `parameters` mean. Optional: `parameters` (typed holes), `computation` (a path to the
computation file instead of the body `# Computation` fence), `executor` (how to run it; its
`resource` names instructions/code and `receipt` names the evidence fields), and `attester`
(deterministic, no-LLM code that inspects the receipt). The computation lives in a fenced block
under `# Computation` or in the `computation` file — never improvised by the consumer.

## Normative rules

**MUST**
- Reserved filenames MUST NOT be used for concept documents.
- Every non-reserved `.md` MUST contain **parseable YAML frontmatter**.
- Every frontmatter block MUST contain a **non-empty `type`**.
- Consumers MUST tolerate broken links, unknown types, unknown keys, and missing optional
  families; they MUST treat a bare `verified` mapping as a one-element list.

**SHOULD**
- Favor **structural markdown** — headings, lists, tables.
- `type` values SHOULD be descriptive and self-explanatory.
- Reserved filenames SHOULD be used where applicable (an `index.md` for a directory listing).
- Index entries SHOULD include the linked concept's `description`.
- Provenance SHOULD live in `sources` (with footnote attribution); a body `# Citations` list is
  the legacy v0.1 form.
- A concept SHOULD say how it was generated (`generated`) and, when confirmed, by whom
  (`verified`).

**MAY**
- A bundle MAY declare the OKF version via `okf_version: "0.2"` in the bundle-root `index.md`.
- `index.md` / `log.md` MAY appear at any level; a consumer MAY synthesize `index.md` on the fly.
- A concept MAY declare `status` and `stale_after`.
- A concept MAY be an `Attested Computation` with the contract fields above.

## Actor convention

Fields that record an identity (`generated.by`, `verified[].by`) use one convention:

- `<producer>/<version>` for agents and tools — `reference_agent/gemini-2.5-pro`;
- `human:<id>` for a person or a human-owned team — `human:ahormati`;
- `process:<id>` for an automated process — `process:finance-nightly`.

Consumers key trust tiers off the `human:` prefix, so hand-authored or human-confirmed content
MUST use it.

## Links

Two forms: **absolute (bundle-relative)** beginning with `/` (relative to the bundle root), and
**relative** markdown paths. Links assert a relationship; the specific meaning comes from the
surrounding prose.

## Bundle & conformance

```
bundle/
├── index.md   (optional; root may carry okf_version)
├── log.md     (optional)
├── <concept>.md
└── <subdir>/ …
```

A bundle **conforms** with v0.2 if: (1) every non-reserved `.md` has parseable YAML
frontmatter, (2) every frontmatter has a non-empty `type`, (3) reserved filenames follow their
structures. Consumers SHOULD NOT reject a bundle for missing optional fields, unknown types,
unknown keys, broken links, a missing `index.md`, or a missing optional family.

## Changes from v0.1

- **Breaking:** `timestamp` → `generated.at` (legacy fallback allowed); body `# Citations` →
  `sources` frontmatter (legacy parse allowed).
- **Additive:** `sources` and its credibility signals (`author`, `usage_count`,
  `last_modified`) plus `usage_window`; `generated`, `verified`; `status`, `stale_after`; the
  `Attested Computation` type and its `runtime`/`parameters`/`computation`/`executor`/`attester`
  keys; the `# Computation` body heading; the actor convention.

Everything else (bundle structure, reserved filenames, the required `type`, recommended
`title`/`description`/`resource`/`tags`, cross-linking, index/log files, permissive conformance)
is carried forward unchanged.

## What this plugin adds on top (OKF-strict profile)

OKF is permissive; this plugin narrows it into a portable **signature** (all still
OKF-valid — additive keys, descriptive types, reserved-file structures):

1. **`index.md` carries no frontmatter** — except the **root** `docs/index.md`, which carries
   **only** `okf_version: "0.2"`.
2. **`type` is mandatory and drawn from the fixed vocabulary** per home (see
   [taxonomy.md](.agents/quenching/references/knowledge-align/taxonomy.md)).
3. **`resource` is derived, never invented** — for standards a comma-separated **glob set** of
   what the doc governs (`*`/`**` only, repo-root-relative); for catalog/reference the asset URI.
   Empty is disallowed, and so is self-pointing (`resource-self`) — except a **bundle-level
   aggregate** whose scope contains the bundle root, which `glossary.md` legitimately
   is. A glob states what the doc governs and is the input the staleness check reads; a
   `file:line` states only where a rule happens to be written today.
4. **`log.md` is retired** — nothing in this plugin creates one, appends to one, or checks
   one. It stays a **reserved** name all the same: a log left over from an earlier alignment
   is still recognized, so it is never read as a malformed concept doc and never blocked.
   Unreserving it is a different and much worse change than retiring it — see
   `docs/standards/architecture/retiring-a-reserved-artifact.md`. Provenance that used to
   land here now lands in the archived spec's `## Outcome`.
5. **Links:** relative within a home, absolute `/docs/...` across homes.
6. **Canonical English structure** — folder names **and concept-doc file slugs**, keys, enum
   values, and the `type` vocabulary. Frontmatter is English; **body prose MAY follow the repo's
   language**. Identifier-derived slugs (catalog tables/schemas, repo names)
   stay **verbatim** — the slug is the greppable key to the asset.
   *(`standards/agents/communication.md` is the owner of that language rule for a bundle that has
   one. Every **normative** statement of it in this plugin was collapsed into a citation; the
   boundary reminders that survive do so by decision, under
   `standards/architecture/source-layout.md` §A boundary reminder is not a restatement. This one
   stays self-contained deliberately: a format spec is what other implementers read to build
   against, and one that defers to a doc living inside a particular repo's bundle stops being
   self-describing.)*
7. **Harness files** `AGENTS.md` (a legacy root `AGENTS.md` is its fallback) are navigation
   pointers, **not** OKF concepts —
   exempt from the `type` requirement. The validator skips them entirely; the `/quenching-components harness-align`
   skill keeps them thin and honest.
8. **Every knowledge-holding folder has an `index.md`, and listings do not lie.** The validator
   flags (WARN) a folder of concept docs with no `index.md`, a listing link to a nonexistent
   file, and a concept doc nothing links to. Still OKF-valid (these are SHOULDs the plugin
   surfaces; a consumer may ignore them).
9. **`generated: true` is this plugin's projection marker**, not the v0.2 `generated` family —
   the name collision is tolerated so an already-generated glossary page keeps validating. A
   hand-authored doc uses the v0.2 mapping (`generated: { by, at }`).
