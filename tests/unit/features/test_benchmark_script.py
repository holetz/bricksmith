"""The benchmark script selects its context like the remote tests: the ``.env`` file is optional."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "features_benchmark.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("features_benchmark", SCRIPT)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake the context and session, and the benchmark itself, recording what the script passed on."""
    script = load_script()
    record: dict[str, Any] = {"script": script}

    def resolve_context(dotenv: dict[str, str] | None = None) -> str:
        record["dotenv"] = dotenv
        return "context"

    class Clients:
        def __init__(self, context: str) -> None:
            record["context"] = context

        def spark(self) -> str:
            return "spark"

    monkeypatch.setattr("bricksmith.context.resolve_context", resolve_context)
    monkeypatch.setattr("bricksmith.clients.Clients", Clients)
    monkeypatch.setattr(script, "run_benchmark", lambda spark, *_a, **_k: record.setdefault("spark", spark) and [])
    monkeypatch.setattr(script, "render_report", lambda *_: "report\n")
    return record


def test_spark_engines_run_without_a_dotenv_file(
    seen: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(seen["script"], "ROOT", tmp_path)  # a checkout without .env: the environment selects it
    assert seen["script"].main(["--sizes", "2x2x1", "--engines", "arrow", "--families", "basic"]) == 0
    assert (seen["dotenv"], seen["context"], seen["spark"]) == ({}, "context", "spark")


def test_an_existing_dotenv_file_is_loaded(
    seen: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("BRICKSMITH_FREE_CATALOG=synthetic\n", encoding="utf-8")
    monkeypatch.setattr(seen["script"], "ROOT", tmp_path)
    assert seen["script"].main(["--sizes", "2x2x1", "--engines", "spark", "--families", "basic"]) == 0
    assert seen["dotenv"] == {"BRICKSMITH_FREE_CATALOG": "synthetic"}
