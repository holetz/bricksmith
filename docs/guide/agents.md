# Agents

The `plugins/bricksmith-omnigent/agents/` directory ships three read-only omnigent bundles over the public command line. See the [plugin README](../../plugins/bricksmith-omnigent/README.md) for the permission table.

## The three domains

- **`bricksmith-bundle`**: bundle resources and the [task graph](tasks-and-graph.md).
- **`bricksmith-mlops`**: [model dataset](model-datasets.md) contracts and the model card. Lifecycle changes stay with the human.
- **`bricksmith-analysis`**: run reports, diagnosis and [monitoring](monitoring.md) windows. Table comparison and quality checks are not granted because they write a local report file.

## The minimal-grant rule

A bundle grants the smallest set that does the job. The policy is deny-only: it denies write, edit, web, delegation and skill tools, and any shell command that is not a granted `bricksmith <verb>` chain. The shell rule cannot forbid a flag, so a verb is granted only while the CLI registers no mutating flag for it. The agents follow the exit codes and the one-explicit-context rule of [context and CLI](context-and-cli.md), and take names and thresholds from the project, never from the bundle.

## The dry run

`tests/test_omnigent_agents.py` loads each `config.yaml` with the standard library, evaluates the policy on synthetic tool calls (denied tools, granted verbs, injection characters, a missing command) and pins every granted verb to the CLI parser. It contacts no model and uses no credential.

## Adding a verb

1. Confirm the verb is read-only: the parser registers none of `--apply`, `--out`, `--output`, `--freeze`, `--write-baseline`, `--authorize`, `--report-out`.
2. Add it to the regex in `config.yaml`, to the granted list in `instructions.md` and to `VERBS` in the test, in the same change.
3. Run `uv run pytest tests/test_omnigent_agents.py -q`.
4. A write-capable verb needs its own agent and its own approval, never an existing bundle.
