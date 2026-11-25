"""The operation scripts share one repository root, and the kernel build fails clearly and copies what sbt built."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import bootstrap
import build_kernel
import pytest

REPO = Path(__file__).resolve().parents[2]


def test_bootstrap_resolves_the_repository_root() -> None:
    assert bootstrap.ROOT == REPO
    assert (bootstrap.ROOT / "pyproject.toml").is_file()
    assert build_kernel.ROOT is bootstrap.ROOT


def test_build_kernel_refuses_without_sbt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SBT", raising=False)
    monkeypatch.setattr(build_kernel.shutil, "which", lambda _name: None)
    with pytest.raises(SystemExit, match="sbt is not on PATH"):
        build_kernel.main([])


def _fake_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, build: bool) -> list[list[str]]:
    project = tmp_path / "jvm"
    built = project / "target" / "bricksmith-kernel.jar"
    built.parent.mkdir(parents=True)
    monkeypatch.setattr(build_kernel, "ROOT", tmp_path)
    monkeypatch.setattr(build_kernel, "PROJECT", project)
    monkeypatch.setattr(build_kernel, "BUILT", built)
    monkeypatch.setattr(build_kernel, "PACKAGED", tmp_path / "src" / "_jars" / "bricksmith-kernel.jar")
    monkeypatch.setenv("SBT", "sbt-launcher")
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:  # noqa: ANN401
        calls.append(command)
        if build:
            built.write_bytes(b"jar")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(build_kernel.subprocess, "run", run)
    return calls


def test_build_kernel_copies_the_built_jar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _fake_tree(tmp_path, monkeypatch, build=True)
    assert build_kernel.main(["--skip-tests"]) == 0
    assert calls == [["sbt-launcher", "-batch", "package"]]
    assert (tmp_path / "src" / "_jars" / "bricksmith-kernel.jar").read_bytes() == b"jar"
    assert "kernel JAR: src/_jars/bricksmith-kernel.jar (3 bytes)" in capsys.readouterr().out


def test_build_kernel_refuses_when_sbt_leaves_no_jar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_tree(tmp_path, monkeypatch, build=False)
    with pytest.raises(SystemExit, match="is missing"):
        build_kernel.main([])
    assert calls == [["sbt-launcher", "-batch", "test", "package"]]
