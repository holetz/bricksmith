# Ops router snippets

Choose exactly one snippet: the router declared by `.agents/quenching.json` (or the legacy
`.agents/quenching.json`). Replace
`<operation>` with the normalized entry-point name and `<module.path>` with the Python module
that exposes `main`. The router is the active surface; it does not copy domain logic.

## Python `project.scripts`

Use when `router` is `pyproject.toml`:

```toml
[project.scripts]
<operation> = "<module.path>:main"
```

Keep the entry point's return value intact so the console-script runner receives its typed exit
status. Verify the installed command with:

```bash
<operation> --help
<operation> --json
```

## `justfile`

Use when `router` is `justfile`:

```make
<operation>:
    python -m <module.path> --json
```

The recipe is a thin dispatch layer. Keep the module invocation identical to the documented
example and do not put repository-root discovery or domain logic in the recipe.

## `Taskfile.yml`

Use when `router` is `Taskfile.yml`:

```yaml
version: "3"

tasks:
  <operation>:
    cmds:
      - python -m <module.path> --json
```

Keep one task name per normalized operation. A convenience task may call this task, but it is not
a second router and must not register a second implementation.

## Lifecycle boundary

Register only `active` entry points in the canonical router. An `archived` entry point remains
searchable under `_archive/` and in the generated registry, but it has no active router entry.
After applying one snippet, run `.agents/quenching/bin/cq ops registry --write` and `.agents/quenching/bin/cq ops doctor --json`; the latter
proves that the router and generated registry name the same entry point.
