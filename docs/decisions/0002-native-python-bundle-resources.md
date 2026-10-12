# 0002. Bundle resources are built natively in Python

- Status: accepted
- Date: 2026-10-11

## Context

In this framework Python is the source of truth: tasks are discovered from folders and their
dependencies are inferred from the code that reads them. The reference implementation generated
`job.yml`, `schema.yml` and a task manifest from Python and committed them. That intermediate
step produced its own class of bugs (a build that needed two passes before the generated files
agreed with the code), large diffs on every change and drift between code and YAML.

Python support in Declarative Automation Bundles (`databricks-bundles`) is generally available and
covers jobs, schemas, volumes and catalogs, among others.

## Decision

The core exposes `load_resources(bundle)`, which runs discovery and returns native `Job`,
`Schema` and `Volume` resources. `databricks.yml` keeps only the bundle, variables, targets and the
`python:` section. Per-target variation comes from `bundle.target` in Python. Nothing generated is
committed.

## Consequences

- Discovery runs on every `bundle validate`, `deploy` and `run`. Measured on the reference
  implementation: 8.7 s for 1,142 tasks (2.1 s for catalogs and schemas, 6.6 s for importing tasks
  and inferring dependencies). A project with tens of tasks should stay well under a second; at
  that scale a cache keyed by a source hash is warranted.
- Pull requests no longer show a YAML diff. A snapshot of `load_resources` output for the examples
  lives in the tests, and a `plan` command prints jobs, tasks and dependencies.
- Deploying needs a virtual environment with the framework importable, and the Databricks CLI and
  `databricks-bundles` move in lockstep at the same pinned version.
- The load phase may only add resources and mutators may only modify them; the framework fits
  that model.
- Resources still in Beta are YAML-only in the bundle schema, so the template keeps a place for a
  hand-written YAML resource.
