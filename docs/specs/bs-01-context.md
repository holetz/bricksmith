# BS-01. Context, parameters and lazy integrations

Status: implemented. Epic tasks BS-01.1 to BS-01.5. No deviation from the shared guardrails.

## Decisions closed

- Layers, highest first: explicit arguments, process environment, explicitly loaded dotenv,
  project defaults. A blank value counts as unset, so an empty `.env` slot never shadows a lower layer.
- Environment names match `[a-z][a-z0-9_]*`; `BRICKSMITH_TEST_ENV` selects (default `free`) and
  fields are `BRICKSMITH_<ENV>_{PROFILE,WORKSPACE_HOST,CATALOG,SCHEMA_PREFIX,COMPUTE}`. Only the
  selected environment's fields are read, so another environment may be blank or malformed.
- `BRICKSMITH_TARGET` is the bundle target only. It never selects a profile or host.
- Auth mode: `profile` (named profile), `credentials` (expected host, injected credentials) or
  `none`. Profile mode rejects exported `DATABRICKS_HOST/TOKEN/CLIENT_ID/CLIENT_SECRET` and a
  different `DATABRICKS_CONFIG_PROFILE`; credential mode rejects a different exported host or any
  exported profile. Messages name variables, never values.
- A profile absent from the Databricks config file is an error naming `DEFAULT` as not used.
- Compute is `serverless` or `cluster:<id>`; concurrency is an integer >= 1 (default 2).
- Dotenv loading is a function call (`--env-file`); it returns a dict and never edits `os.environ`.
- Auth settings are returned (`auth_settings`) for explicit use; bricksmith never exports them.
- Window forms: `start`+`end`, or `as_of` with optional `lookback_days`; both ends inclusive.
  Repeating a segment dimension and include/exclude overlap are ambiguous and rejected.
- Exit codes: 0 ok, 1 unexpected, 2 usage, 3 config/auth/missing extra, 4 validation.

## Public contract

- `errors`: `BricksmithError(code, exit_code, to_dict)`, `UsageError`, `ConfigError`,
  `AuthConflictError`, `MissingExtraError`, `ValidationError`, `EXIT_CODES`.
- `context`: frozen `ExecutionContext` (mappings read-only; `physical_catalog`, `physical_schema`,
  `auth_mode`, `to_dict`) and `resolve_context(...)`; `sources` records the winning layer per field.
- `params`: `parse_date`, `parse_window`/`DateWindow`, `parse_selector`/`DatasetSelector`,
  `parse_segments`, `parse_write_options`/`WriteOptions`, `validate_parameters`.
- `env`: `parse_dotenv`, `load_dotenv`, `auth_settings`, `check_auth_conflicts`,
  `require_profile_defined`, `normalize_host`.
- `clients`: `Clients(context, providers=None)` with `spark/workspace/tracking/secrets/io`,
  cached per instance; `import_extra` raises `MissingExtraError` naming the extra.
- CLI: `bricksmith [--json --env --env-file --profile --target] <group> <command>`; the options
  are also accepted after the group. Groups come from `cli/groups.py::GROUPS` (module paths);
  each exposes `register(subparsers)`. Shipped: `context show`.
- Extras (declared only): `spark`, `sdk`, `bundle`, `mlops`.

## Acceptance tests (`tests/unit/core/`)

- Import, `--help` and `context show` load no heavy module (subprocess `sys.modules` check).
- Import does not read `.env` or touch `os.environ`; dotenv loads only when asked.
- Invalid windows, selectors, segments, write options and parameters fail before a provider runs.
- Two contexts share no mapping, target or parameter state; contexts reject mutation.
- Both profiles configured: only the selected one is active and validated.
- Target `ci` alone leaves profile and host unset.
- Conflicting exported credentials are rejected without echoing values.
- A missing profile is an error, never `DEFAULT`.
- CLI errors are structured JSON or text with the documented exit codes.

## Open

The default providers import Databricks packages through the extras and are covered only by
injected fakes until the remote harness (BS-00 environments) exercises them.
