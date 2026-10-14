# External inputs, connectors and volume artifacts

`bricksmith.io` moves small, bounded data between tasks and the outside world. It imports only the
standard library; drivers are chosen by the caller (`pip install 'bricksmith[postgres]'` adds
`psycopg`). Design record: [BS-09](../specs/bs-09-external-io.md).

## Declare external I/O

```python
@task(kind="ingest", external="orders_src", metadata={"external_sinks": ["orders_dst"]})
def pull(ctx):
    table = ctx.read_external("orders_src")  # bricksmith.io.Table, bounded
    ctx.write_external("orders_dst", table)  # only declared sinks are accepted
```

Inputs are graph edges of kind `external`; sinks are metadata. `io_lineage(graph)` returns the
`in`/`out` links, and `PlanEntry.sinks` carries the sinks in the plan.

```python
resources = ExternalResources(
    sources={"orders_src": lambda: SqlSource(connector, "orders", columns=("id", "label"), bookmark_column="id")},
    sinks={"orders_dst": lambda: SqlSink(connector, "orders_copy", columns=("id", "label"))},
    limits=Limits(max_rows=100_000, max_bytes=64 * 2**20, batch_rows=1_000),
)
apply_plan(plan, graph, ..., externals=resources)  # also run_plan, apply_from_args, run_entrypoint
```

## SQL connector, secrets and errors

`SqlConnector(driver.connect, params, paramstyle="qmark"|"format", resolver=...)`. A parameter value
`secret:<scope>/<key>` is resolved inside `connect()` by the resolver
(`workspace_resolver(clients.secrets())`) and never stored, logged or reported; error text is
scrubbed and reports show `<secret-ref>`. Failures are `TransientIoError` (retried by `IoRetry`) or
`PermanentIoError` (never retried). Table and column names are validated identifiers.

## Incremental transfer and replay

`resources.transfer("orders_src", "orders_dst", key="orders")` (or `transfer(...)`) reads rows
after the stored bookmark in batches, commits each batch with a ledger token in the sink's
transaction and records progress. The bookmark moves only after the whole transfer succeeds, so an
interrupted run leaves it unchanged and a rerun resumes and skips already committed batches.

| Situation | Outcome |
|---|---|
| Failure between batches | committed batches stay; rerun continues; no duplicates |
| Crash after sink commit, before progress save | rerun trims the already delivered rows |
| `max_rows` / `max_bytes` reached | stops cleanly (`truncated=True`), bookmark advances to what was delivered |
| First batch over `max_bytes` | `LimitExceededError` |

Limits of the guarantee: the bookmark column must be unique and increasing; a transfer is atomic
per batch, never across source, sink and bookmark store (no distributed transaction); a sink without
transactions gives at-least-once. Stores: `MemoryBookmarkStore`, `FileBookmarkStore(dir)` (works on
a volume path) or your own `BookmarkStore`.

## Volume artifacts

```python
store = ArtifactStore("catalog.schema.exports", files_api_client(clients))  # local: Files API
store = ArtifactStore("catalog.schema.exports", FilesystemVolumeClient())  # deployed: /Volumes
store.write("reports/summary.json", "{}")
store.read_text(...)
store.list("reports")
store.delete(...)
```

Volumes are named explicitly; several can share one schema and no naming per model is required.
Paths must be relative, `/`-separated, without `..`, `.`, empty segments, backslashes or control
characters; reads and writes are capped by `max_bytes`.

## Materialization, checkpoints and exports (opt-in)

`Materializer(store, MaterializationPolicy(enabled=True, max_rows=..., max_files=...))` offers
`checkpoint/restore`, `export(fmt="csv"|"jsonl")` and `cleanup(run_id)`, all under
`<prefix>/<run_id>/`. Disabled by default.

| Capability | Local (SDK) | Databricks compute |
|---|---|---|
| Artifact I/O, checkpoints, exports | Files API client | `/Volumes` filesystem client |
| SQL connectors | any reachable database | needs outbound network (restricted on Free Edition) |
| Spark `cache`/`persist`/`checkpoint` | not provided | not provided (unavailable on serverless) |
| Directory removal by `cleanup` | not provided | not provided |
