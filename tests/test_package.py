"""All capabilities are importable from a single installed library."""

from __future__ import annotations

import importlib
from importlib.metadata import distribution
from pathlib import Path

import pytest


@pytest.mark.parametrize("capability", ["dab", "registry", "mlops", "monitor"])
def test_capability_resolves_inside_library(capability: str) -> None:
    package = importlib.import_module("bricksmith")
    module = importlib.import_module(f"bricksmith.{capability}")
    assert package.__file__ is not None
    assert module.__file__ is not None
    assert Path(module.__file__).is_relative_to(Path(package.__file__).parent)


def test_installed_distribution_remains_private() -> None:
    metadata = distribution("bricksmith").metadata
    assert "Private :: Do Not Upload" in metadata.get_all("Classifier", [])
