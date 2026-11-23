# `standards/data-modeling/`

How the data is modeled — grain, keys, joins, catalog/schema choice, historization. The
"database" decisions that shape the tables.

**Boundary:** the *shape* of the data (grain/key/joins); the *names* live in
[../naming/](../naming/index.md); the physical catalog of tables lives in
[catalog/](/docs/catalog/index.md). One standard per file (files, not sub-folders); each
carries `type: standard` + a derived `resource:`; add each to [../index.md](../index.md).

## Candidate sub-standards

Break this subject **one concept per file**. The method evaluates each candidate against
the repo, generates the applicable ones (`file:line`-anchored, full OKF frontmatter), and
records the rest below as deferrals (never a silent skip):
`grain` · `keys` · `joins` · `schema-catalog-choice` · `historization`.

## Coverage / deferred sub-standards

Per-subject ledger the verify gate reads. A subject is "done" only when every candidate is
**present or listed here** with a one-line why.

- _(none yet — fill on population)_
