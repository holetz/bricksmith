"""Reprocessing: re-profile and re-judge explicit periods after a backfill, and the gate over profiled periods."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import Gate, parse_config
from bricksmith.monitor.stability.engine import CheckResult, MemoryStore, check
from bricksmith.monitor.stability.tables import RUNS, SCHEMAS, merge_rows


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
    config = parse_config({"period": "month", "min_rows": 10, "baseline": "previous", **declared})
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
