---
type: concept
title: Glossary
description: The repo's single A–Z lookup of terms, acronyms, and domain vocabulary — one entry per term, each linking to its full concept doc when one exists.
resource: /docs/**
tags: [glossary, vocabulary, terminology]
generated: { by: quenching/6.13.0, at: 2026-07-25T00:00:00Z }
audience: both
authority: current
source: quenching skeleton
maintainer: <the team>
---

# Glossary

The repository's **single source of truth for what a term means here**. One entry per
term, in the same bullet syntax every `index.md` uses: `* [<Term>](<path>.md) — <one-sentence
definition>` when a full concept doc exists, or `* **<Term>** — <one-sentence definition>`
when it doesn't — the glossary is the *index* of vocabulary, not the long-form home.

**Resolving a term.** When a repo-specific word, acronym, or piece of jargon is unclear,
**search this file first** (Ctrl-F, or `grep -i '<term>' /docs/glossary.md`). A
matching entry gives the local meaning and, when linked, points to the doc that explains
it in full. No entry means the term is not yet defined — capture it (see *How to enrich*).

**This file is the ONE deliberate exception to "one concept per file."** A glossary is
inherently a multi-term aggregate — a flat bullet list, not a concept doc per term. It is
also the one exception to an `index.md`'s "only list what exists" rule: an **unlinked**
entry (a term with no concept doc yet) is a normal, permanent, valid state, not a defect.
Keep the list **alphabetically sorted by Term**, keep each definition to a single
sentence, and **link out** rather than explaining in full here.

**Its `resource: /docs/**` is deliberate, and must not be "corrected".** A glossary
governs the whole bundle, so a scope that names the whole bundle is the honest one —
narrowing it to look tidier would be a fabrication. The validator knows: a scope
containing the bundle root is a **bundle aggregate**, exempt from `resource-self` and
from the resource-activity figure (which would otherwise read fresh forever, since the
doc sits inside the scope it measures). Every other doc pointing at itself is still a
real finding.

## Terms

- **_(seed — replace with this repo's first term)_** — _one-sentence meaning in this repo,
  linked to its concept doc once one exists_

## How to enrich

Add a term whenever a repo-specific word, acronym, or piece of jargon surfaces that a
newcomer would not know. Three ways in:

- **Automatically, as a tail of a capture.** The `quenching` knowledge skills
  (`quenching-knowledge-learn`, `quenching-knowledge-add`, `quenching-knowledge-import-memory`) each check, at
  the end of a capture, whether the new concept introduced a term that belongs here, and
  add or update the entry — linking it to the concept doc just written.
- **On demand, one term at a time.** Run `quenching-knowledge-define` to add or refine a single
  entry (inserted in alphabetical position, MERGE — never clobbering a filled definition).
- **In bulk, across the whole bundle.** Run `quenching-knowledge-glossary-backfill` to sweep every doc
  already in `/docs/` for repo-specific terms that were never fed into the glossary and
  backfill them in one pass.

Keep entries honest: define the term as **this repo** uses it, not the dictionary sense,
and let the linked doc carry the depth.

## Published projection

The glossary remains canonical here and is copied into the bounded `site-source/` input by
`.agents/quenching/bin/cq knowledge site-source`; there is no second editable route to keep in step. Only the abbreviation snippet used for tooltips is
derived from this file; never maintain a second editable list of terms there. The site layer turns
each canonical entry into Python-Markdown `*[term]: definition` syntax, so terms such as `ASRC` and
`chave conformada` gain tooltips without authors marking occurrences by hand.
