# Planning, running and replaying tasks

Every run starts from a plan you can read before anything executes.

```bash
bricksmith run plan --task main.sales.order_totals --out plan.json     # executes nothing
bricksmith run apply --plan plan.json --dry-run                          # validate, list effects
bricksmith run apply --plan plan.json \
    --authorize main.sales.clean_orders:write --authorize main.sales.order_totals:merge
bricksmith run replay --plan plan.json --report-in report.json --apply  # only the undone work
```

Add `--json` for stable machine output. `--task` accepts ids or patterns, `--include`/`--exclude`
filter, `--no-upstream`/`--downstream` change the closure, `--include-static` recomputes static
tasks and `--partial` accepts an incomplete closure as warnings.

## What a plan says

Each task is `run` or `skip` with a reason (`disabled`, static output, done in a previous run), in
deterministic order, with its read bindings and its targets: physical object, declared operations and
whether it is protected. Declare operations with `metadata={"operations": [...]}` (or `write`), and
protect an output with `metadata={"protected": True}`. A plan with errors (unknown root, missing or
disabled upstream, invalid graph) is shown but never starts.

## Authorization and stale plans

Protected targets need an explicit `OBJECT:OPERATION` grant per object; authorizing an output does not
authorize its upstream. Grants are stamped with the plan fingerprint. If the graph, physical names or
targets changed since review, `apply` fails with `stale_plan`; plan again.

## Results

Task states are `succeeded`, `failed`, `blocked` (a dependency did not succeed), `skipped` and
`not-run`. Independent branches keep running; `--fail-fast` stops new work. `--retries N` retries
failing tasks (not `BricksmithError`s). `--concurrency` bounds parallel tasks. The report
(`--report FILE`) is JSON; replay plans only failed, blocked and not-run tasks and needs new grants.

## In a task and in a job

A task receives a context with `read`, `read_external`, `read_state` and `write(name, data,
operation=None)`; writes go only to the task's own targets. A job task runs
`python -m bricksmith.runner.entrypoint --task <id> --no-upstream --env <name>`, with the same options
and the same explicit context as the CLI. The Spark/Delta table backend arrives with a later slice.
