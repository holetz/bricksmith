# BS-03. Inspectable execution and reprocessing

Slice of [Epic 0001](../epics/0001-bricksmith-library.md); [user guide](../guide/running.md).

## Decisions closed

**Semantics left open by BS-02.** `enabled=False` means the task never executes: it appears in a plan
as `skip` ("disabled"), and a task that needs its output makes the closure incomplete. `static=True`
means the output is not recomputed as part of a closure: a static task that is not a root is `skip`
("static output; not recomputed") unless `--include-static`; a static root runs. Skipped producers
satisfy their dependents (their outputs are assumed to exist).

**Selection.** Roots are task ids or `fnmatch` patterns (empty means every task; a pattern without
`[...]` also matches variants). The closure follows `data` edges upstream (default) and optionally
downstream; temporal edges never extend it. `include`/`exclude` filter the closure, roots included.
Order is the graph's deterministic topological order. A task also becomes `skip` when listed in `done`.

**Incomplete closure.** A runnable task whose data producer is absent from the plan (filtered, not
selected) or disabled is an `error` issue (`missing_upstream`, `disabled_upstream`); `--partial` records
the same findings as warnings. Unknown roots, an empty selection and an invalid graph are errors too. A
plan with errors can be printed and reviewed but never starts.

**Plan.** `Plan(selection, graph_fingerprint, entries, issues, grants)`; each `PlanEntry` carries the
action, reason, data dependencies, the read bindings (reference to physical object) and the targets
(physical object, declared operations, `protected`). Operations come from `metadata.operations`, else the
`metadata.write` strategy, else `write`; `metadata.protected: true` marks a protected output. JSON is
sorted; `fingerprint` hashes everything except grants, and `from_dict` rejects content that does not match
it. `bricksmith.plan` never imports the runner or any task module.

**Authorization (BS-03.4).** A grant is `(physical object, operations)` inside one reviewed plan:
`Plan.authorize` rejects objects that are not targets of running tasks and undeclared operations, and
stamps the plan fingerprint. Every operation on every protected target that runs needs its own exact
grant, so authorizing one protected output never covers its protected upstream. Applying also rejects
grants stamped for another plan or naming objects outside the plan. Write-time guard repeats the check.

**Stale plans.** `validate_plan` re-plans from the stored selection against the current graph and context
and raises `StalePlanError` when the graph fingerprint (identities, flags, outputs, physical names,
edges; not line numbers) or any entry differs. Limit: task source changes that keep the declared graph
do not make a plan stale.

**State machine.** Each plan entry ends in one state: `skipped` (plan said skip), `succeeded`, `failed`
(after the retry policy), `blocked` (a dependency failed, was blocked or not run; transitive; lists
`blocked_by`) or `not-run` (dry-run, or `--fail-fast` stopped scheduling). Independent branches keep
running. Retries: `RetryPolicy(max_attempts, delay, backoff)`; `BricksmithError` failures are not retried.

**Runtime context.** `TaskContext` implements `TaskReads` plus `write(name, data, *, operation=None)`.
Reads resolve only references the plan bound to the task; writes only the task's own targets and
declared operations. Table access goes through `TableIO.read(physical)` and
`TableIO.write(physical, data, *, operation)`; `InMemoryTableIO` is the test fake and
`default_table_io(context)` returns a backend that fails on use until BS-04 supplies Spark/Delta.
The task's return value is ignored; the task writes explicitly.

**Dry-run.** Validates the plan and grants, imports no module, calls no task, writes nothing; run entries
are `not-run` and list their declared effects.

**Replay.** `replay_plan(plan, report, graph)` roots a new plan at the report's failed, blocked and
not-run tasks, marks succeeded tasks `done`, keeps filters and static handling, and needs its own review
and grants. The report must name the plan's fingerprint.

## Public contract

- `bricksmith.plan`: `build_plan`, `validate_plan`, `check_authorization`, `Plan`, `PlanEntry`, `Target`,
  `ReadBinding`, `Selection`, `Issue`, `Authorization`.
- `bricksmith.runner`: `run_plan`, `apply_plan`, `replay_plan`, `RetryPolicy`, `RunReport`, `TaskResult`,
  `TaskContext`, `TableIO`, `InMemoryTableIO`, `run_entrypoint`; `python -m bricksmith.runner.entrypoint`.
- CLI: `bricksmith run plan|apply|replay`; exit 4 for an invalid plan, 5 (`EXIT_RUN_FAILED`) when tasks fail.
- Errors: `PlanError`, `StalePlanError`, `AuthorizationError` (all `ValidationError`).

## Deviations

- `TaskNode.from_dict` already kept `metadata`; BS-02's contract is unchanged. New exit code 5.
- Task modules are imported under a private package name with namespace parents so relative imports work.

## Acceptance tests

`tests/unit/execution/test_execution.py`: closure and reasons, filters, incomplete closure and invalid
graph never start, stable JSON and tamper check, dry-run, real-loader run, failure blocking, fail-fast,
replay, stale plan, protected-output authorization, concurrency bound and order, retries, CLI, entrypoint.
