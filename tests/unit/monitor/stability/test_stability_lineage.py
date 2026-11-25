"""Table-level series across binning versions: totals, baselines, trend history and alert identity survive a refit."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

from bricksmith.monitor.stability.config import parse_config
from bricksmith.monitor.stability.engine import CheckResult, MemoryStore, check
from bricksmith.monitor.stability.metrics import index_profiles, table_totals
from bricksmith.monitor.stability.profile import fit_rows_binning, periods_of_rows, profile_rows
from bricksmith.monitor.stability.tables import merge_rows


def _data(counts: dict[str, int], shifted: tuple[str, ...] = ()) -> list[dict[str, object]]:
    return [{"month": m, "x": 9 if m in shifted else i % 10, "y": i % 4} for m, n in counts.items() for i in range(n)]


def _check(rows: list[dict[str, object]], store: MemoryStore, run_id: str, **declared: object) -> CheckResult:
    config = parse_config({"period": "month", "min_rows": 0, "baseline": "previous", **declared})
    return check(rows, config, store, dataset="d", run_id=run_id, now="2026-09-01T00:00:00+00:00")


def _commit(store: MemoryStore, result: CheckResult) -> None:
    for kind, rows in result.tables.items():
        store.tables[kind] = merge_rows(kind, store.tables.get(kind, []), rows)


def _metric(result: CheckResult, metric: str, period: str, column: str = "") -> dict:
    (row,) = [
        r
        for r in result.tables["results"]
        if r["metric"] == metric and r["period"] == period and r["column"] == column and r["segment"] == ""
    ]
    return row


def test_totals_take_the_newest_version_and_count_one_column_per_version() -> None:
    config = parse_config({"period": "month", "min_rows": 0})
    rows = _data({"2026-01": 10, "2026-02": 20})
    binning, _ = fit_rows_binning(rows, config, ["2026-01"], dataset="d", version=1)
    old = profile_rows(binning, rows, config, periods_of_rows(rows, "month"), dataset="d")[0]
    newer = _data({"2026-02": 30, "2026-03": 40})
    binning2, _ = fit_rows_binning(newer, config, ["2026-02"], dataset="d", version=2)
    fresh = profile_rows(binning2, newer, config, ["2026-03"], dataset="d")[0]
    totals = table_totals(index_profiles(binning2, fresh), old)
    assert totals.rows[""] == {"2026-01": 10, "2026-02": 20, "2026-03": 40}
    assert totals.periods == ["2026-01", "2026-02", "2026-03"]


def test_memory_store_filters_a_read_to_the_given_columns() -> None:
    store = MemoryStore({"profiles": [{"dataset": "d", "column": "x"}, {"dataset": "d", "column": "y"}]})
    assert [r["column"] for r in store.rows("profiles", "d", columns=["y"])] == ["y"]
    assert len(store.rows("profiles", "d")) == 2


def test_volume_change_continues_across_a_refit_when_history_left_the_table() -> None:
    store = MemoryStore()
    _commit(store, _check(_data({"2026-01": 100, "2026-02": 100, "2026-03": 100}), store, "r1"))
    purged = _data({"2026-04": 150, "2026-05": 150})
    refit = _check(purged, store, "r2", columns=["x"])
    assert refit.outcome.version == 2
    _commit(store, refit)
    april = _metric(refit, "volume_change", "2026-04")
    assert april["baseline"] == 100.0 and april["value"] == 0.5 and april["status"] != "no_baseline"
    assert not [r for r in refit.tables["results"] if r["period"] == "2026-03"]
    again = _check(purged, store, "r3", columns=["x"])
    assert again.outcome.version == 2 and again.tables["binnings"] == []


def test_a_single_version_judges_exactly_as_before_when_nothing_is_earlier() -> None:
    result = _check(_data({"2026-01": 100, "2026-02": 100}), MemoryStore(), "r1")
    assert _metric(result, "volume_change", "2026-01")["status"] == "no_baseline"
    assert _metric(result, "volume_change", "2026-02")["baseline"] == 100.0


def test_table_alerts_survive_a_refit_and_column_alerts_are_superseded() -> None:
    store = MemoryStore()
    persistence = {"immediate_red": True}
    stable = {"2026-01": 100, "2026-02": 100, "2026-03": 400, "2026-04": 400}
    first = _check(_data(stable, ("2026-03", "2026-04")), store, "r1", persistence=persistence)
    (volume,) = [a for a in first.tables["alerts"] if a["metric"] == "volume_change" and a["state"] == "open"]
    _commit(store, first)
    refit = _check(_data(stable, ("2026-03", "2026-04")), store, "r2", persistence=persistence, columns=["x"])
    assert refit.outcome.version == 2
    kept = [a for a in refit.tables["alerts"] if a["alert_id"] == volume["alert_id"]]
    assert len(kept) == 1 and kept[0]["state"] == "open" and kept[0]["version"] == 2
    superseded = [a for a in refit.tables["alerts"] if a["state"] == "superseded"]
    assert superseded and all(a["column"] != "" and a["version"] == 1 for a in superseded)
    assert not [
        a for a in refit.tables["alerts"] if a["metric"] == "volume_change" and a["alert_id"] != volume["alert_id"]
    ]
