"""The built wheel and sdist import every module in clean environments (slow; builds the package)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "verify_dist.py"


@pytest.mark.slow
def test_built_artifacts_import_without_heavy_integrations() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("modules imported") == 2
