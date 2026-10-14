# Context and command line

Every remote operation receives an immutable `ExecutionContext`. Nothing reads a global profile,
and importing bricksmith never loads `.env` or contacts Databricks.

## Precedence

For each setting, the first non-blank value wins:

1. Explicit arguments (`--env`, `--profile`, `--target`, or keyword arguments in Python).
2. Process environment (`BRICKSMITH_*`).
3. A dotenv file loaded explicitly (`--env-file .env`, or `load_dotenv(path)` in Python).
4. Project defaults.

`BRICKSMITH_TEST_ENV` selects the environment (`free` unless set). Only that environment's
`BRICKSMITH_<ENV>_*` fields are read. `BRICKSMITH_TARGET` names the bundle target and never picks
the workspace. See [.env.example](../../.env.example) and the
[test setup](../development/databricks-tests.md).

## Authentication rules

- With a profile, exported `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_CLIENT_ID` and
  `DATABRICKS_CLIENT_SECRET` are rejected instead of mixed in.
- With only an expected host (CI), an exported host must match it.
- A profile missing from the Databricks config file is an error; `DEFAULT` is never substituted.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | unexpected failure |
| 2 | usage error |
| 3 | configuration, authentication or missing extra |
| 4 | invalid input |

With `--json`, errors are written to stderr as `{"error": {"code": ..., "message": ...}}`.

## Examples

```bash
bricksmith context show                                   # offline, nothing is contacted
bricksmith context show --json --env-file .env --env corporate
bricksmith --target ci context show --profile bricksmith-free
```

```python
from bricksmith.clients import Clients
from bricksmith.context import resolve_context
from bricksmith.env import load_dotenv

ctx = resolve_context(dotenv=load_dotenv(".env"), catalogs={"raw": "raw_dev"})
spark = Clients(ctx).spark()  # needs: pip install 'bricksmith[spark]'
```

Pass `providers={"spark": fake_factory}` to `Clients` in unit tests.
