# `standards/ci-cd/`

Build and deploy — how code becomes a running artifact, "code defines YAML", manifest
generation, pipeline stages, versioning/release.

**Boundary:** the *build/deploy* path; the job/task framework that runs the work lives in
[../workflows/](../workflows/index.md); deploy *targets* and governance live in
[../platform/](../platform/index.md). One standard per file (files, not sub-folders); each
carries `type: standard` + a derived `resource:`; add each to [../index.md](../index.md).

## Candidate sub-standards

Break this subject **one concept per file**. The method evaluates each candidate against
the repo, generates the applicable ones (`file:line`-anchored, full OKF frontmatter), and
records the rest below as deferrals (never a silent skip):
`build` · `deploy` · `manifest-generation` · `pipeline-stages` · `versioning-release`.

## Coverage / deferred sub-standards

Per-subject ledger the verify gate reads. A subject is "done" only when every candidate is
**present or listed here** with a one-line why.

- _(none yet — fill on population)_
