# `how-to/` — task recipes

User guides that answer "how do I do X" with bricksmith, one capability per page (`type: how-to`).

## Pages

* [Agents](agents.md) — How to use the three read-only omnigent agent bundles, their minimal grants and their dry run.
* [Bundles and jobs](bundles-and-jobs.md) — How to define Databricks bundle resources and jobs in Python.
* [Offline catalog](catalog.md) — How to render an offline catalog of tables, models and lineage with bricksmith catalog render.
* [Context and command line](context-and-cli.md) — How the immutable execution context and the command line select one explicit Databricks environment.
* [Isolated experiments](experiments.md) — How to try a change in an isolated experiment revision without touching baseline outputs.
* [External inputs, connectors and volume artifacts](external-io.md) — How to move small, bounded data between tasks and external inputs, SQL connectors and volume artifacts.
* [Feature store](feature-store.md) — How to declare closing-based feature tables on tasks and build training and scoring sets by exact closing.
* [Governance](governance.md) — How to declare, plan and apply tags, grants and metadata for managed resources.
* [Model datasets](model-datasets.md) — How to build population, feature, label and training or prediction datasets as ordinary tasks.
* [Models: training, registry and lifecycle](models.md) — How to train, evaluate, register and promote models with ordinary tasks.
* [Monitoring: references, drift, model quality and alerts](monitoring.md) — How to monitor references, drift, model quality and alerts from tables.
* [Operations: events, reports, readiness, alerts and budgets](operations.md) — How to record run events and use reports, readiness checks, alerts and budgets.
* [Comparing data and checking quality](quality.md) — How to compare data and check quality with offline plans and bounded samples.
* [Registry and naming checks](registry.md) — How to validate a column registry and naming rules offline.
* [Planning, running and replaying tasks](running.md) — How to plan, run and replay tasks from a readable execution plan.
* [Schema evolution and maintenance](schema-evolution.md) — How to declare a target table, plan its Delta migration and run maintenance.
* [Data stability](stability.md) — How to declare data-stability checks on tasks and read their dashboard-ready results.
* [Tasks and the dependency graph](tasks-and-graph.md) — How to declare tasks and how bricksmith builds their dependency graph.
* [Writing tables from tasks](writes.md) — How to declare how a task output is written to Spark and Delta tables.
