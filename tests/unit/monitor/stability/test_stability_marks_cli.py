"""``bricksmith stability mark|unmark|marks``: the row, the INSERT and the marks in force."""
# ruff: noqa: PT018 - dense assertions on rows

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from bricksmith.cli.main import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

BASE = ["--dataset", "orders", "--period", "2025-04", "--reason", "migration", "--author", "ana"]


def _stored(state: Path) -> list[dict[str, str]]:
    return json.loads(state.read_text(encoding="utf-8"))["marks"]


def test_mark_appends_to_the_state_and_prints_the_insert(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    argv = ["stability", "mark", *BASE, "--mark", "excluded", "--state", str(state), "--scope", "main.monitoring",
            "--created-at", "2026-01-01T00:00:00+00:00"]  # fmt: skip
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "INSERT INTO main.monitoring.stability_marks" in out and "'excluded'" in out
    assert [r["period"] for r in _stored(state)] == ["2025-04"]
    assert main(argv) == 0  # the same decision is one row
    assert len(_stored(state)) == 1


def test_mark_refuses_what_the_core_refuses(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["stability", "mark", *BASE, "--mark", "excluded", "--metric", "psi"]) != 0
    assert (
        main(["stability", "mark", *BASE, "--mark", "accepted", "--metric", "psi", "--valid-until", "2999-01-01"]) == 0
    )
    capsys.readouterr()


def test_marks_lists_what_is_in_force_and_unmark_cancels(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    mark = ["stability", "mark", *BASE, "--state", str(state)]
    assert main([*mark, "--mark", "regime_change", "--created-at", "2026-01-01T00:00:00+00:00"]) == 0
    assert main([*mark, "--mark", "accepted", "--period", "2025-05", "--valid-until", "2026-02-01",
                 "--created-at", "2026-01-01T00:00:00+00:00"]) == 0  # fmt: skip
    capsys.readouterr()
    assert main(["stability", "marks", "--state", str(state), "--as-of", "2026-01-15", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [m["mark"] for m in listed["marks"]] == ["regime_change", "accepted"]
    assert main(["stability", "marks", "--state", str(state), "--as-of", "2026-03-01", "--json"]) == 0
    assert [m["mark"] for m in json.loads(capsys.readouterr().out)["marks"]] == ["regime_change"]
    assert main(["stability", "unmark", *BASE, "--state", str(state), "--created-at", "2026-02-01T00:00:00+00:00"]) == 0
    capsys.readouterr()
    assert main(["stability", "marks", "--state", str(state), "--as-of", "2026-01-15", "--dataset", "orders"]) == 0
    assert "1 mark(s) in force" in capsys.readouterr().out
