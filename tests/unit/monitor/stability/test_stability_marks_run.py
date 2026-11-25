"""Marks in the stability run: labels, the gate, alerts, the fingerprint and the replay of a removed mark."""
# ruff: noqa: PT018 - dense assertions on rows

from __future__ import annotations

from typing import Any

from bricksmith.monitor.stability import MemoryStore, backtest, check, parse_config
from bricksmith.monitor.stability.engine import CheckResult, judgment_fingerprint
from bricksmith.monitor.stability.marks import ACCEPTED, EXCLUDED, INVALID, REGIME_CHANGE, UNMARKED, mark_row
from bricksmith.monitor.stability.tables import MARKS, SCHEMAS, merge_rows, view_statements

NOW = "2026-04-01T00:00:00+00:00"
GATE = {"period": "month", "gate": {"fail_on": ["red"], "periods": "profiled"}, "min_rows": 0, "baseline": "previous"}


def _rows(counts: list[int]) -> list[dict[str, object]]:
    return [{"month": f"2025-{m:02d}", "x": i % 3} for m, n in enumerate(counts, 1) for i in range(n)]


def _mark(mark: str, period: str, at: str = "2026-03-01T00:00:00+00:00", **extra: str) -> dict[str, Any]:
    return mark_row(dataset="d", period=period, mark=mark, reason="known load", author="ana", created_at=at, **extra)


def _run(
    counts: list[int],
    store: MemoryStore,
    marks: list[dict[str, Any]] | None = None,
    run_id: str = "r",
    **declared: Any,  # noqa: ANN401
) -> CheckResult:
    store.tables[MARKS] = list(marks or [])
    config = parse_config({**GATE, "columns": {"x": {"edges": [0, 1.5, 3]}}, **declared})
    result = check(_rows(counts), config, store, dataset="d", run_id=run_id, now=NOW)
    for kind, rows in result.tables.items():
        store.tables[kind] = merge_rows(kind, store.tables.get(kind, []), rows)
    return result


def _volume(result: CheckResult) -> dict[str, dict[str, Any]]:
    return {r["period"]: r for r in result.tables["results"] if r["metric"] == "volume_change" and not r["segment"]}


SPIKE = [30, 30, 30, 300, 30, 30]


def test_the_marks_table_is_part_of_the_run_and_results_carry_the_mark() -> None:
    result = _run(SPIKE, MemoryStore())
    assert result.tables[MARKS] == []
    assert {"mark", "mark_reason"} <= set(SCHEMAS["results"])
    assert {r["mark"] for r in result.tables["results"]} == {""}


def test_an_accepted_red_result_keeps_its_color_but_not_the_gate() -> None:
    assert _run(SPIKE, MemoryStore()).outcome.failed
    store = MemoryStore()
    result = _run(SPIKE, store, [_mark(ACCEPTED, "2025-04"), _mark(ACCEPTED, "2025-05")])
    spike = _volume(result)["2025-04"]
    assert spike["status"] == "red" and spike["mark"] == ACCEPTED and spike["mark_reason"] == "known load"
    assert result.outcome.red == 0 and not result.outcome.failed


def test_an_accepted_mark_holds_the_alert_of_its_last_period_out_of_the_gate() -> None:
    shifted = [30, 30, 30, 300, 300, 300]
    declared = {"baseline": {"kind": "fixed", "start": "2025-01", "end": "2025-03"}, "gate": {"fail_on": ["alert"]}}
    assert _run(shifted, MemoryStore(), **declared).outcome.failed
    held = _run(shifted, MemoryStore(), [_mark(ACCEPTED, "2025-06")], **declared)
    assert held.outcome.open_alerts == 0 and not held.outcome.failed


def test_an_accepted_mark_can_be_narrowed_to_one_metric() -> None:
    marks = [_mark(ACCEPTED, "2025-04", metric="volume_change")]
    result = _run(SPIKE, MemoryStore(), marks, gate={"fail_on": ["red"], "periods": "profiled"})
    labeled = {
        (r["metric"], r["mark"]) for r in result.tables["results"] if r["period"] == "2025-04" and not r["column"]
    }
    assert ("volume_change", ACCEPTED) in labeled and ("row_count", "") in labeled


def test_an_excluded_period_leaves_later_baselines_and_unmark_restores_the_judgment() -> None:
    store = MemoryStore()
    plain = _volume(_run(SPIKE, store))
    store = MemoryStore()
    excluded = _volume(_run(SPIKE, store, [_mark(EXCLUDED, "2025-04")]))
    assert plain["2025-05"]["baseline"] == 300 and excluded["2025-05"]["baseline"] == 30
    assert excluded["2025-04"]["mark"] == EXCLUDED
    restored = _volume(
        _run(SPIKE, store, [*store.tables[MARKS], _mark(UNMARKED, "2025-04", "2026-03-02T00:00:00+00:00")])
    )
    assert restored["2025-05"]["baseline"] == 300 and restored["2025-05"]["mark"] == ""


def test_a_regime_change_restarts_the_baseline_and_an_invalid_period_is_informational() -> None:
    shift = [30, 30, 30, 90, 90, 90]
    result = _run(
        shift,
        MemoryStore(),
        [_mark(REGIME_CHANGE, "2025-04")],
        baseline={"kind": "rolling", "periods": 3, "min_periods": 1},
    )
    found = _volume(result)
    assert found["2025-04"]["status"] == "no_baseline" and found["2025-05"]["baseline"] == 90
    invalid = _volume(_run(SPIKE, MemoryStore(), [_mark(INVALID, "2025-04")]))
    assert invalid["2025-04"]["status"] == "informational" and invalid["2025-05"]["status"] == "green"


def test_a_new_mark_changes_the_judgment_fingerprint_and_rejudges_the_window() -> None:
    config = parse_config({**GATE, "columns": {"x": {"edges": [0, 1.5, 3]}}})
    base = judgment_fingerprint(config)
    assert judgment_fingerprint(config, [], []) == base
    assert judgment_fingerprint(config, [], [_mark(EXCLUDED, "2025-04")]) != base
    store = MemoryStore()
    _run(SPIKE, store)
    first = store.tables["runs"][0]["judgment_fingerprint"]
    _run(SPIKE, store, [_mark(EXCLUDED, "2025-04")], run_id="r2")
    assert first in {r["judgment_fingerprint"] for r in store.tables["runs"]}
    assert len({r["judgment_fingerprint"] for r in store.tables["runs"]}) == 2


def test_an_expired_mark_no_longer_holds() -> None:
    expired = _mark(EXCLUDED, "2025-04", valid_until="2026-03-31")
    assert _volume(_run(SPIKE, MemoryStore(), [expired]))["2025-05"]["baseline"] == 300


def test_backtest_replays_with_the_stored_marks() -> None:
    store = MemoryStore()
    _run(SPIKE, store)
    config = parse_config({**GATE, "columns": {"x": {"edges": [0, 1.5, 3]}}})
    plain = backtest(config, store, dataset="d", now=NOW)
    store.tables[MARKS] = [_mark(EXCLUDED, "2025-04")]
    marked = backtest(config, store, dataset="d", now=NOW)
    assert not plain.judgment_changed and marked.judgment_changed
    assert any(c.period == "2025-05" and c.field == "status" for c in marked.changes)


def test_the_active_marks_view_lists_the_marks_in_force() -> None:
    view = next(s for s in view_statements("main") if "active_marks" in s)
    assert "main.stability_marks" in view and "unmarked" in view and "valid_until" in view
