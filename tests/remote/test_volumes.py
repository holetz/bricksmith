"""Volume artifact I/O on the selected workspace: a test-owned managed volume in the test-owned schema."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import pytest

from bricksmith.io import (
    ArtifactNotFoundError,
    ArtifactStore,
    LimitExceededError,
    MaterializationPolicy,
    Materializer,
    Table,
    files_api_client,
)
from bricksmith.preflight import _managed_volume_type

if TYPE_CHECKING:
    from collections.abc import Iterator

    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext


@pytest.fixture
def volumes(remote_context: ExecutionContext, remote_clients: Clients, test_schema: str) -> Iterator[tuple[str, str]]:
    """Create two managed volumes in the test schema; the schema fixture removes everything afterwards."""
    catalog = remote_context.catalog or ""
    workspace = remote_clients.workspace()
    names = ("exports", "scratch")
    for name in names:
        workspace.volumes.create(
            catalog_name=catalog, schema_name=test_schema, name=name, volume_type=_managed_volume_type()
        )
    try:
        yield (f"{catalog}.{test_schema}.{names[0]}", f"{catalog}.{test_schema}.{names[1]}")
    finally:
        for name in names:
            with contextlib.suppress(Exception):  # the schema teardown verifies what is left
                workspace.volumes.delete(f"{catalog}.{test_schema}.{name}")


def test_artifact_round_trip_and_two_resources_in_one_schema(volumes: tuple[str, str], remote_clients: Clients) -> None:
    client = files_api_client(remote_clients)
    first, second = (ArtifactStore(name, client, max_bytes=1024) for name in volumes)
    first.write("reports/summary.json", '{"rows": 2}')
    second.write("reports/summary.json", "other")  # same relative path, other resource
    assert first.read_text("reports/summary.json") == '{"rows": 2}'
    assert second.read_text("reports/summary.json") == "other"
    assert first.exists("reports/summary.json")
    assert [(e.name, e.is_dir) for e in first.list()] == [("reports", True)]
    assert [e.name for e in first.list("reports")] == ["summary.json"]
    with pytest.raises(LimitExceededError):
        first.write("too-big.bin", b"x" * 2048)
    first.delete("reports/summary.json")
    second.delete("reports/summary.json")
    assert not first.exists("reports/summary.json")
    with pytest.raises(ArtifactNotFoundError):
        first.read("reports/summary.json")
    assert first.list("reports") == []


def test_checkpoint_export_and_cleanup_on_a_volume(volumes: tuple[str, str], remote_clients: Clients) -> None:
    store = ArtifactStore(volumes[0], files_api_client(remote_clients))
    mat = Materializer(store, MaterializationPolicy(enabled=True, max_files=5))
    table = Table(("id", "label"), ((1, "a"), (2, "b")))
    mat.checkpoint("run1", "t", table)
    mat.export("run1", "t", table)
    assert mat.restore("run1", "t") == table
    assert mat.cleanup("run1") == 2
    assert mat.restore("run1", "t") is None
