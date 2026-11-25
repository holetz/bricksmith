# Tests

Each first-level directory is one proof layer; `tests/conftest.py` derives the layer marker from the path, and
`tests/fixtures/` holds the fixtures shared across modules, one module per layer.

| Layer | What it proves |
| --- | --- |
| `unit` | In-process behavior of the library: pure logic, CLI, planning and execution on temporary trees, and local Spark parity where pyspark is available. |
| `contract` | Repository-facing contracts: packaging, the leak gate, the operation scripts, the quenching configuration and the plugin bundle. |
| `jvm` | The JVM feature kernel on a classic local Spark session (`uv run --isolated --with pyspark==4.0.1 pytest tests/jvm`). |
| `remote` | Behavior on a real Databricks workspace; opt-in with `BRICKSMITH_RUN_REMOTE_TESTS=true` (see `docs/project/databricks-tests.md`). |

CI runs the suite in random order under coverage; the floor is `[tool.coverage.report] fail_under` in
`pyproject.toml`, and `.coverage-floor.json` records the per-root ratchet.

<!-- quenching-proof-readme-start -->
<!-- quenching-proof-readme-sha256 ee57c81d807340dd8b77f2294a5a4eb5377351e9ee98c3b8686ada0269d4f2b2 -->
## Declared proof layers

### contract
- Proves: the `contract` proof contract declared by the target.
- Marker: `contract`
- Reach: tree
- Budget: 300
- Required: yes
- Test modules: 6
- Fixture modules: `fixtures/jvm.py`, `fixtures/remote.py`, `fixtures/unit.py`

### jvm
- Proves: the `jvm` proof contract declared by the target.
- Marker: `jvm`
- Reach: session
- Budget: 600
- Required: no
- Test modules: 1
- Fixture modules: `fixtures/jvm.py`, `fixtures/remote.py`, `fixtures/unit.py`

### remote
- Proves: the `remote` proof contract declared by the target.
- Marker: `remote`
- Reach: workspace
- Budget: 3600
- Required: no
- Test modules: 25
- Fixture modules: `fixtures/jvm.py`, `fixtures/remote.py`, `fixtures/unit.py`

### unit
- Proves: the `unit` proof contract declared by the target.
- Marker: `unit`
- Reach: session
- Budget: 120
- Required: yes
- Test modules: 86
- Fixture modules: `fixtures/jvm.py`, `fixtures/remote.py`, `fixtures/unit.py`

### Declared cost contract
- Temp root: `isolated` — outside the repository, removed when the run ends
- Workers: `1`
- Task scope: `uv run pytest tests/unit`
- Close scope: `uv run pytest tests/unit tests/contract -m 'not slow'`
- CI scope: `uv run coverage run -m pytest --random-order`
<!-- quenching-proof-readme-end -->
