# `standards/naming/`

**Data** naming — tables, columns, descriptions, schemas/catalogs. The globally unique,
predictable name of the data.

**Boundary:** `naming/` governs **data**; **code** symbol naming lives in
[../code/](../code/index.md); the modeling behind the data (grain/key/joins) lives in
[../data-modeling/](../data-modeling/index.md). One standard per file (files, not
sub-folders); each carries `type: standard` + a derived `resource:`; add each to
[../index.md](../index.md).

## Candidate sub-standards

Break this subject **one concept per file**. The method evaluates each candidate against
the repo, generates the applicable ones (`file:line`-anchored, full OKF frontmatter), and
records the rest below as deferrals (never a silent skip):
`tables` · `columns` · `descriptions` · `schemas-catalogs`.

## Coverage / deferred sub-standards

Per-subject ledger the verify gate reads. A subject is "done" only when every candidate is
**present or listed here** with a one-line why.

- _(none yet — fill on population)_
