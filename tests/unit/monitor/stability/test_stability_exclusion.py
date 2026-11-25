"""Anomalous periods stay out of the trend history and the rolling baselines of stability results."""

from __future__ import annotations

from typing import Any

import pytest

from bricksmith.monitor.stability import MemoryStore, check, parse_config
from bricksmith.monitor.stability.engine import judgment_fingerprint
from bricksmith.monitor.stability.metrics import compute
from bricksmith.monitor.stability.tables import merge_rows

NOW = "2026-04-01T00:00:00+00:00"


def _rows(counts: list[int]) -> list[dict[str, object]]:
    return [{"month": f"2025-{m:02d}", "x": i % 3} for m, n in enumerate(counts, 1) for i in range(n)]


def _config(**overrides: object):  # noqa: ANN202 - test helper
    base = {"period": "month", "columns": {"x": {"edges": [0, 1.5, 3]}}, "min_rows": 0}
    return parse_config({**base, **overrides})


def _by_key(found: Any) -> dict[tuple[str, str, str], dict[str, Any]]:  # noqa: ANN401 - CheckResult
    return {(r["period"], r["column"], r["metric"]): r for r in found.tables["results"]}


SPIKE = [30] * 7 + [300] + [30] * 3 + [90]


def _trend_run(exclude: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    cfg = _config(baseline="previous", trend_metrics=["row_count"], trend={"exclude": exclude, "min_rel_change": 0.0})
    return _by_key(check(_rows(SPIKE), cfg, MemoryStore(), dataset="d", run_id="r", now=NOW))


def test_a_red_spike_no_longer_masks_a_later_jump_in_the_trend() -> None:
    kept = _trend_run("none")
    dropped = _trend_run("red")
    assert kept[("2025-08", "", "row_count")]["trend_status"] == "red"
    assert dropped[("2025-08", "", "row_count")]["trend_status"] == "red"
    assert kept[("2025-12", "", "row_count")]["trend_status"] == "green"  # the spike widened the band
    assert dropped[("2025-12", "", "row_count")]["trend_status"] == "red"
    assert dropped[("2025-12", "", "row_count")]["trend_expected"] == pytest.approx(30)


def test_a_robust_fit_alone_also_resists_the_spike() -> None:
    cfg = _config(
        baseline="previous", trend_metrics=["row_count"],
        trend={"exclude": "none", "fit": "theil_sen", "scale": "mad", "min_rel_change": 0.0},
    )  # fmt: skip
    found = _by_key(check(_rows(SPIKE), cfg, MemoryStore(), dataset="d", run_id="r", now=NOW))
    assert found[("2025-12", "", "row_count")]["trend_status"] == "red"


def _baseline_run(exclude: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    cfg = _config(baseline={"kind": "rolling", "periods": 2, "min_periods": 1, "exclude": exclude})
    return _by_key(check(_rows([30, 30, 30, 300, 30, 30]), cfg, MemoryStore(), dataset="d", run_id="r", now=NOW))


def test_a_rolling_baseline_skips_a_red_period() -> None:
    kept = _baseline_run("none")
    skipped = _baseline_run("red")
    assert kept[("2025-04", "", "volume_change")]["status"] == "red"
    assert skipped[("2025-04", "", "volume_change")]["status"] == "red"
    assert kept[("2025-05", "", "volume_change")]["value"] == pytest.approx(30 / 165 - 1)
    assert skipped[("2025-05", "", "volume_change")]["value"] == pytest.approx(0.0)
    assert skipped[("2025-05", "", "volume_change")]["status"] == "green"
    assert skipped[("2025-06", "", "volume_change")]["value"] == pytest.approx(0.0)  # baseline 2025-05 and 2025-03


def test_a_previous_baseline_takes_the_next_eligible_period() -> None:
    cfg = _config(baseline={"kind": "previous", "exclude": "red"})
    found = _by_key(check(_rows([30, 30, 30, 300, 30]), cfg, MemoryStore(), dataset="d", run_id="r", now=NOW))
    assert found[("2025-05", "", "volume_change")]["value"] == pytest.approx(0.0)


def test_a_stored_red_period_is_skipped_by_a_later_run() -> None:
    cfg = _config(baseline={"kind": "rolling", "periods": 2, "min_periods": 1, "exclude": "red"}, max_periods=1)
    store = MemoryStore()
    for counts in ([30, 30, 30, 300], [30, 30, 30, 300, 30]):
        found = check(_rows(counts), cfg, store, dataset="d", run_id="r", now=NOW)
        for kind, rows in found.tables.items():
            store.tables[kind] = merge_rows(kind, store.tables.get(kind, []), rows)
    last = _by_key(found)
    assert last[("2025-05", "", "volume_change")]["value"] == pytest.approx(0.0)


def test_declared_excluded_periods_leave_baselines_and_trend_history() -> None:
    cfg = _config(baseline={"kind": "rolling", "periods": 2, "min_periods": 1}, trend_metrics=["row_count"])
    found = check(_rows([30, 30, 30, 300, 30, 30]), cfg, MemoryStore(), dataset="d", run_id="r", now=NOW)
    window = [f"2025-{m:02d}" for m in range(1, 7)]
    assert found.binning is not None
    marked = compute(found.binning, found.tables["profiles"], cfg, dataset="d", window=window, excluded=["2025-04"])
    by = {(r["period"], r["column"], r["metric"]): r for r in marked.results}
    assert by[("2025-05", "", "volume_change")]["value"] == pytest.approx(0.0)


def test_exclusion_settings_change_the_judgment_fingerprint() -> None:
    plain = judgment_fingerprint(_config())
    assert judgment_fingerprint(_config(trend={"exclude": "none"})) != plain
    assert judgment_fingerprint(_config(trend={"fit": "theil_sen"})) != plain
    assert judgment_fingerprint(_config(baseline={"kind": "previous", "exclude": "red"})) != judgment_fingerprint(
        _config(baseline="previous")
    )
