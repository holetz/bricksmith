"""The built wheel and sdist import every module in clean environments (slow; builds the package)."""

from __future__ import annotations

import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
import verify_dist

SCRIPT = Path(verify_dist.__file__)


@pytest.mark.slow
def test_built_artifacts_import_without_heavy_integrations() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("modules imported") == 2


def _artifacts(tmp_path: Path, *, jar: bool) -> tuple[Path, Path]:
    wheel = tmp_path / "bricksmith-0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("bricksmith/__init__.py", "")
        if jar:
            archive.writestr("bricksmith/features/_jars/bricksmith-kernel.jar", b"jar")
    sdist = tmp_path / "bricksmith-0.tar.gz"
    source = tmp_path / "kernel.jar"
    source.write_bytes(b"jar")
    with tarfile.open(sdist, "w:gz") as archive:
        if jar:
            archive.add(source, "bricksmith-0/src/bricksmith/features/_jars/bricksmith-kernel.jar")
    return wheel, sdist


def test_require_kernel_accepts_artifacts_with_the_jar(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    verify_dist.require_kernel(*_artifacts(tmp_path, jar=True))
    assert "package bricksmith/features/_jars/bricksmith-kernel.jar" in capsys.readouterr().out


def test_require_kernel_refuses_artifacts_without_the_jar(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="do not package"):
        verify_dist.require_kernel(*_artifacts(tmp_path, jar=False))
