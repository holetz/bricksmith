---
type: system            # OKF concept type — non-empty on every catalog concept
title: <system alias> — access card
description: how to connect to <system> and where its build scripts live
resource: <URI/FQN of the system — e.g. jdbc:postgresql://<host>/<db> or uc://<catalog>>
engine: <postgres | sqlserver | databricks-uc | bigquery | ...>
generated: { by: human:<author>, at: <ISO 8601 UTC — e.g. 2026-07-06T00:00:00Z> }
audience: both
authority: current
source: <where metadata comes from — UC, origin dictionary, curation>
maintainer: <team/person owning the access>
---

# `<system alias>` — `<engine>`

> **SYSTEM mold** → becomes `catalog/<system>/access.md` (the sibling `index.md` is the
> reserved listing). The LLM reads **this card** before touching any table in this system:
> how to connect, where the scripts are, what catalog granularity exists.

## Access

- **Engine:** `<postgres | sqlserver | databricks-uc | bigquery>`
- **Endpoint:** `<host:port>` · **catalog/database:** `<name>`
- **Mechanism:** `<JDBC | SQL warehouse | Spark Connect | native client>` —
  `<connection string/template, WITHOUT secrets>`
- **Credential:** reference to `<secret scope / vault / env var>` — **never** paste the
  password/token here.
- **Network:** `<VPN? whitelisted IP? maintenance window? notes>`

## Build scripts

- **Where they live:** `<repo/folder>` — scripts that build/transform the tables here.
- **How a table points to its script:** `<convention — e.g. the table page links the path
  in its "Build" section>`.
- **Related pipelines/jobs:** `<DAGs, jobs, schedules that touch this system>`.

## This system's catalog

- **Available granularity:** `<detailed (column descriptions available) | consolidated (no
  descriptions)>` — decides whether tables become detailed pages or stay in the index.
- **Catalogs/databases:** `<list>` → each has its `<schema>.md` (consolidated) and/or
  `<schema>/<table>.md` (detailed).

## Where to find more

- `<links — standards/data-modeling, origin dictionary, access runbook, system owner>`.
