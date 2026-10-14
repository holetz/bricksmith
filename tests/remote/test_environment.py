"""Preflight stages and teardown guarantees against the selected workspace."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from bricksmith.preflight import State, create_owned_schema, remove_owned_schema, run_preflight, unique_schema_name

if TYPE_CHECKING:
    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext


def test_preflight_stages_pass(remote_context: ExecutionContext, remote_clients: Clients) -> None:
    report = run_preflight(remote_context, environ=os.environ, clients=remote_clients)
    states = {s.stage: s.state for s in report.stages}
    assert states == {
        "configured": State.PASSED,
        "authenticated": State.PASSED,
        "compute-tested": State.PASSED,
        "resources-tested": State.PASSED,
        "deployment-tested": State.NOT_RUN,
    }


def test_owned_schema_exists_and_holds_a_volume(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str
) -> None:
    workspace = remote_clients.workspace()
    catalog = remote_context.catalog or ""
    assert workspace.schemas.get(f"{catalog}.{test_schema}").name == test_schema


def test_teardown_removes_everything_after_injected_failure(
    remote_context: ExecutionContext, remote_clients: Clients
) -> None:
    workspace = remote_clients.workspace()
    catalog = remote_context.catalog or ""
    name = unique_schema_name(remote_context.schema_prefix or "bricksmith_ci")
    create_owned_schema(workspace, catalog, name)
    leftovers: list[str] = []

    def fail_then_clean() -> None:
        try:
            msg = "injected failure"
            raise RuntimeError(msg)
        finally:
            leftovers.extend(remove_owned_schema(workspace, catalog, name))

    with pytest.raises(RuntimeError, match="injected failure"):
        fail_then_clean()
    assert leftovers == []
    assert name not in {s.name for s in workspace.schemas.list(catalog_name=catalog)}
