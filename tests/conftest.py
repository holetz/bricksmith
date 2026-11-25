"""Suite-wide bootstrap: the shared fixture library and the layer markers derived from test paths.

Each first-level directory under ``tests/`` is one declared proof layer (``.agents/quenching.json`` holds its reach
and budget, ``tests/README.md`` describes it). Shared fixtures live in ``tests/fixtures/``, one module per layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

pytest_plugins = ["tests.fixtures.jvm", "tests.fixtures.remote", "tests.fixtures.unit"]


# QUENCHING PROOF COLLECTION HOOK: BEGIN
PROOF_ROOT = Path(__file__).resolve().parent
LAYER_MARKERS = {
    "contract": "contract",
    "jvm": "jvm",
    "remote": "remote",
    "unit": "unit",
}


def pytest_collection_modifyitems(session: pytest.Session, config: pytest.Config, items: list[pytest.Item]) -> None:
    """Attach the declared marker to each collected test from its first path segment."""
    del session, config
    for item in items:
        try:
            relative = Path(str(item.fspath)).resolve().relative_to(PROOF_ROOT)
        except ValueError:
            continue
        marker = LAYER_MARKERS.get(relative.parts[0]) if relative.parts else None
        if marker:
            item.add_marker(marker)


# QUENCHING PROOF COLLECTION HOOK: END
