"""Deploy a bricksmith job (schema, two dependent tasks, a condition and a script), run it, verify order, destroy."""

from __future__ import annotations

import itertools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from bricksmith.env import auth_settings
from bricksmith.preflight import remove_owned_schema, unique_schema_name

if TYPE_CHECKING:
    from collections.abc import Iterator

    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

FIXTURE = Path(__file__).parent / "fixtures" / "jobs_project"
ROOT = Path(__file__).resolve().parents[2]
TIMEOUT = 1200
BUNDLE_NAME = "bricksmith_jobs_fixture"


class Deployment:
    """A copy of the fixture project bound to one context, schema name and built wheel."""

    def __init__(self, directory: Path, context: ExecutionContext, schema: str) -> None:
        self.directory = directory
        self.context = context
        self.schema = schema

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        # The selected profile is passed explicitly; inherited DATABRICKS_* settings never reach the CLI.
        base = {k: v for k, v in os.environ.items() if not k.startswith(("DATABRICKS_", "BUNDLE_VAR_"))}
        env = {**base, **auth_settings(self.context)}
        env.pop("DATABRICKS_SERVERLESS_COMPUTE_ID", None)
        env["BUNDLE_VAR_catalog"] = self.context.catalog or ""
        env["BUNDLE_VAR_schema"] = self.schema
        return subprocess.run(
            ["databricks", "bundle", *args, "-t", self.context.bundle_target or "ci"],
            cwd=self.directory,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=TIMEOUT,
        )


def _build_project(directory: Path) -> None:
    shutil.copytree(FIXTURE, directory, ignore=shutil.ignore_patterns("__pycache__", ".databricks"))
    dist = directory / "dist"
    built = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist), str(ROOT)],
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT,
    )
    assert built.returncode == 0, built.stderr[-800:]
    wheel = next(dist.glob("bricksmith-*.whl")).name
    for name, replacements in (
        ("bricksmith.toml", {"__WHEEL__": wheel}),
        ("databricks.yml", {"__VENV__": str(ROOT / ".venv")}),
    ):
        path = directory / name
        text = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")


def _summary(deployment: Deployment) -> dict[str, object]:
    result = deployment.cli("summary", "--output", "json")
    assert result.returncode == 0, result.stderr[-800:]
    return json.loads(result.stdout)


@pytest.fixture
def deployment(tmp_path: Path, remote_context: ExecutionContext, remote_clients: Clients) -> Iterator[Deployment]:
    """Yield the project; teardown destroys the deployment and removes the workspace folder on every path."""
    schema = unique_schema_name(remote_context.schema_prefix or "bricksmith_ci")
    directory = tmp_path / "project"
    _build_project(directory)
    current = Deployment(directory, remote_context, schema)
    folder = _bundle_folder(current)
    try:
        yield current
    finally:
        destroy = current.cli("destroy", "--auto-approve")
        if folder:
            try:
                remote_clients.workspace().workspace.delete(folder, recursive=True)
            except Exception as error:  # noqa: BLE001 - an already removed folder is fine
                sys.stderr.write(f"workspace folder cleanup: {type(error).__name__}\n")
        assert destroy.returncode == 0, destroy.stderr[-500:]
        left = [s.name for s in remote_clients.workspace().schemas.list(catalog_name=remote_context.catalog or "")]
        leftovers = [name for name in left if (name or "").endswith(schema)]
        if leftovers:
            remove_owned_schema(remote_clients.workspace(), remote_context.catalog or "", leftovers[0] or "")
            pytest.fail("bundle schema survived destroy", pytrace=False)


def _bundle_folder(deployment: Deployment) -> str:
    """Return the workspace folder the CLI creates for this bundle (its parent stays a shared area)."""
    result = deployment.cli("summary", "--output", "json")
    if result.returncode != 0:
        return ""
    root_path = str(json.loads(result.stdout).get("workspace", {}).get("root_path", ""))
    return str(Path(root_path).parent.parent / BUNDLE_NAME) if "/.bundle/" in root_path else ""


def test_ordered_job_deploys_runs_in_order_and_is_destroyed(
    deployment: Deployment, remote_clients: Clients, remote_context: ExecutionContext
) -> None:
    for step in (("validate",), ("deploy",)):
        result = deployment.cli(*step)
        assert result.returncode == 0, f"{step[0]} failed:\n{result.stderr[-1500:]}"
    workspace = remote_clients.workspace()
    summary = _summary(deployment)
    resources = summary["resources"]
    assert isinstance(resources, dict)
    job_id = int(resources["jobs"]["ordered"]["id"])
    suffix = deployment.schema
    schemas = [s.name or "" for s in workspace.schemas.list(catalog_name=remote_context.catalog or "")]
    assert any(name.endswith(suffix) for name in schemas), "the bundle schema resource was not created"

    ran = deployment.cli("run", "ordered")
    assert ran.returncode == 0, f"run failed:\n{(ran.stdout + ran.stderr)[-2500:]}"
    latest = next(iter(workspace.jobs.list_runs(job_id=job_id, limit=1)))
    run = workspace.jobs.get_run(latest.run_id or 0)
    tasks = {t.task_key: t for t in run.tasks or []}
    assert set(tasks) == {"main_fixture_first", "main_fixture_second", "gate", "finish", "note", "launch_child"}
    for key, task in tasks.items():
        assert task.state is not None
        assert str(task.state.result_state).endswith("SUCCESS"), f"{key}: {task.state.result_state}"
    chain = ["main_fixture_first", "main_fixture_second", "gate", "finish", "note"]
    for before, after in itertools.pairwise(chain):
        ended = tasks[before].end_time or 0
        started = tasks[after].start_time or 0
        assert ended <= started, f"{after} started before {before} finished"
    assert (tasks["gate"].end_time or 0) <= (tasks["launch_child"].start_time or 0)
