# `standards/mlops/`

Model lifecycle — training/serving lifecycle, lineage, experiment tracking, model
serving, and the interface to regulatory requirements.

**Boundary:** *our* implementation of the model lifecycle; the external regulation itself
(the PDF) lives in [reference/regulations/](/docs/external/regulations/index.md) — this
home holds *how we implement it*. One standard per file (files, not sub-folders); each
carries `type: standard` + a derived `resource:`; add each to [../index.md](../index.md).

## Candidate sub-standards

Break this subject **one concept per file**. The method evaluates each candidate against
the repo, generates the applicable ones (`file:line`-anchored, full OKF frontmatter), and
records the rest below as deferrals (never a silent skip):
`model-lifecycle` · `lineage` · `experiment-tracking` · `regulatory-interface` · `serving`.

## Coverage / deferred sub-standards

Per-subject ledger the verify gate reads. A subject is "done" only when every candidate is
**present or listed here** with a one-line why.

- _(none yet — fill on population)_
