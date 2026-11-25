"""A re-judged window keeps the trend history of the periods before it."""

from __future__ import annotations

from typing import Any

from bricksmith.monitor.stability import MemoryStore, check, parse_config
from bricksmith.monitor.stability.tables import merge_rows

PERIODS = [f"2025-{m:02d}" for m in range(1, 13)]


def _rows() -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for i, period in enumerate(PERIODS):
        out += [{"month": period, "x": v} for v in ([1, 2, 1, 2] if i % 3 else [1, 2, 2, 2])]
    return out


def _run(max_periods: int, store: MemoryStore) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    cfg = parse_config(
        {
            "period": "month",
            "columns": {"x": {"edges": [0, 1.5, 3]}},
            "min_rows": 0,
            "max_periods": max_periods,
            "baseline": {"kind": "rolling", "periods": 2, "min_periods": 1},
        }
    )
    found = check(_rows(), cfg, store, dataset="d", run_id="r", now="2026-04-01T00:00:00+00:00")
    for kind, rows in found.tables.items():
        store.tables[kind] = merge_rows(kind, store.tables.get(kind, []), rows)
    return {(r["period"], r["segment"], r["column"], r["metric"]): r for r in found.tables["results"]}


def test_a_narrow_window_judges_the_trend_like_the_first_run() -> None:
    store = MemoryStore()
    full = _run(12, store)
    narrow = _run(6, store)
    compared = 0
    for key, row in narrow.items():
        assert row["trend_status"] == full[key]["trend_status"], key
        assert row["trend_expected"] == full[key]["trend_expected"], key
        compared += row["trend_status"] not in ("", "insufficient_history")
    assert compared
