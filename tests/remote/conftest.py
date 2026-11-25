"""Remote-test harness: every test here is marked ``databricks`` and skipped unless the opt-in is set.

The fixtures (explicit context, clients, test-owned schema) live in ``tests/fixtures/remote.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.remote import OPT_IN, remote_enabled


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
