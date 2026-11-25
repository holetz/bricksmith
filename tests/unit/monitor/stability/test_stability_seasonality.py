"""Seasonality, business-day volume and sustained shifts in the stability trend."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

from typing import Any

import pytest

from bricksmith.monitor import MonitorError
from bricksmith.monitor.business import business_days
from bricksmith.monitor.model.config import parse_config as parse_model_config
from bricksmith.monitor.stability import MemoryStore, check, parse_config
from bricksmith.monitor.stability.engine import judgment_fingerprint
from bricksmith.monitor.stability.tables import merge_rows

NOW = "2026-04-01T00:00:00+00:00"
EFFECT = [-6.0, -8.0, -2.0, 0.0, 2.0, 1.0, 0.0, -1.0, 3.0, 4.0, 7.0, 40.0]  # December carries a 40% peak
NOISE = [1, -2, 1, 0, 2, -1, 0, 2, -2, 1, -1, 1]


def _months(first_year: int, counts: list[int]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for i, n in enumerate(counts):
        period = f"{first_year + i // 12}-{i % 12 + 1:02d}"
        out += [{"month": period, "x": j % 3} for j in range(n)]
    return out


def _count(t: int) -> int:
    return round(100 + 0.5 * t + EFFECT[t % 12] + NOISE[(t * 5 + t // 12 * 3) % 12])


def _config(trend: dict[str, Any], **extra: object):  # noqa: ANN202 - test helper
    base = {
        "period": "month", "columns": {"x": {"edges": [0, 1.5, 3]}}, "min_rows": 0, "baseline": "previous",
        "trend_metrics": ["row_count"], "max_periods": 80,
    }  # fmt: skip
    return parse_config({**base, **extra, "trend": {"min_rel_change": 0.0, **trend}})


def _last(rows: list[dict[str, object]], cfg: Any, store: MemoryStore | None = None) -> dict[str, dict[str, Any]]:  # noqa: ANN401
    found = check(rows, cfg, store or MemoryStore(), dataset="d", run_id="r", now=NOW)
    return {r["period"]: r for r in found.tables["results"] if r["metric"] == "row_count"}


def test_a_seasonal_december_is_green_and_a_real_break_is_red() -> None:
    counts = [_count(t) for t in range(48)]  # four years, the last period is a December
    plain = _last(_months(2021, counts), _config({"window": 36}))["2024-12"]
    seasonal = _last(_months(2021, counts), _config({"window": 36, "season": 12}))["2024-12"]
    assert plain["trend_status"] != "green" and plain["trend_model"] == "line"
    assert seasonal["trend_status"] == "green" and seasonal["trend_model"] == "seasonal"
    broken = [*counts[:-1], round(counts[-1] * 0.6)]
    assert _last(_months(2021, broken), _config({"window": 36, "season": 12}))["2024-12"]["trend_status"] == "red"


def test_a_new_table_stays_on_the_plain_line_with_an_explicit_reason() -> None:
    counts = [_count(t) for t in range(20)]
    last = _last(_months(2021, counts), _config({"season": 12}))["2022-08"]
    assert last["trend_model"] == "line:insufficient_history"


def test_business_day_volume_is_trended_per_business_day() -> None:
    months = [f"{2024 + i // 12}-{i % 12 + 1:02d}" for i in range(20)]
    rows: list[dict[str, object]] = []
    for period in months:
        rows += [{"month": period, "x": j % 3} for j in range(10 * (business_days(period, _calendar()) or 0))]
    plain = _last(rows, _config({}))
    per_day = _last(rows, _config({"per": "business_day"}))
    last = per_day["2025-08"]
    assert last["trend_status"] == "green" and last["trend_model"] == "line+business_day"
    assert last["trend_expected"] == pytest.approx(10.0)
    assert plain["2025-02"]["trend_model"] == "line"
    boosted = [r for r in rows if r["month"] != "2025-08"] + [{"month": "2025-08", "x": 0}] * 330
    assert _last(boosted, _config({"per": "business_day"}))["2025-08"]["trend_status"] == "red"


def _calendar():  # noqa: ANN202 - test helper
    return parse_config({"period": "month", "columns": {"x": {"edges": [0, 1]}}}).trend.business_calendar  # type: ignore[union-attr]


def test_a_holiday_calendar_changes_the_business_days() -> None:
    cfg = _config({"per": "business_day", "calendar": {"holidays": ["2025-03-03", "2025-03-04"]}})
    assert cfg.trend is not None and business_days("2025-03", cfg.trend.business_calendar) == 19


def test_a_sustained_drift_the_point_rule_never_flags_is_reported_by_cusum_and_ewma() -> None:
    counts = [round(1000 + (3 if i % 2 else -3) + (i % 3) + (0.5 * (i - 12) ** 2 if i > 12 else 0)) for i in range(30)]
    for method in ("cusum", "ewma"):
        cfg = _config({"drift": method}, trend_directions={"row_count": "higher"})
        found = _last(_months(2023, counts), cfg)
        assert all(r["trend_status"] != "red" for r in found.values())
        assert found["2024-01"]["drift_status"] in ("", "green")
        assert found["2025-03"]["drift_status"] == "red"
        assert all(r["change_point_suggested"] is None for r in found.values())


def test_a_level_shift_is_suggested_as_a_change_point_while_it_lasts() -> None:
    counts = [1000 + (3 if i % 2 else -3) + (i % 3) for i in range(14)] + [1000 + 11 + (i % 3) for i in range(12)]
    cfg = _config({"change_point": True}, trend_directions={"row_count": "higher"})
    found = _last(_months(2023, counts), cfg)
    assert found["2023-10"]["change_point_suggested"] is False
    assert found["2024-04"]["change_point_suggested"] is True
    assert all(r["drift_status"] == "" for r in found.values())


def _december(max_periods: int, store: MemoryStore) -> dict[str, Any]:
    cfg = _config({"window": 36, "season": 12, "drift": "ewma"}, max_periods=max_periods)
    found = check(_months(2021, [_count(t) for t in range(48)]), cfg, store, dataset="d", run_id="r", now=NOW)
    for kind, rows in found.tables.items():
        store.tables[kind] = merge_rows(kind, store.tables.get(kind, []), rows)
    return next(r for r in found.tables["results"] if r["metric"] == "row_count" and r["period"] == "2024-12")


def test_a_rewritten_window_seeds_the_season_and_the_scores_from_the_store() -> None:
    store = MemoryStore()
    first = _december(80, store)
    again = _december(3, store)  # only the newest periods are re-judged: their history is the stored one
    assert again["trend_model"] == "seasonal" and first["trend_model"] == "seasonal"
    assert again["trend_status"] == first["trend_status"]
    assert again["trend_expected"] == pytest.approx(first["trend_expected"])
    assert again["drift_status"] == first["drift_status"]


def test_the_default_declaration_keeps_its_judgment_fingerprint() -> None:
    plain = _config({})
    assert judgment_fingerprint(plain) == judgment_fingerprint(_config({"season": 0, "drift": "none"}))
    assert judgment_fingerprint(plain) != judgment_fingerprint(_config({"season": 12}))
    assert judgment_fingerprint(plain) != judgment_fingerprint(_config({"per": "business_day"}))


def test_the_model_monitor_takes_the_season_but_not_the_stability_only_options() -> None:
    base = {"model": "m", "reference": "r", "scores": "s"}
    for trend in ({"per": "business_day"}, {"drift": "cusum"}, {"change_point": True}):
        with pytest.raises(MonitorError):
            parse_model_config({**base, "trend": trend})
