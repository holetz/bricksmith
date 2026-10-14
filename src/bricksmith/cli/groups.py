"""Command groups: module paths that each expose ``register(subparsers) -> None``."""

GROUPS: tuple[str, ...] = (
    "bricksmith.cli.context_group",
    "bricksmith.cli.env_group",
    "bricksmith.registry.cli",
    "bricksmith.cli.tasks_group",
    "bricksmith.runner.cli",
    "bricksmith.dab.cli",
    "bricksmith.evolution.cli",
    "bricksmith.quality.cli",
    "bricksmith.experiments.cli",
    "bricksmith.ops.cli",
    "bricksmith.mlops.cli",
    "bricksmith.governance.cli",
)
