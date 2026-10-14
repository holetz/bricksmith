# Roadmap

The implementation plan is [Epic 0001](https://github.com/holetz/bricksmith/blob/quenching/specs/5.md): 19 delivery slices,
their dependencies, implementation tasks, acceptance criteria and release scenarios. The
[capability review](planning/capability-review.md) explains which reference behaviors, documented
limitations and independent additions informed it. This roadmap summarizes that plan.

The release delivers one Python library, an updatable project template and a Claude Code plugin
that users may install optionally. See [delivery contracts](planning/delivery-contracts.md).
No private reference path or ignored evidence is required for implementation.

All waves grow the same `bricksmith` library with one version and release. Data, MLOps, quality
and monitoring tasks share discovery, dependency planning and execution. Dedicated catalogs,
schemas and folders are project choices, not requirements inferred from task type. See
[decision 0009](decisions/0009-one-library-shared-task-scopes.md).

Development starts from generic requirements and independent synthetic examples, without copying
private code, tests, vocabulary or history. The earlier monitoring history-import and
reference-derived naming-preset plans are retired by
[decision 0010](decisions/0010-behavior-derived-independent-development.md).

## Current baseline

The unified package, capability skeletons, CI configuration and private-content gate exist.
Local lint, type checking, 13 tests, source/wheel builds and an isolated wheel import check passed
when the package was consolidated. This is foundation evidence, not a working framework or proof
of remote CI, workspace authentication or deployment. Those remain BS-00 acceptance work.

## Delivery waves

| Wave | Slices | Delivers | Exit evidence |
|---|---|---|---|
| 0 | BS-00 | Clean builds, explicit free/corporate test profiles, primary Free Edition workspace, verified auth/platform matrix and cleanup harness | A minimal free-workspace job deploys where supported, runs and is destroyed; missing auth never selects another environment |
| 1 | BS-01, BS-02, BS-03, BS-07 | Explicit context, offline discovery, stable task/output identities, inference with explicit fallback, inspectable runner and neutral registry | Mixed task types in one schema plan offline; invalid edges and new configured registry violations fail clearly |
| 2 | BS-04, BS-05, BS-06, BS-08, BS-09 | Safe full/partition/incremental/SCD2 writes, schema migration, native jobs/resources, comparison, bounded SQL/volume I/O | A synthetic pipeline deploys and survives repeat runs, bounded backfill, drift checks and interrupted-transfer tests |
| 3 | BS-10, BS-11, BS-14 baseline, BS-15 | Frozen inputs, isolated revisions, model datasets with feature/label time contracts, operational evidence and governance state | A candidate is isolated and compared; invalid labels, target collisions and unenforced policies are visible |
| 4 | BS-12, BS-13, BS-14 integration | Model training/registration/prediction/lifecycle, independent monitoring metrics, persisted alerts and diagnostics | A model predicts from a pinned artifact and is monitored alongside ordinary data tasks; failure does not silently retrain |
| 5 | Complete BS-16, BS-18, BS-17 | Copier template/update, Claude Code plugin, stable catalog, documentation, compatibility and release proof | The project works with or without the plugin; all three artifacts and selected-workspace coverage have recorded evidence |

Each slice is a vertical delivery: public contract, example, meaningful tests, documentation and
an operating workflow. Platform behavior requires functional proof; pure checks run locally.
Template, documentation and agent skills start with the minimal pipeline in wave 2 and grow with
the following slices. A slice is complete only when its acceptance evidence passes.

## Release scope

The first release is 0.1.0. It includes the generic data/model workflow, one proven SQL connector,
explicit governance declaration/application/verification states, template and packaged Claude Code plugin.
Additional drivers, chat-provider adapters, report exporters, conversational data resources,
streaming and online serving are not implicit release requirements; see the epic's extension
backlog and open decisions.

Free Edition is the primary development/test environment where feasible. Corporate Databricks can
provide explicit supplementary proof of capabilities it does not support. Configure both profiles
locally if needed using [.env.example](../.env.example) and the
[test environment guide](development/databricks-tests.md). Creating `.env` is not remote setup
proof; authentication, compute, deployment and cleanup are verified separately.

Publishing or changing repository visibility is a separate decision. Its gate includes a
full-history scan with the private denylist. Nothing in this roadmap authorizes importing private
source history, moving private data or archiving another repository.
