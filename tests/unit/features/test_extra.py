"""Every features module imports without numpy; using an engine then names the extra to install."""

from __future__ import annotations

import subprocess
import sys

PROBE = """
import importlib, sys
sys.modules["numpy"] = None  # makes any import of numpy fail
for name in ("reference", "engine", "arrow", "engines", "spark", "benchmark", "history", "spec", "catalog"):
    importlib.import_module("bricksmith.features." + name)
from bricksmith.errors import MissingExtraError
from bricksmith.features import FeatureSpec, WindowFeature, compute_rows
spec = FeatureSpec(("e",), "c", (WindowFeature(("x",), (3,), ("mean",)),))
try:
    compute_rows([{"e": 1, "c": "2026-01-31", "x": 1.0}], spec)
except MissingExtraError as error:
    print(error)
else:
    raise SystemExit("compute_rows ran without numpy")
"""


def test_modules_import_without_numpy_and_name_the_extra() -> None:
    result = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "bricksmith[features]" in result.stdout
