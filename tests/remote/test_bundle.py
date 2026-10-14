"""Validate, deploy, run and destroy the Python-defined fixture bundle under target ``ci``."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from bricksmith.env import auth_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from bricksmith.context import ExecutionContext

BUNDLE = Path(__file__).parent / "fixtures" / "minimal_bundle"
TIMEOUT = 900


def _cli(context: ExecutionContext, *args: str) -> subprocess.CompletedProcess[str]:
    # The selected profile is passed explicitly; inherited DATABRICKS_* settings never reach the CLI.
    base = {k: v for k, v in os.environ.items() if not k.startswith("DATABRICKS_")}
    env = {**base, **auth_settings(context)}
    env.pop("DATABRICKS_SERVERLESS_COMPUTE_ID", None)
    return subprocess.run(
        ["databricks", "bundle", *args, "-t", context.bundle_target or "ci"],
        cwd=BUNDLE,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT,
    )


@pytest.fixture
def deployed(remote_context: ExecutionContext) -> Iterator[None]:
    """Destroy the deployment on every exit path, including failures."""
    try:
        yield
    finally:
        destroy = _cli(remote_context, "destroy", "--auto-approve")
        assert destroy.returncode == 0, destroy.stderr[-500:]


@pytest.mark.usefixtures("deployed")
def test_python_resource_job_validates_deploys_runs_and_is_destroyed(remote_context: ExecutionContext) -> None:
    assert remote_context.bundle_target == "ci"
    for step in (("validate",), ("deploy",), ("run", "fixture_job")):
        result = _cli(remote_context, *step)
        assert result.returncode == 0, f"{step[0]} failed:\n{result.stderr[-1500:]}"
