"""Shared fixtures for the ``remote`` proof layer (reach: ``workspace``).

The explicit context, real clients, a test-owned schema with verified teardown, and a managed volume for the kernel
JAR. Remote tests are opt-in; ``tests/remote/conftest.py`` marks and skips them.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from bricksmith.clients import Clients
from bricksmith.context import ExecutionContext, resolve_context
from bricksmith.env import load_dotenv
from bricksmith.preflight import (
    _managed_volume_type,  # pyright: ignore[reportPrivateUsage]
    create_owned_schema,
    remove_owned_schema,
    unique_schema_name,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

OPT_IN = "BRICKSMITH_RUN_REMOTE_TESTS"
ROOT = Path(__file__).resolve().parents[2]


def dotenv() -> dict[str, str]:
    """The repository's optional ``.env``, loaded explicitly; the process environment alone may select the context."""
    path = ROOT / ".env"
    return load_dotenv(path) if path.is_file() else {}


def remote_enabled() -> bool:
    """Whether the opt-in is ``true`` in the process environment or the explicitly loaded ``.env``."""
    value = os.environ.get(OPT_IN) or dotenv().get(OPT_IN, "")
    return value.strip().lower() == "true"


# QUENCHING PROOF FIXTURE LIBRARY: BEGIN
# The declared reach is the ceiling for this module: workspace.


@pytest.fixture(scope="session")
def remote_context() -> ExecutionContext:
    """The single selected environment's context; a failure here never switches environments."""
    return resolve_context(dotenv=dotenv())


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


@pytest.fixture
def jar_volume(remote_context: ExecutionContext, remote_clients: Clients, test_schema: str) -> Iterator[str]:
    """Create a managed volume for the kernel JAR in the test schema; the schema fixture verifies removal."""
    catalog = remote_context.catalog or ""
    workspace = remote_clients.workspace()
    workspace.volumes.create(
        catalog_name=catalog, schema_name=test_schema, name="jars", volume_type=_managed_volume_type()
    )
    try:
        yield f"{catalog}.{test_schema}.jars"
    finally:
        with contextlib.suppress(Exception):  # the schema teardown verifies what is left
            workspace.volumes.delete(f"{catalog}.{test_schema}.jars")


# QUENCHING PROOF FIXTURE LIBRARY: END
