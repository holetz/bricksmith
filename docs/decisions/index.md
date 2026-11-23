# `decisions/` — why a contract is the way it is

Architecture decision records: the context, the decision and its consequences. One decision per
file, `type: decision`, numbered `NNNN-<slug>.md` in the order written. A decision is append-only:
a later one supersedes or amends it by saying so, never by rewriting it.

## Pages

* [0001. One repository, four distributions, one namespace](0001-one-repository-four-distributions.md) — Superseded plan to ship the framework, registry, MLOps and monitoring as four distributions under one namespace.
* [0002. Bundle resources are built natively in Python](0002-native-python-bundle-resources.md) — Why Databricks bundle resources are built natively in Python instead of generated YAML.
* [0003. Private first, with a leak gate](0003-private-first-with-a-leak-gate.md) — Why the repository started private and guards every commit and push with a leak gate.
* [0004. Free-first functional tests with supplementary corporate proof](0004-functional-tests-on-free-edition.md) — Why functional tests run first on Databricks Free Edition, with corporate proof only for unavailable features.
* [0005. The project template is rendered with copier](0005-copier-template.md) — Why the project template is rendered and updated with copier.
* [0006. English everywhere](0006-english-everywhere.md) — Why code, identifiers, docs, specs and commit messages are written in English.
* [0007. The registry splits mechanism, preset and seed](0007-registry-mechanism-presets-seed.md) — Why the column registry separates the checking mechanism, the naming preset and the seed data.
* [0008. bricksmith monitoring succeeds `psi_spark`](0008-monitor-succeeds-psi-spark.md) — Why bricksmith monitoring succeeds the standalone psi_spark binning and stability repository.
* [0009. One library with growing capabilities and shared task scopes](0009-one-library-shared-task-scopes.md) — Why bricksmith is one library whose data, MLOps and monitoring tasks share discovery, graph and runner.
* [0010. Independently implement behavior-derived requirements](0010-behavior-derived-independent-development.md) — Why requirements are implemented independently from generic behavior, never translated from the private reference.
* [0011. Three delivery surfaces and explicit test environments](0011-delivery-surfaces-and-test-environments.md) — Why the product ships as a library, a template and a plugin, tested against one explicit environment at a time.
* [0012. Model monitoring is declared on tasks and stored in trusted run tables](0012-model-monitoring-declared-on-tasks.md) — Why model monitoring runs in the shared runner from a task declaration and writes its own run-keyed tables.
