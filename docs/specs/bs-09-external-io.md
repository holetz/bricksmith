# BS-09. External inputs, connectors and volume artifacts

Slice of [Epic 0001](../epics/0001-bricksmith-library.md); [user guide](../guide/external-io.md).

## Decisions closed

**First SQL driver and semantics.** The connector contract is DB-API 2.0 and injectable: the caller
passes the driver's `connect` callable. The disposable proof target is PostgreSQL (`postgres` extra:
`psycopg[binary]`) in a Docker container; a zero-dependency SQLite adapter runs the same contract
offline. Free Edition restricts outbound networking, so the SQL proof is local and the workspace
proof covers volume artifacts.

**Delivery and replay.** A transfer reads in bookmark order, commits each batch to the sink together
with a ledger token in one sink transaction, and saves a progress record after each committed batch.
The committed bookmark advances only after the whole transfer succeeds. A replay resumes from the
progress record; a batch whose token the ledger knows is skipped or trimmed to the rows after the
recorded end. Result: exactly-once rows at the sink for a source whose bookmark column is unique and
increasing. Limits: atomic per batch only, never across source, sink and bookmark store; a
non-transactional sink degrades to at-least-once; a bookmark must be JSON-serializable.

**Task metadata.** Sources reuse `external=`; sinks are `metadata={"external_sinks": [...]}`.
`PlanEntry.sinks` carries them (serialized only when present, so earlier plan fingerprints do not
change). `io_lineage(graph)` lists `in` and `out` links per task.

**Runtime wiring.** `ExternalProvider` (`read_external`, `write_external`) is injected through
`run_plan(externals=)`, `apply_plan`, `apply_from_args` and `run_entrypoint`. Without a provider,
`read_external` keeps the BS-03 behaviour (table I/O). `ctx.write_external(name, data)` accepts only
declared sinks.

**Secrets.** Parameters named `secret:<scope>/<key>` are resolved inside `SqlConnector.connect`
through an injectable resolver (`workspace_resolver(Clients.secrets())` decodes the API value).
Resolved values live only in a `Redactor` that scrubs error text; reports show `<secret-ref>`;
credential-like literal names are masked; driver errors are re-raised without chained causes.

**Errors.** `IoError` > `TransientIoError` (retryable) / `PermanentIoError`; classification by
exception class names along the MRO (`OperationalError`, `InterfaceError`, timeouts, `OSError` are
transient; integrity, programming and data errors are not). `IoRetry` retries only transient failures.

**Volumes and materialization.** `VolumeRef(catalog, schema, volume)` plus validated relative paths;
`FilesApiVolumeClient` (local) and `FilesystemVolumeClient` (deployed `/Volumes`). `Materializer`
is opt-in (`enabled=False` by default) with row, file and byte limits and exact per-run cleanup.

## Deviations and limits

- No CLI group: nothing needed a command beyond the runner. Spark `cache`/`persist`/`checkpoint` are
  not wrapped (unavailable on serverless); materialization is volume-artifact based and small-table.
- Directories are not deleted by `cleanup` (the client protocol deletes files only).
- The `FilesystemVolumeClient` is proven offline on a temporary root, not on compute.

## Acceptance tests

- `tests/unit/io/test_transfer.py` (SQLite contract, interrupted transfer and replay, crash between
  sink commit and progress save, limits, retry, classification), `test_secrets.py`, `test_volumes.py`
  (paths, both clients, shared schema, materialization), `test_wiring.py` (graph, plan, run).
- `tests/unit/io/test_postgres_docker.py` (`slow`, `docker`): interrupted transfer and replay on
  PostgreSQL, skipped without Docker or the `postgres` extra.
- `tests/remote/test_volumes.py`: volume artifacts and two resources in one schema on `free`.
