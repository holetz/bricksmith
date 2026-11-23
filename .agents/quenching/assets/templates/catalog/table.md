---
type: table             # OKF concept type — non-empty on every catalog concept
title: <system>.<catalog>.<schema>.<table>
description: detailed page of the <table> table — grain, key, columns, build
resource: <URI/FQN — e.g. uc://<catalog>.<schema>.<table>>
generated: { by: human:<author>, at: <ISO 8601 UTC — e.g. 2026-07-06T00:00:00Z> }
audience: both
authority: current
source: <metadata origin — UC, origin dictionary, curation>
maintainer: <owning team>
---

<!-- AUTO-GENERATED — do not edit (remove this line if the page is fully curated) -->

# `<table>`

`<system>.<catalog>.<schema>.<table>` · **grain:** `<one line>` · **key:** `<pk>`

System access: [`../access.md`](../access.md) · schema index: [`../<schema>.md`](../<schema>.md).

## Columns

| Column | Type | Description |
|---|---|---|
| `<col>` | `<type>` | `<description — leave blank if the origin does not expose it; fill via curation>` |

## Build

- **Script(s) that materialize it:** `<repo/folder/file>` — `<what it does>`.
- **Main transformation:** `<summary — upstream source → this table>`.

## Related

- **Upstream:** `<source tables/scripts>`.
- **Downstream:** `<who consumes this table>`.

<!-- /AUTO-GENERATED -->

## Curated observation

`<grain, unit, gotchas, usage — survives regeneration; the only section outside the
AUTO-GENERATED block in a generated page>`.
