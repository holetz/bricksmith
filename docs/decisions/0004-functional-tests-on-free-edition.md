# 0004. Functional tests run on Databricks Free Edition

- Status: accepted (authentication to be confirmed in phase 0b)
- Date: 2026-10-11

## Context

The framework has to be proven against a real workspace, and the examples double as usage
documentation. Databricks Free Edition is available at no cost, with constraints documented as of
2026-09-29:

- serverless compute only, no classic clusters;
- at most five concurrent tasks per account;
- one SQL warehouse, size 2X-Small;
- one active pipeline per pipeline type;
- a daily fair-usage quota whose numbers are not published; exceeding it shuts compute down for
  the rest of the day;
- non-commercial use only; no account console and no account-level APIs.

## Decision

Three test layers:

| Layer | Scope | When |
|---|---|---|
| Unit | Registry, naming checks, `load_resources` snapshots, dependency inference, planning | Every pull request, no workspace |
| Functional | Table writes, SCD2, COMMENT and CHECK, MLflow registration, monitoring metrics, through Databricks Connect serverless with one schema per run | Push to `main` and on demand |
| End to end | Render the template, `bundle deploy -t ci`, run the example jobs, assert on the data, `bundle destroy` | Nightly and on demand |

- Functional and end-to-end runs share a single concurrency group and are not required checks on
  pull requests, so a quota hit never blocks review.
- Each run writes to its own schema and drops it, whatever the outcome.
- Example data is synthetic and generated inside the job from a fixed seed: no egress, no
  dependency on sample catalogs.
- CI authenticates with a service principal over OAuth machine-to-machine; GitHub OIDC federation
  needs account-level APIs Free Edition does not expose. A personal access token is the fallback
  if the service principal route fails in practice.
- The framework defaults to serverless; classic compute is opt-in.

## Consequences

- Examples never need more than five parallel tasks, and tests avoid the SQL warehouse where a
  Databricks Connect session will do.
- The documentation states that Free Edition is for development and demonstration only.
