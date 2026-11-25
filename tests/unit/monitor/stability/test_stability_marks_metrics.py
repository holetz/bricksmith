"""Regime changes and invalid periods change what the metrics compare a period with."""
# ruff: noqa: PT018 - dense assertions on rows

from __future__ import annotations

from typing import Any

from bricksmith.monitor.stability import MemoryStore, check, parse_config
from bricksmith.monitor.stability.engine import _trend_seed  # pyright: ignore[reportPrivateUsage]
from bricksmith.monitor.stability.metrics import compute

NOW = "2026-04-01T00:00:00+00:00"
SHIFT = [30] * 6 + [90] * 6
PERIODS = [f"2025-{m:02d}" for m in range(1, 13)]


def _rows(counts: list[int]) -> list[dict[str, object]]:
    return [{"month": f"2025-{m:02d}", "x": i % 3} for m, n in enumerate(counts, 1) for i in range(n)]


def _config(**overrides: object):  # noqa: ANN202 - test helper
    base = {"period": "month", "columns": {"x": {"edges": [0, 1.5, 3]}}, "min_rows": 0, "baseline": "previous",
            "trend_metrics": ["row_count"], "trend": {"min_points": 3, "window": 6, "min_rel_change": 0.0}}  # fmt: skip
    return parse_config({**base, **overrides})


def _computed(counts: list[int], **marks: Any):  # noqa: ANN202, ANN401 - test helper
    config = _config(**marks.pop("config", {}))
    store = MemoryStore()
    first = check(_rows(counts), config, store, dataset="d", run_id="r", now=NOW)
    assert first.binning is not None
    return config, first, compute(
        first.binning, first.tables["profiles"], config, dataset="d", window=PERIODS[: len(counts)], **marks
    )  # fmt: skip


def _row_count(computed: Any, metric: str = "row_count") -> dict[str, dict[str, Any]]:  # noqa: ANN401
    return {r["period"]: r for r in computed.results if r["metric"] == metric and not r["column"] and not r["segment"]}


def test_a_regime_change_restarts_the_trend_at_its_period() -> None:
    _, _, plain = _computed(SHIFT)
    _, _, restarted = _computed(SHIFT, regimes=["2025-07"])
    assert _row_count(plain)["2025-07"]["trend_status"] == "red"
    found = _row_count(restarted)
    assert found["2025-09"]["trend_status"] == "insufficient_history"
    assert found["2025-12"]["trend_status"] == "green" and found["2025-12"]["trend_expected"] == 90
    assert _row_count(restarted)["2025-06"]["trend_status"] == _row_count(plain)["2025-06"]["trend_status"]


def test_a_regime_change_cuts_the_baseline_at_its_period() -> None:
    cfg = {"baseline": {"kind": "rolling", "periods": 3, "min_periods": 1}}
    _, _, plain = _computed(SHIFT, config=cfg)
    _, _, restarted = _computed(SHIFT, config=cfg, regimes=["2025-07"])
    assert _row_count(plain, "volume_change")["2025-08"]["baseline"] > 30
    assert _row_count(restarted, "volume_change")["2025-07"]["status"] == "no_baseline"
    assert _row_count(restarted, "volume_change")["2025-08"]["baseline"] == 90
    assert _row_count(restarted, "volume_change")["2025-08"]["value"] == 0


def test_an_invalid_period_is_informational_and_feeds_nothing() -> None:
    counts = [30] * 4 + [300] + [30] * 3
    _, _, plain = _computed(counts)
    _, _, marked = _computed(counts, invalid={"2025-05": "backfill running"})
    bad = _row_count(marked, "volume_change")["2025-05"]
    assert bad["status"] == "informational" and "backfill running" in bad["reason"]
    assert _row_count(plain, "volume_change")["2025-06"]["status"] == "red"  # the spike is the baseline
    assert _row_count(marked, "volume_change")["2025-06"]["status"] == "green"
    assert _row_count(marked)["2025-05"]["trend_status"] == ""


def test_the_trend_seed_drops_excluded_periods_and_those_before_a_regime() -> None:
    config = _config()
    rows = [
        {"dataset": "d", "version": 1, "period": p, "segment": "", "column": "", "metric": "row_count",
         "value": float(v), "status": "green", "direction": "up", "trend_status": "green"}
        for p, v in zip(PERIODS, SHIFT, strict=True)
    ]  # fmt: skip
    store = MemoryStore({"results": rows})
    key = ("", "", "row_count")
    window = PERIODS[9:]
    full = _trend_seed(config, store, PERIODS, window, dataset="d", version=1)[key]
    assert full == [30.0, 30.0, 30.0, 90.0, 90.0, 90.0]
    dropped = _trend_seed(config, store, PERIODS, window, dataset="d", version=1, excluded=["2025-09"])[key]
    assert dropped == [30.0, 30.0, 30.0, 30.0, 90.0, 90.0]  # the six before the window, without 2025-09
    restarted = _trend_seed(config, store, PERIODS, window, dataset="d", version=1, regimes=["2025-08"])[key]
    assert restarted == [90.0, 90.0]
