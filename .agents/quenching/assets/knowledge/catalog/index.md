# `catalog/` — data / domain catalog

A navigable map of the **data we touch** — internal and from external systems
(PostgreSQL, SQL Server, Databricks/Unity Catalog, BigQuery…). It exists to **orient an
LLM**: what each table means, **how to access** the source system, **which scripts** build
it, and **where to look** for more.

**Boundary:** `catalog/` = "the **data** we produce/consume". Distinct from
[external/](/docs/external/index.md) (external tools/libs/regulations) and
[standards/](/docs/standards/index.md) (our contracts). The modeling behind the data
(grain/key/joins) lives in [standards/data-modeling/](/docs/standards/data-modeling/index.md);
table/column naming in [standards/naming/](/docs/standards/naming/index.md).

## Hierarchy: system → catalog → schema → table

```
catalog/
  <system>/                 # physical origin
    index.md                # reserved listing of the system's catalogs/schemas (NO type)
    access.md               # the ACCESS CARD concept — engine, connection, scripts (type: system)
    <catalog>/              # catalog (UC) / database (postgres/sqlserver) / project (bigquery)
      <schema>.md           # CONSOLIDATED schema index — one row per table (type: schema)
      <schema>/
        <table>.md          # DETAILED page — grain, key, columns, scripts (type: table)
```

- **`<system>/index.md`** is the reserved listing (no frontmatter) — it links `access.md`
  and lists the catalogs/schemas. **`<system>/access.md`** carries `type: system` — the
  access card (engine, connection, **secret by reference**, never the credential) and where
  the build scripts live. (OKF-strict keeps `index.md` a pure listing, so the system's
  concept metadata lives in `access.md`.)
- **`<schema>.md`** carries `type: schema` — consolidated, one line per table, no columns.
- **`<schema>/<table>.md`** carries `type: table` — the detailed page.
- The `<system>/<catalog>/<schema>/<table>` segments use the **real system names** (do not
  translate); only the canonical home names in `/docs/` are English kebab-case.

## Two granularities

**Consolidated** (`<schema>.md`) when the origin does not expose descriptions or only a map
is needed; **detailed** (`<schema>/<table>.md`) when descriptions exist and the table is
queried. Promote consolidated → detailed when descriptions appear; never create an empty
detailed page.

## Generated × curated

When an official source describes the tables (Unity Catalog, manifest, dictionary), the
content is **AUTO-GENERATED** (never edited by hand — the `<!-- AUTO-GENERATED -->` block is
overwritten on the next build). **Curation** (grain/usage notes, missing descriptions,
access card, script links) is versioned separately and **survives** regeneration.

Molds: `system.md` / `schema.md` / `table.md` (applied by `quenching-knowledge-add`). Seed this home
by inserting a `<system>/index.md` first.
