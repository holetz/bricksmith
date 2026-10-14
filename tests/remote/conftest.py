"""Remote-test harness: opt-in skip, explicit context and a test-owned schema with verified teardown."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from bricksmith.clients import Clients
from bricksmith.context import ExecutionContext, resolve_context
from bricksmith.env import load_dotenv
from bricksmith.preflight import create_owned_schema, remove_owned_schema, unique_schema_name

if TYPE_CHECKING:
    from collections.abc import Iterator

OPT_IN = "BRICKSMITH_RUN_REMOTE_TESTS"
ROOT = Path(__file__).resolve().parents[2]


def _dotenv() -> dict[str, str]:
    path = ROOT / ".env"
    return load_dotenv(path) if path.is_file() else {}


def remote_enabled() -> bool:
    """Whether the opt-in is ``true`` in the process environment or the explicitly loaded ``.env``."""
    value = os.environ.get(OPT_IN) or _dotenv().get(OPT_IN, "")
    return value.strip().lower() == "true"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test here ``databricks`` and skip them unless the opt-in is set."""
    enabled = remote_enabled()
    skip = pytest.mark.skip(reason=f"not run: set {OPT_IN}=true to run remote tests")
    here = Path(__file__).parent
    for item in items:
        if Path(str(item.fspath)).is_relative_to(here):
            item.add_marker(pytest.mark.databricks)
            if not enabled:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def remote_context() -> ExecutionContext:
    """The single selected environment's context; a failure here never switches environments."""
    return resolve_context(dotenv=_dotenv())


@pytest.fixture(scope="session")
def remote_clients(remote_context: ExecutionContext) -> Clients:
    """Real clients bound to the selected context."""
    return Clients(remote_context)


@pytest.fixture
def test_schema(remote_context: ExecutionContext, remote_clients: Clients) -> Iterator[str]:
    """Yield a unique schema owned by this test; teardown fails loudly when anything is left over."""
    catalog = remote_context.catalog or ""
    name = unique_schema_name(remote_context.schema_prefix or "bricksmith_ci")
    workspace = remote_clients.workspace()
    create_owned_schema(workspace, catalog, name)
    try:
        yield name
    finally:
        leftovers = remove_owned_schema(workspace, catalog, name)
        if leftovers:
            sys.stderr.write("LEFTOVERS: " + "; ".join(leftovers) + "\n")
            pytest.fail("cleanup incomplete: " + "; ".join(leftovers), pytrace=False)
