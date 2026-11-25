"""The kept segment set is stable across runs: a kept segment whose volume collapses is still judged."""
# ruff: noqa: PT018 - dense assertions on run outcomes

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.monitor.stability import MemoryStore, check, parse_config
from bricksmith.monitor.stability.profile import fit_rows_binning, profile_rows
from bricksmith.monitor.stability.tables import merge_rows

if TYPE_CHECKING:
    from collections.abc import Mapping

    from bricksmith.monitor.stability.engine import CheckResult

BASE = ("2026-01", "2026-02", "2026-03")
STEADY = {"a": 300, "b": 200, "c": 100}
DECLARATION = {
    "period": "month",
    "columns": {"x": {"edges": [0, 5, 10]}},
    "segments": ["ch"],
    "max_segments": 2,
    "baseline": {"kind": "fixed", "start": "2026-01", "end": "2026-03"},
    "min_rows": 0,
    "persistence": {"immediate_red": True},
    "gate": {"fail_on": ["red"]},
}


def rows_of(volumes: Mapping[str, Mapping[str, int]]) -> list[dict[str, Any]]:
    return [
        {"month": month, "ch": ch, "x": float(i % 10)}
        for month, counts in volumes.items()
        for ch, n in counts.items()
        for i in range(n)
    ]


def run(rows: list[dict[str, Any]], store: MemoryStore, run_id: str, **overrides: object) -> CheckResult:
    found = check(rows, parse_config({**DECLARATION, **overrides}), store, dataset="d", run_id=run_id, now="t")
    for kind, items in found.tables.items():
        store.tables[kind] = merge_rows(kind, store.tables.get(kind, []), items)
    return found


def volume(found: CheckResult, period: str, segment: str) -> dict[str, Any] | None:
    return next(
        (
            r
            for r in found.tables["results"]
            if (r["period"], r["segment"], r["column"], r["metric"]) == (period, segment, "", "volume_change")
        ),
        None,
    )


def history(latest: Mapping[str, int]) -> list[dict[str, Any]]:
    return rows_of({**dict.fromkeys((*BASE, "2026-04"), STEADY), "2026-05": latest})


def test_a_kept_segment_whose_volume_collapses_is_still_judged() -> None:
    store = MemoryStore()
    run(rows_of(dict.fromkeys((*BASE, "2026-04"), STEADY)), store, "r1")
    second = run(history({"a": 300, "b": 5, "c": 150}), store, "r2")
    collapsed = volume(second, "2026-05", "ch=b")
    assert collapsed is not None and collapsed["status"] == "red"
    assert collapsed["value"] == 5 / 200 - 1
    assert second.outcome.status == "failed" and second.outcome.red >= 1
    assert volume(second, "2026-05", "ch=c") is None  # still truncated: c never entered the kept set


def test_a_kept_segment_that_vanishes_is_judged_with_zero_rows() -> None:
    store = MemoryStore()
    run(rows_of(dict.fromkeys((*BASE, "2026-04"), STEADY)), store, "r1")
    second = run(history({"a": 300, "c": 150}), store, "r2")
    gone = volume(second, "2026-05", "ch=b")
    assert gone is not None and gone["status"] == "red" and gone["value"] == -1.0 and gone["rows"] == 0
    zero = [r for r in second.tables["profiles"] if r["period"] == "2026-05" and r["segment"] == "ch=b"]
    assert zero and all(r["count"] == 0 for r in zero)


def test_a_segment_with_stored_history_stays_kept_when_it_falls_out_of_the_top() -> None:
    store = MemoryStore()
    run(rows_of(dict.fromkeys((*BASE, "2026-04"), STEADY)), store, "r1")
    # over the loaded periods c (400 + 1000) now outranks b (800 + 5); b keeps its history, so it stays kept
    second = run(history({"a": 300, "b": 5, "c": 1000}), store, "r2")
    assert {r["segment"] for r in second.tables["profiles"]} == {"", "ch=a", "ch=b", "ch=c"}
    collapsed = volume(second, "2026-05", "ch=b")
    assert collapsed is not None and collapsed["status"] == "red"


def test_reruns_over_the_same_history_are_identical() -> None:
    store = MemoryStore()
    rows = history({"a": 300, "b": 5, "c": 150})
    run(rows_of(dict.fromkeys((*BASE, "2026-04"), STEADY)), store, "r1")
    second = run(rows, store, "r2")
    third = run(rows, store, "r2")
    assert second.tables["profiles"] == third.tables["profiles"]
    assert second.tables["results"] == third.tables["results"]
    assert second.outcome == third.outcome


def test_first_and_later_runs_keep_the_same_segments() -> None:
    rows = history({"a": 300, "b": 5, "c": 150})
    once = run(rows, MemoryStore(), "r1")
    store = MemoryStore()
    run(rows_of(dict.fromkeys((*BASE, "2026-04"), STEADY)), store, "r1")
    later = run(rows, store, "r2")
    segments = {r["segment"] for r in once.tables["profiles"] if r["period"] == "2026-05"}
    assert segments == {r["segment"] for r in later.tables["profiles"] if r["period"] == "2026-05"}
    assert segments == {"", "ch=a", "ch=b"}


def test_a_segment_gets_no_zero_rows_before_its_first_period() -> None:
    config = parse_config(DECLARATION)
    rows = rows_of({**{p: {"a": 300, "b": 200} for p in BASE}, "2026-04": {"a": 300, "b": 200, "n": 900}})
    binning, _ = fit_rows_binning(rows, config, BASE, dataset="d", version=1)
    profile, info = profile_rows(binning, rows, config, [*BASE, "2026-04"], dataset="d")
    first = {r["period"] for r in profile if r["segment"] == "ch=n"}
    assert first == {"2026-04"}
    assert info.truncated == {"ch": 1}
    results = check(rows, config, MemoryStore(), dataset="d", run_id="r", now="t")
    fresh = volume(results, "2026-04", "ch=n")
    assert fresh is not None and fresh["status"] == "no_baseline"
