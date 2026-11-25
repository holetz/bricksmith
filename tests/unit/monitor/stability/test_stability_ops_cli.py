"""The workspace verbs: ``stability status|series|actions|reprocess`` and ``--warehouse`` on marks and actions."""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace
from typing import Any

import pytest

from bricksmith.cli.main import main
from bricksmith.monitor.stability import cli
from bricksmith.monitor.stability.ops import Operations

WORKSPACE = ["--warehouse", "wh1", "--scope", "main.mon"]


class _Sql:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.rows: list[dict[str, Any]] = []

    def run(self, statement: str, params: Any = None) -> list[dict[str, Any]]:  # noqa: ANN401
        self.calls.append((statement, dict(params or {})))
        return self.rows


class _Jobs:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run_now(self, **kwargs: Any) -> SimpleNamespace:  # noqa: ANN401
        self.calls.append(kwargs)
        return SimpleNamespace(run_id=9)


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch) -> tuple[_Sql, _Jobs]:
    sql, jobs = _Sql(), _Jobs()
    monkeypatch.setattr(
        cli,
        "_ops",
        lambda args: Operations(
            sql, args.scope, prefix=args.prefix, jobs=jobs, now=lambda: "2026-02-01T00:00:00+00:00"
        ),
    )
    return sql, jobs


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> dict[str, Any]:
    assert main([*argv, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_status_series_and_actions_read_through_the_operations(
    workspace: tuple[_Sql, _Jobs], capsys: pytest.CaptureFixture[str]
) -> None:
    sql, _ = workspace
    sql.rows = [{"dataset": "orders", "status": "red"}]
    assert _run(capsys, "stability", "status", *WORKSPACE)["datasets"] == sql.rows
    assert "stability_datasets" in sql.calls[-1][0]
    assert _run(capsys, "stability", "series", *WORKSPACE, "--dataset", "orders", "--metric", "psi")["series"]
    assert sql.calls[-1][1] == {"dataset": "orders", "metric": "psi"}
    _run(capsys, "stability", "actions", *WORKSPACE, "--queue")
    assert "stability_open_alerts" in sql.calls[-1][0]
    _run(capsys, "stability", "actions", *WORKSPACE, "--dataset", "orders")
    assert "stability_actions" in sql.calls[-1][0]


def test_mark_unmark_and_act_write_to_the_workspace(
    workspace: tuple[_Sql, _Jobs], capsys: pytest.CaptureFixture[str]
) -> None:
    sql, _ = workspace
    base = ["--dataset", "orders", "--period", "2025-04", "--reason", "migration", "--author", "ana", *WORKSPACE]
    assert _run(capsys, "stability", "mark", *base, "--mark", "excluded")["mark"]["mark"] == "excluded"
    assert sql.calls[-1][0].startswith("INSERT INTO main.mon.stability_marks")
    _run(capsys, "stability", "unmark", *base)
    assert sql.calls[-1][1]["mark"] == "unmarked"
    act = [
        "stability",
        "action",
        "--dataset",
        "orders",
        "--alert-id",
        "a1",
        "--reason",
        "r",
        "--author",
        "ana",
        *WORKSPACE,
    ]
    assert _run(capsys, *act, "--action", "analysis")["action"]["action"] == "analysis"
    assert sql.calls[-1][0].startswith("INSERT INTO main.mon.stability_actions")
    sql.rows = []
    assert _run(capsys, "stability", "marks", *WORKSPACE)["marks"] == []


def test_reprocess_with_a_job_starts_the_run(workspace: tuple[_Sql, _Jobs], capsys: pytest.CaptureFixture[str]) -> None:
    _, jobs = workspace
    out = _run(capsys, "stability", "reprocess", "--job", "42", "--periods", "2026-01..2026-03", *WORKSPACE)
    assert out["reprocess"]["run_id"] == 9
    assert jobs.calls == [{"job_id": 42, "job_parameters": {"reprocess": "2026-01..2026-03"}}]


def test_workspace_verbs_refuse_without_a_warehouse_and_reprocess_needs_a_target(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["stability", "reprocess", "--periods", "2026-01", "--data", "x.csv"]) != 0
    assert main(["stability", "marks"]) != 0
    capsys.readouterr()


def test_the_default_builder_needs_warehouse_and_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[object] = []
    monkeypatch.setattr(cli, "context_from_args", called.append)
    with pytest.raises(cli.UsageError):
        cli._ops(argparse.Namespace(warehouse="", scope="x", prefix="p"))  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
    assert called == []
