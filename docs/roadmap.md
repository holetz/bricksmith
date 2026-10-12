# Roadmap

Every phase is a vertical slice: the code, an example that uses it, a functional test on
Databricks and the agent skill that operates it. A phase is done when its exit criterion passes
in CI, not when its code is written.

| Phase | Delivers | Exit criterion |
|---|---|---|
| 0a | Private repository, uv workspace with four empty distributions, unit CI, leak gate, decision records | CI green on `main`; the leak gate blocks a seeded finding |
| 0b | Databricks Free Edition workspace, CI service principal and secrets, pinned CLI and `databricks-bundles` | `select 1` through Databricks Connect serverless and a "hello" job deployed from CI with resources defined in Python |
| 1 | Core walking skeleton: catalogs, schemas, tasks (full and date-partitioned), folder discovery, inferred dependencies, the task runner and `load_resources` | A bronze → silver → gold example deploys, runs and is destroyed end to end |
| 2 | `bricksmith-registry` wired into table writes (COMMENT, NOT NULL, CHECK), the `check` CLI, the naming and description skills | The ratchet fails the build on a seeded violation in the example |
| 3 | Copier template; CI renders it and runs the example from the render | A new project deploys with no manual edit |
| 4 | `bricksmith-monitor` (successor of `psi_spark`, imported with history): pure DataFrame core, optional persistence | PSI computed on Free Edition; the old repository archived with a pointer |
| 5 | `bricksmith-mlops`: the model chain, Unity Catalog registration, construction skills from sampling to training | A synthetic model trained, registered and monitored end to end |
| 6 | Evaluation, change, retirement and documentation skills (model card), standards shipped as skeletons, docs site | The whole flow is covered by skills; release 0.1.0 |

Going public is a separate decision taken after phase 6, with a full-history leak scan as its
gate.
