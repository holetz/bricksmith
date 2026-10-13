# 0009. One library with growing capabilities and shared task scopes

- Status: accepted
- Source adoption and default-preset policy amended by
  [0010](0010-behavior-derived-independent-development.md) on 2026-10-13
- Date: 2026-10-13
- Supersedes: [0001](0001-one-repository-four-distributions.md)
- Amends: [0007](0007-registry-mechanism-presets-seed.md) and
  [0008](0008-monitor-succeeds-psi-spark.md)

## Context

The initial plan separated the framework, registry, MLOps and monitoring into four distributions
with enforced dependency directions. The project still contains only package skeletons, so those
boundaries add packaging and release work before there is a concrete need for independent
products. Capability boundaries also should not dictate how a data project groups its tasks:
feature preparation, training, prediction and monitoring can belong to the same domain and schema.

## Decision

- Ship one `bricksmith` distribution from `src/bricksmith`, with one version, wheel and release.
  Add capabilities to this library as the roadmap progresses.
- Keep internal modules when they make code easier to understand. The current `dab`, `registry`,
  `mlops` and `monitor` modules are organizational starting points, not independently released
  products. Use shared abstractions and imports where useful, without the former capability-wide
  dependency prohibitions; avoid circular imports.
- Use one task model, discovery mechanism, dependency graph, runner and bundle integration for
  data, MLOps, monitoring and future capabilities. Specialized task behavior extends these common
  abstractions instead of creating a separate execution hierarchy.
- Derive catalog and schema from the project tree, as before. Task type describes behavior and
  does not require or implicitly select a catalog, schema, folder or job specific to a capability.
  Different task types may coexist in the same scope and depend on one another.
- Let projects choose dedicated scopes for ownership, permissions or readability. Such scopes
  use the same discovery and execution rules and may have dependencies across scopes.
- Keep imports lightweight. Registry checks must still run without Spark, and monitoring metric
  computation remains DataFrame in, DataFrame out. Heavy external integrations may be optional
  extras of the same distribution, loaded only by the code that needs them. Extras do not control
  task placement or create separate bricksmith distributions.
- Retain the registry's mechanism, preset and seed separation from decision 0007, now within
  `bricksmith.registry`. The planned CLI is `bricksmith check`, supplied by the single library.
- Implement monitoring within `src/bricksmith/monitor` from independent contracts under decision
  0010, replacing the earlier source/history-import plan. Within a project, ordinary tasks persist
  monitoring results so their dependencies remain visible. Any persistence adapter for outside
  use also belongs to this library.

## Consequences

- Installation and upgrades use one dependency. The root `pyproject.toml` is the library manifest;
  the repository no longer needs a multi-distribution uv workspace or a PEP 420 namespace.
- Tests for namespace splitting and forbidden capability imports are removed. Package tests and
  builds verify the installed library; future task tests must verify mixed scopes and dependencies.
- The template and examples must demonstrate both shared and optionally dedicated scopes. In
  particular, the MLOps example must train, predict and monitor alongside ordinary data tasks in
  one schema, with the same dependency graph.
- Roadmap phases deliver capabilities of one library. Sharing a release couples their versions;
  an independent distribution would require a new decision justified by an actual use case.
