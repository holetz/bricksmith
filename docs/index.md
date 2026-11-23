---
okf_version: "0.2"
---

# `docs/` — bricksmith knowledge bundle

The documentation of bricksmith, kept as an Open Knowledge Format (OKF v0.2) bundle. Each home has
a fixed name and a single purpose. Folder names and frontmatter keys are canonical English
kebab-case.

## Homes

* [how-to/](how-to/index.md) — user guides: how to do a task with bricksmith
* [decisions/](decisions/index.md) — architecture decision records: why a contract is the way it is
* [project/](project/index.md) — the manual for this repository: development, tests, specifications, delivery and evidence
* [vision/](vision/index.md) — direction and delivery waves

## Boundaries

- `how-to/` serves a bricksmith user with a goal; `project/` serves a contributor to this repository.
- `decisions/` keeps the history behind a rule; it is append-only.
- Specifications, epics and tasks live on the `quenching` git branch (`cq specs`), outside this
  bundle — see [project/specifications.md](project/specifications.md).
