"""The benchmark script selects its context like the remote tests: the ``.env`` file is optional."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import features_benchmark
import pytest

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


def load_script() -> ModuleType:
    return features_benchmark


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


def test_options_reach_the_benchmark_and_a_partial_run_is_labelled(
    seen: dict[str, Any], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    script = seen["script"]

    def run_benchmark(*_args: object, **options: Any) -> list[Any]:  # noqa: ANN401 - a recording fake
        seen["options"] = options
        options["progress"]("[1/1] working")
        options["budget"].skip("reference/basic/2x2x1")
        return []

    notes: list[Any] = []
    monkeypatch.setattr(script, "run_benchmark", run_benchmark)
    monkeypatch.setattr(script, "render_report", lambda _timings, given: notes.extend(given) or "report\n")
    args = ["--sizes", "2x2x1", "--engines", "reference", "--single-closing", "--budget", "30"]
    assert script.main(args) == 0
    captured = capsys.readouterr()
    assert captured.out == "report\n"
    assert captured.err == "[1/1] working\n"
    assert seen["options"]["single_closing"] is True
    assert seen["options"]["budget"].seconds == 30
    assert "mode: single closing (the last closing of each size)" in notes
    assert "partial: the 30 s time budget ran out; 1 measurement(s) not run" in notes
    assert "not run: reference/basic/2x2x1" in notes
