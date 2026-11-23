# `standards/code/`

Code conventions — imports, formatting/lint, typing, **symbol** naming, dependency
pins, error handling, logging, docstrings, testing conventions.

**Boundary:** `code/` governs **symbols** (code); **data** naming (tables/columns) lives
in [../naming/](../naming/index.md). One standard per file — flat files by default, but when a
subject fractures into a cluster of siblings (e.g. symbol naming across classes / functions /
modules / variables), fold it into a subfolder (`symbol-naming/{classes,functions,…}.md`) with
its own `index.md`, rather than a run of `symbol-naming-*.md` files. Each standard carries
`type: standard` + a derived `resource:`; add each to [../index.md](../index.md).

## Candidate sub-standards

Break this subject **one concept per file**. The method evaluates each candidate against
the repo, generates the applicable ones (`file:line`-anchored, full OKF frontmatter), and
records the rest below as deferrals (never a silent skip):
`imports` · `format-lint` · `typing` · `symbol-naming` · `dependencies-pins` · `error-handling` · `logging` · `docstrings` · `testing-conventions`.

## Coverage / deferred sub-standards

Per-subject ledger the verify gate reads. A subject is "done" only when every candidate is
**present or listed here** with a one-line why.

- _(none yet — fill on population)_
