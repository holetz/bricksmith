"""Backtest a stability configuration over stored profiles: same config reports nothing, a tighter one the new reds."""
# ruff: noqa: PT018 - dense assertions on rows

from __future__ import annotations

import copy
from typing import Any

from bricksmith.monitor.stability import backtest, parse_config
from bricksmith.monitor.stability.backtest import noise_estimate
from bricksmith.monitor.stability.engine import CheckResult, MemoryStore, check
from bricksmith.monitor.stability.tables import merge_rows

DECLARED: dict[str, Any] = {
    "period": "month",
    "columns": ["amount"],
    "persistence": {"immediate_red": True},
    "gate": {"fail_on": ["red"]},
    "limits": {
        "psi": {"green": 50.0, "red": 100.0},
        "mean_shift": {"green": 50.0, "red": 100.0},
        "unseen_rate_delta": {"green": 50.0, "red": 100.0},
    },
}


def _rows(months: int = 6, shift_from: int = 4) -> list[dict[str, object]]:
    return [
        {"month": f"2026-{m + 1:02d}", "amount": float(i % 20 + (6 if m >= shift_from else 0))}
        for m in range(months)
        for i in range(80)
    ]


def _run(store: MemoryStore, declared: dict[str, Any], run_id: str = "r1") -> CheckResult:
    result = check(_rows(), parse_config(declared), store, dataset="orders", run_id=run_id)
    for kind, rows in result.tables.items():
        store.tables[kind] = list(merge_rows(kind, store.tables.get(kind, []), rows))
    return result


def _tighter() -> dict[str, Any]:
    return {**DECLARED, "limits": {**DECLARED["limits"], "psi": {"green": 0.0001, "red": 0.0002}}}


def test_the_current_configuration_reports_zero_differences() -> None:
    store = MemoryStore()
    _run(store, DECLARED)
    report = backtest(parse_config(DECLARED), store, dataset="orders")
    assert report.differences == 0 and not report.changes
    assert not report.requires_refit and not report.judgment_changed
    assert report.periods and all(g.before == g.after for g in report.gates)


def test_tightening_a_limit_reports_exactly_the_new_reds_the_next_run_produces() -> None:
    store = MemoryStore()
    _run(store, DECLARED)
    before = copy.deepcopy(store.tables)
    report = backtest(parse_config(_tighter()), store, dataset="orders")
    assert report.judgment_changed and report.changes
    assert store.tables == before  # a backtest never writes

    real = MemoryStore(copy.deepcopy(before))
    _run(real, _tighter(), "r2")
    stored = {(r["period"], r["segment"], r["column"], r["metric"]): r["status"] for r in before["results"]}
    actual = {
        (r["period"], r["segment"], r["column"], r["metric"]): r["status"]
        for r in real.tables["results"]
        if (r["period"], r["segment"], r["column"], r["metric"]) in stored
    }
    expected = {(c.period, c.segment, c.column, c.metric): c.after for c in report.changes if c.field == "status"}
    moved = {k: v for k, v in actual.items() if stored[k] != v}
    assert expected == moved
    assert any(c.after == "red" for c in report.changes)


def test_alerts_that_would_open_and_the_gate_per_period_are_reported() -> None:
    store = MemoryStore()
    _run(store, DECLARED)
    report = backtest(parse_config(_tighter()), store, dataset="orders")
    assert report.alerts_opened and all(a["state"] == "open" for a in report.alerts_opened)
    assert any(g.before != g.after for g in report.gates)
    data = report.to_dict()
    assert data["differences"] == report.differences and data["gates"] and data["series"]
    assert "difference(s)" in report.summary()


def test_periods_narrow_what_is_reported_not_what_is_replayed() -> None:
    store = MemoryStore()
    _run(store, DECLARED)
    last = max(r["period"] for r in store.tables["results"])
    report = backtest(parse_config(_tighter()), store, dataset="orders", periods=[last])
    assert report.periods == [last]
    assert {c.period for c in report.changes} <= {last}
    ranged = backtest(parse_config(_tighter()), store, dataset="orders", periods=["2026-04..2026-06"])
    assert ranged.periods == ["2026-04", "2026-05", "2026-06"]


def test_a_change_of_bins_requires_a_refit_and_compares_nothing() -> None:
    store = MemoryStore()
    _run(store, DECLARED)
    report = backtest(parse_config({**DECLARED, "fit_periods": 2}), store, dataset="orders")
    assert report.requires_refit and report.differences == 0 and "requires refit" in report.summary()
    assert not report.series and not report.gates


def test_an_unknown_dataset_has_nothing_to_replay() -> None:
    store = MemoryStore()
    _run(store, DECLARED)
    report = backtest(parse_config(DECLARED), store, dataset="nope")
    assert report.requires_refit


def _noisy_store() -> MemoryStore:
    store = MemoryStore()
    for month in range(1, 15):
        store.tables.setdefault("results", []).append(
            {
                "dataset": "orders", "version": 1, "period": f"2026-{month:02d}", "segment": "", "column": "",
                "metric": "row_count", "value": 1000.0 + (month * 37) % 90, "status": "green", "direction": "both",
            }
        )  # fmt: skip
    return store


def test_noise_estimate_is_seeded_and_close_to_the_nominal_rates() -> None:
    store = _noisy_store()
    config = parse_config({**DECLARED, "trend": {"window": 12, "min_points": 6}, "trend_metrics": ["row_count"]})
    rows = store.tables["results"]
    first = noise_estimate(config, rows, runs=400, seed=7)
    again = noise_estimate(config, rows, runs=400, seed=7)
    assert first == again and first.series and first.series[0].points == 14
    assert 0.0 < first.flagged < 0.12 and first.red < 0.03


def test_a_tighter_trend_flags_more_noise_and_a_disabled_trend_none() -> None:
    rows = _noisy_store().tables["results"]
    base = {**DECLARED, "trend_metrics": ["row_count"]}
    loose = noise_estimate(parse_config({**base, "trend": {"yellow": 3.0, "red": 4.0}}), rows, runs=300, seed=1)
    tight = noise_estimate(parse_config({**base, "trend": {"yellow": 1.0, "red": 1.5}}), rows, runs=300, seed=1)
    assert tight.flagged > loose.flagged and tight.red > loose.red
    off = noise_estimate(parse_config({**base, "trend": False}), rows, runs=50)
    assert off.flagged == 0.0 and not off.series


def test_the_report_carries_the_noise_estimate_only_when_asked() -> None:
    store = MemoryStore()
    _run(store, DECLARED)
    assert backtest(parse_config(DECLARED), store, dataset="orders").noise is None
    report = backtest(parse_config(DECLARED), store, dataset="orders", noise_runs=20, seed=3)
    assert report.noise is not None and report.noise.runs == 20 and report.to_dict()["noise"]["seed"] == 3
