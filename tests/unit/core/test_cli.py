"""CLI behaviour and the no-remote, lightweight-import guarantees (checked in subprocesses)."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

from bricksmith.cli.main import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

HEAVY = ("pyspark", "databricks", "mlflow", "sklearn", "pandas", "numpy", "requests", "databricks_bundles")


def _run(code: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False, cwd=cwd, env=env)


def test_import_and_help_load_no_heavy_module() -> None:
    code = (
        "import sys, bricksmith, bricksmith.context, bricksmith.params, bricksmith.env, bricksmith.clients\n"
        "from bricksmith.cli.main import main\n"
        "try:\n main(['--help'])\nexcept SystemExit: pass\n"
        "main(['context','show'])\n"
        f"bad = [m for m in {HEAVY!r} if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    done = _run(code)
    assert done.returncode == 0, done.stderr


def test_import_does_not_load_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("BRICKSMITH_FREE_PROFILE=from-dotenv\nBRICKSMITH_TARGET=ci\n")
    code = (
        "import os, bricksmith.context as c, bricksmith.env\n"
        "assert 'BRICKSMITH_FREE_PROFILE' not in os.environ\n"
        "ctx = c.resolve_context()\n"
        "assert ctx.profile is None and ctx.bundle_target is None\n"
    )
    env = {k: v for k, v in __import__("os").environ.items() if not k.startswith(("BRICKSMITH_", "DATABRICKS_"))}
    done = _run(code, cwd=tmp_path, env=env)
    assert done.returncode == 0, done.stderr


def test_context_show_json(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("BRICKSMITH_FREE_PROFILE", "BRICKSMITH_TARGET", "BRICKSMITH_TEST_ENV"):
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / "e.env"
    env_file.write_text("BRICKSMITH_FREE_PROFILE=p\nBRICKSMITH_TARGET=ci\n")
    assert main(["context", "show", "--json", "--env-file", str(env_file), "--target", "dev"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["profile"], data["bundle_target"], data["environment"]) == ("p", "dev", "free")
    assert data["sources"]["profile"] == "dotenv"
    assert main(["--json", "context", "show"]) == 0


def test_global_options_work_before_and_after_group(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "--profile", "x", "context", "show"]) == 0
    assert json.loads(capsys.readouterr().out)["profile"] == "x"


def test_errors_are_structured_with_stable_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["nope"]) == 2
    assert main(["context", "show", "--json", "--env", "Bad"]) == 3
    err = json.loads(capsys.readouterr().err.splitlines()[-1])
    assert err["error"]["code"] == "config_error"
    assert main(["context", "show", "--env-file", "/nonexistent/.env"]) == 3
    assert "error[config_error]" in capsys.readouterr().err
