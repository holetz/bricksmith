# `standards/quality/`

Data and model quality — per-row data checks, data drift/stability, model monitoring.

**Boundary:** *observing correctness* of data and models over time; the *modeling* that
defines the data lives in [../data-modeling/](../data-modeling/index.md); the model
lifecycle in [../mlops/](../mlops/index.md). One standard per file (files, not
sub-folders); each carries `type: standard` + a derived `resource:`; add each to
[../index.md](../index.md).

## Candidate sub-standards

Break this subject **one concept per file**. The method evaluates each candidate against
the repo, generates the applicable ones (`file:line`-anchored, full OKF frontmatter), and
records the rest below as deferrals (never a silent skip):
`row-checks` · `data-drift` · `model-monitoring` · `stability`.

## Coverage / deferred sub-standards

Per-subject ledger the verify gate reads. A subject is "done" only when every candidate is
**present or listed here** with a one-line why.

- _(none yet — fill on population)_
