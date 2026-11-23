---
type: schema            # OKF concept type — non-empty on every catalog concept
title: <system>.<catalog>.<schema> — consolidated index
description: consolidated one-row-per-table index of the <schema> schema
resource: <URI/FQN — e.g. uc://<catalog>.<schema>>
generated: { by: human:<author>, at: <ISO 8601 UTC — e.g. 2026-07-06T00:00:00Z> }
audience: both
authority: current
source: <metadata origin — UC, origin dictionary, curation>
maintainer: <owning team>
---

<!-- AUTO-GENERATED — do not edit (remove this line if the page is fully curated) -->

# `<system>.<catalog>.<schema>` — tables (consolidated)

> **SCHEMA mold / consolidated mode** → becomes `<catalog>/<schema>.md`. One line per table,
> **no** columns. Use when the origin does not expose column descriptions or the schema is
> large. Promote a table to a detailed page (`<schema>/<table>.md`) when descriptions exist.

System access: [`access.md`](access.md).

| Table | FQN | Grain / observation | Detail |
|---|---|---|---|
| `<table>` | `<system>.<catalog>.<schema>.<table>` | `<one line>` | [page](<schema>/<table>.md) · or — |

<!-- /AUTO-GENERATED -->

## Curated schema observation

`<general grain, use, context of the schema — survives regeneration; kept OUTSIDE the
AUTO-GENERATED block>`.
