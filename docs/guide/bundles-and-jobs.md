# Bundles and jobs

bricksmith defines bundle resources in Python; nothing is generated to YAML. The project's
`databricks.yml` keeps the bundle, variables and targets, and points at the loader:

```yaml
bundle:
  name: my_project
python:
  venv_path: .venv
  resources:
    - "bricksmith.dab:load_resources"
targets:
  dev:
    mode: development
```

The Databricks CLI and `databricks-bundles` (extra `bundle`) must be installed together at the same
version. Resources not covered by Python (for example Beta kinds) stay hand-written in YAML; list them in
`preserved` so validation can warn about key collisions. bricksmith never reads, changes or deletes them.

## Declare resources and jobs

Everything lives in `bricksmith.toml`:

```toml
[bundle]
wheel = "dist/bricksmith-*.whl"      # the dependency of every bricksmith task (serverless environment)
environment_version = "3"
manage_schemas = true                # schemas of discovered scopes are bundle resources
manage_catalogs = false              # catalogs are only referenced unless you opt in
preserved = ["jobs.legacy_export"]

[bundle.schemas."main.sales"]
owner = "analysts"                   # becomes an ALL_PRIVILEGES grant
targets = ["prod"]                   # omit to deploy to every target

[bundle.volumes.landing]
schema = "main.sales"

[jobs.nightly]
select = ["main.sales.*"]            # task ids or patterns; exclude = [...] drops tasks
schedule = "0 0 2 * * ?"             # Quartz cron; or trigger_tables = ["main.sales.raw_orders"]
timezone = "UTC"
max_concurrent_runs = 1
timeout_seconds = 3600
notify_failure = ["ops@example.com"]
run_as_service_principal = "<application-id>"

[[jobs.nightly.steps]]
key = "gate"
kind = "condition"                   # script, notebook, condition or run_job
left = "1"
right = "1"
after = ["main.sales.agg"]

[[jobs.nightly.steps]]
key = "export"
kind = "script"
path = "src/export.py"
after = ["gate:true"]
```

Script and notebook paths are relative to the bundle root; notebook paths keep their extension
(`src/report.py`, `src/report.ipynb`). Each selected task runs through `bricksmith-run` with the project path of the deployed bundle files. Order
inside the job mirrors the dependency graph; steps add their own edges with `after` and `before`.

## Splitting work across jobs

A job cannot silently lose a producer. If `main.sales.clean` is selected without `main.sales.raw`, validation
fails with `missing_producer`. When the producer lives in another job, say how readiness is guaranteed on
the consuming job:

```toml
[jobs.publish.contracts]
"main.sales.raw" = "run_job:ingest"   # publish starts ingest first (run-job task, wired for you)
"main.ref.codes" = "trigger"          # publish's trigger_tables includes codes' output table
"main.sales.fx"  = "external"         # produced outside bricksmith; acknowledged
```

## Inspect before deploying

```bash
bricksmith bundle validate            # exit 4 on errors, offline
bricksmith bundle resources           # readable plan of schemas, volumes, jobs and task dependencies
bricksmith --json bundle resources --target prod > snapshot.json
```

The snapshot is sorted and stable, so it can be committed and diffed. Unsupported task kinds, compute other
than serverless, a schedule combined with a trigger and similar mistakes fail here with a stable code, and
`load_resources` refuses to return resources for an invalid project.
