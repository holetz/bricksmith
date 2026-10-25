# bricksmith-omnigent

Three read-only agent bundles for omnigent that wrap the generic `bricksmith` command line, one per data domain. Each bundle is a directory under `agents/<id>/` with a `config.yaml` (the tool grant, the only authority) and an `instructions.md` (the workflow).

| Agent | Domain | Granted shell verbs |
| --- | --- | --- |
| `bricksmith-bundle` | resource and task-graph inspection | `bundle resources`, `bundle validate`, `tasks list`, `tasks graph`, `tasks validate`, `context show` |
| `bricksmith-mlops` | model dataset contracts and the model card | `dataset validate`, `dataset describe`, `model card`, `tasks list`, `tasks graph`, `tasks validate`, `context show` |
| `bricksmith-analysis` | run reports, diagnosis and monitoring windows | `ops report`, `ops diagnose`, `ops readiness`, `ops explain`, `monitor compare`, `monitor evaluate`, `tasks list`, `context show` |

## Permissions

Every bundle carries one deny-only policy (`omnigent.policies.builtins.cel.cel_policy`): omnigent has no positional allow list and a DENY outranks an ALLOW.

| Family | Tool names | Decision |
| --- | --- | --- |
| Write | `Write`, `write`, `sys_os_write` | denied |
| Edit | `Edit`, `edit`, `MultiEdit`, `NotebookEdit`, `sys_os_edit` | denied |
| Web | `WebFetch`, `webfetch`, `WebSearch`, `websearch` | denied |
| Delegation | `sys_session_create`, `Task`, `Agent`, `task` | denied |
| Skill | `Skill` | denied |
| Shell | `sys_os_shell`, `Bash`, `Shell`, `bash` | denied unless the whole command is one or more granted `bricksmith <verb>` segments joined by `&&`, `\|\|` or `;`, with arguments free of `; & \| < > $`, a backtick, a backslash and a newline |
| Read | `Read`, `Grep`, `Glob` | allowed |

### Not expressed

- The shell rule cannot forbid a flag. Every granted verb is read-only today (none registers `--apply`, `--out`, `--output`, `--freeze`, `--write-baseline`, `--authorize` or `--report-out`) and `tests/test_omnigent_agents.py` pins that against the CLI parser. A verb that gains a mutating flag must leave the list in the same change. For this reason `quality compare` and `quality check` (both register `--report-out`) are not granted.
- Read tools carry no path filter: the instructions forbid opening `.env` or a secret, the policy does not.
- Remote reads use the selected context's credentials; the bundle never holds one.
- Lifecycle changes (`model promote`, `run --apply`) and `env check` are not granted and have no agent.

## Run a bundle

```bash
omnigent run --agent plugins/bricksmith-omnigent/agents/bricksmith-bundle --harness opencode --model <model>
```

No model is pinned in `config.yaml`; `--harness` and `--model` override the executor. Pick one explicit context (`--env`, or `--profile` and `--target`) for the session.

## Bind a role from an overlay

An overlay binds a role by an `agents/<name>.json` file holding `name` and `description`, where `name` matches a bundle id. It holds no credential and no real URL. Names, vocabulary and thresholds come from the project (`bricksmith.toml`, task files) and the overlay; they never change the tool grant.

## Dry run

```bash
uv run pytest tests/test_omnigent_agents.py -q
```

The test uses only the standard library and synthetic tool calls; no model is contacted.
