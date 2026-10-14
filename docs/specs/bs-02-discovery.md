# BS-02. Discovery, identities and dependency graph

Slice of [Epic 0001](../epics/0001-bricksmith-library.md); [user guide](../guide/tasks-and-graph.md).

## Decisions closed

**Task declaration API and metadata format.** One keyword-only decorator on a module-level
function in a file under `catalogs/<catalog>/<schema>/**`:

```python
task(*, kind, name=None, outputs=None, reads=(), external=(), state=(), variants=(),
     enabled=True, static=False, metadata=None)
```

- `kind` is a free-form behaviour label (`table`, `train`, `monitor`...); it never selects a
  catalog, schema, folder or job. The folder gives the logical scope.
- `name` defaults to the function name. `outputs` are short names owned in the task's scope and
  default to the task name, so a task name and its outputs are independent.
- `reads` are other tasks' outputs, `external` are resources the project does not produce, `state`
  are earlier windows or versions of an output (the task's own included).
- `variants` is a list of parameter mappings; each yields its own task. `{param}` in outputs and
  references is replaced per variant.
- `metadata` is an open JSON mapping (write strategy, keys...). BS-02 only checks that it is plain
  data; the slices that consume a key validate it.
- `enabled` and `static` (output does not change per run) are carried for later slices. The
  decorator validates, attaches a `TaskDeclaration` and returns the function unchanged.

**Inference mechanism and dynamic-read policy.** Discovery parses source with `ast`; it never
imports a task module. It is not complete static inference and does not claim to be.

- Declaration values must be literals. A non-literal value is `non_literal_declaration`; when it
  affects identity (`kind`, `name`, `outputs`, `variants`) the task is not registered.
- The task's first parameter is its context. Literal calls to the read API `ctx.read(name)`,
  `ctx.read_external(name)` and `ctx.read_state(name)` (`bricksmith.tasks.TaskReads`) are inferred,
  in the task body, through plain aliases, and in functions the context is passed to when they are
  defined in the same module or reached by a relative import inside the project.
- A computed read name is `dynamic_read`: an error when the task declares no reads at all, an
  `info` coverage note otherwise. A context passed to a function discovery cannot follow is
  `unfollowed_helper` (`warning`, or `info` when reads are declared). Declarations and inferred
  reads are merged; nothing is guessed.
- Every edge carries `kind` (`data`, `external`, `temporal`) and `provenance` (`declared`,
  `inferred`) plus the file and line of the read.

**Identity and resolution.** A task is `catalog.schema.name`, plus `[param=value,...]` for a
variant. Physical names come only from the catalog and schema mappings (project file, overridden
by the `ExecutionContext`). A reference is `name` (same scope), `schema.name` (same catalog) or
`catalog.schema.name`, and resolves to an output, never to a task name and never to the same
short name in another scope. Temporal edges are kept but excluded from cycle detection and order.

## Public contract

- `bricksmith.tasks`: `task`, `TaskDeclaration`, `TaskReads`, `READ_METHODS`, `declaration_of`,
  `make_declaration`, `variant_key`. Standard library only; no discovery code is loaded.
- `bricksmith.tasks.project`: `load_project(root) -> Project`, `Exemption`. `bricksmith.toml`
  tables read here: `[tasks]` (`root`, `exclude` globs on project-relative paths),
  `[mappings.catalogs]`, `[mappings.schemas]`, `[[exemptions]]` (`rule`, `subject` with `*`
  wildcard, `reason`, required `expires` date).
- `bricksmith.tasks.discovery`: `discover(project) -> Discovery` (`TaskDefinition`, `ReadSite`).
- `bricksmith.graph`: `load_graph(root, *, context=None, today=None)`,
  `build_graph(discovery, project, *, context=None, today=None)` returning `TaskGraph` with
  `tasks: TaskNode`, `outputs: Output`, `edges: Edge`, `diagnostics: Diagnostic`; `ok`, `errors`,
  `task(id)`, `dependencies(id, kind=)`, `order()`, `to_dict`/`from_dict`, `to_json`/`from_json`.
  Building never raises for project findings; an invalid graph is retained whole.
- Diagnostic codes. Errors: `non_literal_declaration`, `invalid_declaration`, `invalid_scope`,
  `unreadable_module`, `duplicate_identity`, `output_collision`, `physical_collision`,
  `missing_producer`, `ambiguous_reference`, `cycle`, `unknown_placeholder`, `expired_exemption`,
  `dynamic_read`. Warnings: `unfollowed_helper`, `external_shadows_output`, `unused_exemption`.
  A live exemption turns a matching error into severity `exempt`; an expired one always fails.
- CLI: `bricksmith tasks list|graph|validate [--project DIR] [--json]`; `validate` exits 4 on
  errors. No command resolves a context or makes a remote call.

## Deviations and limits

- Provenance is two fields (`kind`, `provenance`) instead of one four-valued label. Outputs stay
  in the task's own scope; variants sharing one output name are an `output_collision`.
- Not followed: absolute imports, methods, classes, a context stored in a container or attribute,
  and module-level constants used as names. Physical collisions compare case-insensitively;
  schema mappings are keyed by schema name only, as in `ExecutionContext`.

## Acceptance tests

- `tests/unit/graph/test_graph.py` writes synthetic projects to `tmp_path`. One schema with table,
  train, predict and monitor tasks is discovered in a subprocess whose task modules import a heavy
  package and raise at import; none is imported or executed.
- Helper read with its own location, nested folders, renamed outputs, same local names in three
  scopes, no edge through an unrelated short name, dynamic reads, unfollowed helper, external
  input, earlier-window state that is not a cycle, ordinary cycles, missing producers.
- Duplicate identity, output and physical collisions (project and context mappings), live, expired
  and unused exemptions, variants, invalid declarations, exclusions, stable JSON round-trip, the
  runtime decorator and the three CLI commands.
