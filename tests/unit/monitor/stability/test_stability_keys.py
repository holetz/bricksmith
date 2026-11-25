"""Duplicate keys and distinct counts: scan statistics stored beside the bins and judged as table and column metrics."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

from typing import Any

from bricksmith.monitor.stability.config import KEYS_COLUMN, parse_config
from bricksmith.monitor.stability.engine import MemoryStore, check
from bricksmith.monitor.stability.tables import merge_rows

DECLARED: dict[str, Any] = {
    "period": "month",
    "columns": ["amount"],
    "keys": ["id"],
    "distinct": ["customer"],
    "baseline": "previous",
    "min_rows": 0,
    "min_rows_per_bin": 0,
}


def _month(month: str, count: int = 200, *, duplicated: int = 0, customers: int = 50) -> list[dict[str, Any]]:
    rows = [{"month": month, "id": i, "customer": f"c{i % customers}", "amount": float(i % 10)} for i in range(count)]
    return rows + [dict(rows[i]) for i in range(duplicated)]


def _run(rows: list[dict[str, Any]], store: MemoryStore | None = None, run_id: str = "r1") -> Any:  # noqa: ANN401
    store = store or MemoryStore()
    found = check(rows, parse_config(DECLARED), store, dataset="d", run_id=run_id, now="2026-04-01T00:00:00+00:00")
    for kind, table in found.tables.items():
        store.tables[kind] = list(merge_rows(kind, store.tables.get(kind, []), table))
    return found


def _results(found: Any) -> dict[tuple[str, str, str], dict[str, Any]]:  # noqa: ANN401
    return {(r["period"], r["column"], r["metric"]): r for r in found.tables["results"]}


def test_a_three_percent_duplicated_load_turns_duplicate_key_rate_red_while_volume_stays_green() -> None:
    found = _run(_month("2026-01") + _month("2026-02", duplicated=6))
    got = _results(found)
    rate = got[("2026-02", "", "duplicate_key_rate")]
    assert rate["value"] == 1 - 200 / 206 and rate["status"] == "red"
    assert got[("2026-02", "", "volume_change")]["status"] == "green"
    assert got[("2026-01", "", "duplicate_key_rate")]["status"] == "green"


def test_a_collapse_in_distinct_values_is_judged_on_the_declared_column() -> None:
    got = _results(_run(_month("2026-01") + _month("2026-02", customers=5)))
    change = got[("2026-02", "customer", "distinct_change")]
    assert change["value"] == 5 / 50 - 1 and change["status"] == "red"
    assert got[("2026-02", "customer", "distinct_count")]["value"] == 5.0
    assert got[("2026-02", "customer", "distinct_count")]["status"] == "no_rule"


def test_scan_rows_are_stored_with_a_reserved_column_and_never_disturb_the_bins() -> None:
    found = _run(_month("2026-01") + _month("2026-02"))
    keyed = [r for r in found.tables["profiles"] if str(r["column"]).startswith("~")]
    assert {r["column"] for r in keyed} == {KEYS_COLUMN, "~distinct:customer"}
    assert all(r["segment"] == "" for r in keyed)
    plain = [r for r in found.tables["profiles"] if r["column"] == "amount"]
    assert plain and sum(r["count"] for r in plain if r["period"] == "2026-02") == 200


def test_nothing_is_stored_or_judged_without_a_declaration() -> None:
    declared = {k: v for k, v in DECLARED.items() if k not in ("keys", "distinct")}
    found = check(
        _month("2026-01"), parse_config(declared), MemoryStore(), dataset="d", run_id="r1",
        now="2026-04-01T00:00:00+00:00",
    )  # fmt: skip
    assert not any(str(r["column"]).startswith("~") for r in found.tables["profiles"])
    assert not any(r["metric"] in ("duplicate_key_rate", "distinct_change") for r in found.tables["results"])


def test_the_second_run_reads_the_stored_scan_statistics_without_profiling_again() -> None:
    store = MemoryStore()
    _run(_month("2026-01"), store)
    found = _run(_month("2026-01") + _month("2026-02", duplicated=6), store, "r2")
    got = _results(found)
    assert got[("2026-02", "", "duplicate_key_rate")]["status"] == "red"
    assert got[("2026-02", "", "duplicate_key_rate")]["baseline"] == 0.0
