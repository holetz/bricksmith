"""Reprocessing: re-profile and re-judge explicit periods after a backfill, and the gate over profiled periods."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

import json
import textwrap
from typing import TYPE_CHECKING

import pytest

from bricksmith.cli.main import main
from bricksmith.context import ExecutionContext
from bricksmith.errors import EXIT_VALIDATION
from bricksmith.graph import load_graph
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import Gate, parse_config
from bricksmith.monitor.stability.engine import CheckResult, MemoryStore, check
from bricksmith.monitor.stability.tables import RUNS, SCHEMAS, merge_rows
from bricksmith.plan import build_plan
from bricksmith.runner import InMemoryTableIO, RunReport, apply_plan

if TYPE_CHECKING:
    from pathlib import Path


def test_declaration_gate_periods_default_and_validation() -> None:
    plain = parse_config({"period": "month", "gate": {"fail_on": ["red"]}})
    profiled = parse_config({"period": "month", "gate": {"fail_on": ["red"], "periods": "profiled"}})
    assert plain.gate.periods == "latest" and profiled.gate.periods == "profiled"
    assert "periods" not in plain.gate.to_dict() and profiled.gate.to_dict()["periods"] == "profiled"
    assert plain.to_dict()["gate"] == Gate(("red",)).to_dict()
    for bad in ("all", 1, None):
        with pytest.raises(MonitorError):
            parse_config({"period": "month", "gate": {"periods": bad}})


def test_schema_runs_records_the_reprocessed_periods() -> None:
    assert SCHEMAS[RUNS]["periods_reprocessed"] == "STRING"


def _month(month: str, count: int, shift: float = 0.0) -> list[dict[str, object]]:
    return [{"month": month, "amount": float(i % 20) + i * 0.013 + shift} for i in range(count)]


def _rows(shifted: str = "") -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for m in range(1, 6):
        month = f"2026-{m:02d}"
        out += _month(month, 80, shift=40.0 if month == shifted else 0.0)
    return out


def _check(
    rows: list[dict[str, object]],
    store: MemoryStore,
    run_id: str,
    reprocess: list[str] | None = None,
    **declared: object,
) -> CheckResult:
    config = parse_config(
        {
            "period": "month",
            "min_rows": 10,
            "psi_limits": "fixed",
            "min_rows_per_bin": 0,
            "baseline": "previous",
            **declared,
        }
    )
    return check(rows, config, store, dataset="orders", run_id=run_id, reprocess=reprocess or ())


def _commit(store: MemoryStore, result: CheckResult) -> None:
    for kind, rows in result.tables.items():
        store.tables[kind] = merge_rows(kind, store.tables.get(kind, []), rows)


def _psi(result: CheckResult, period: str) -> str:
    found = [r for r in result.tables["results"] if r["period"] == period and r["metric"] == "psi" and not r["segment"]]
    return found[0]["status"]


def test_engine_reprocess_check_reprofiles_a_changed_older_period() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(), store, "r1"))
    stale = _check(_rows("2026-02"), store, "r2")
    assert not [r for r in stale.tables["profiles"] if r["period"] == "2026-02"]  # the status quo: unseen
    fixed = _check(_rows("2026-02"), store, "r3", reprocess=["2026-02", "2026-02"])
    assert {r["period"] for r in fixed.tables["profiles"]} == {"2026-02", "2026-05"}  # the refresh range stays
    assert _psi(fixed, "2026-02") == "red"
    assert fixed.tables["runs"][0]["periods_reprocessed"] == "2026-02"
    assert fixed.outcome.periods_profiled == 2


def test_engine_reprocess_check_replays_the_alerts_of_the_window() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(), store, "r1", persistence={"immediate_red": True}))
    fixed = _check(_rows("2026-02"), store, "r2", reprocess=["2026-02"], persistence={"immediate_red": True})
    assert any(a["state"] == "open" and a["opened_period"] in ("2026-02", "2026-03") for a in fixed.tables["alerts"])
    assert len({r["period"] for r in fixed.tables["results"]}) == 5  # the whole window is judged again


def test_engine_reprocess_check_without_it_leaves_no_record() -> None:
    result = _check(_rows(), MemoryStore(), "r1")
    assert result.tables["runs"][0]["periods_reprocessed"] == ""


def test_engine_reprocess_check_rejects_periods_outside_the_data_or_the_window() -> None:
    store = MemoryStore()
    with pytest.raises(MonitorError, match="2027-01"):
        _check(_rows(), store, "r1", reprocess=["2027-01"])
    with pytest.raises(MonitorError, match="2026-01"):
        _check(_rows(), store, "r1", reprocess=["2026-01"], max_periods=2)


def test_gate_latest_ignores_a_red_reprocessed_period_and_profiled_counts_it() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(), store, "r1"))
    latest = _check(_rows("2026-02"), store, "r2", reprocess=["2026-02"], gate={"fail_on": ["red"]})
    assert latest.outcome.status != "failed" and latest.outcome.red == 0
    profiled = _check(
        _rows("2026-02"), store, "r3", reprocess=["2026-02"], gate={"fail_on": ["red"], "periods": "profiled"}
    )
    assert profiled.outcome.failed and profiled.outcome.red > 0
    assert "reprocessed 2026-02" in profiled.outcome.message and "gate failed on" in profiled.outcome.message


def test_gate_profiled_judges_only_the_periods_of_the_run() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows("2026-02"), store, "r1"))  # first sight: every period is profiled
    quiet = _check(_rows("2026-02"), store, "r2", gate={"fail_on": ["red"], "periods": "profiled"})
    assert not quiet.outcome.failed  # 2026-02 is red in storage but was not profiled by this run


def test_revision_reports_each_reprocessed_period_against_its_first_publication() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(), store, "r1", revision=True))
    fixed = _check(_rows("2026-02"), store, "r2", reprocess=["2026-02"], revision=True)
    found = {
        (r["period"], r["column"], r["metric"]): r
        for r in fixed.tables["results"]
        if r["segment"] == "" and r["metric"].startswith("revision_")
    }
    assert found[("2026-02", "amount", "revision_psi")]["status"] == "red"
    assert found[("2026-03", "amount", "revision_psi")]["status"] == "green"
    assert fixed.tables["revisions"] == []  # the first publication is never rewritten


def test_engine_reprocess_check_takes_ranges_and_can_skip_what_the_data_lacks() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(), store, "r1"))
    ranged = _check(_rows("2026-02"), store, "r2", reprocess=["2026-02..2026-03"])
    assert ranged.tables["runs"][0]["periods_reprocessed"] == "2026-02,2026-03"
    config = parse_config({"period": "month", "min_rows": 10})
    kept = check(
        _rows(), config, store, dataset="orders", run_id="r3", reprocess=["2026-02", "2030-01"], skip_unknown=True
    )
    assert kept.tables["runs"][0]["periods_reprocessed"] == "2026-02"


def _project(root: Path) -> Path:
    path = root / "catalogs/main/sales/orders.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(
            """
            from bricksmith.tasks import task

            @task(kind="table", outputs="orders", metadata={"stability": {
                "period": "month", "min_rows": 10, "psi_limits": "fixed", "min_rows_per_bin": 0, "baseline": "previous",
                "gate": {"fail_on": ["red"], "periods": "profiled"}}})
            def orders(ctx):
                shifted = ctx.execution.parameters.get("shifted")
                rows = []
                for m in range(1, 6):
                    month = f"2026-{m:02d}"
                    shift = 40.0 if month == shifted else 0.0
                    rows += [{"month": month, "amount": float(i % 20) + i * 0.013 + shift} for i in range(80)]
                ctx.write("orders", rows)
            """
        ),
        encoding="utf-8",
    )
    return root


def _run(root: Path, io: InMemoryTableIO, shifted: str = "", reprocess: tuple[str, ...] = ()) -> RunReport:
    execution = ExecutionContext(environment="test", parameters={"shifted": shifted})
    graph = load_graph(root, context=execution)
    return apply_plan(
        build_plan(graph), graph, project_root=root, execution=execution, table_io=io, reprocess=reprocess
    )


def test_runner_reprocess_reprofiles_the_same_periods_without_the_command(tmp_path: Path) -> None:
    root = _project(tmp_path)
    io = InMemoryTableIO()
    assert _run(root, io).ok
    stale = _run(root, io, shifted="2026-02")
    assert stale.ok and stale.results[0].stability[0]["periods_profiled"] == 1
    report = _run(root, io, shifted="2026-02", reprocess=("2026-02", "2030-01"))
    outcome = report.results[0].stability[0]
    assert not report.ok and outcome["status"] == "failed" and "reprocessed 2026-02" in str(outcome["message"])
    runs = io.tables["main.sales.stability_runs"]
    assert isinstance(runs, list) and [r["periods_reprocessed"] for r in runs][-1] == "2026-02"


def test_command_reprocess_profiles_a_changed_older_period_and_fails_the_gate(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    declared = {
        "period": "month",
        "min_rows": 10,
        "psi_limits": "fixed",
        "min_rows_per_bin": 0,
        "baseline": "previous",
        "gate": {"fail_on": ["red"], "periods": "profiled"},
    }
    config.write_text(json.dumps(declared), encoding="utf-8")
    state = tmp_path / "state.json"

    def write(rows: list[dict[str, object]]) -> list[str]:
        data = tmp_path / "orders.jsonl"
        data.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return ["--config", str(config), "--data", str(data), "--state", str(state), "--dataset", "orders", "--json"]

    assert main(["stability", "check", *write(_rows())]) == 0
    assert main(["stability", "check", *write(_rows("2026-02"))]) == 0  # the backfill is invisible to check
    code = main(["stability", "reprocess", *write(_rows("2026-02")), "--periods", "2026-02..2026-03"])
    assert code == EXIT_VALIDATION  # the red reprocessed period gates
    stored = json.loads(state.read_text(encoding="utf-8"))
    assert stored["runs"][-1]["periods_reprocessed"] == "2026-02,2026-03"
    assert any(r["period"] == "2026-02" and r["metric"] == "psi" and r["status"] == "red" for r in stored["results"])
