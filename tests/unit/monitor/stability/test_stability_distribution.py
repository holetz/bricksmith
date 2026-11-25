"""Shape metrics from stored bins: the declaration, vanished categories, quantile shifts and distribution distances."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

import math
from typing import Any

import pytest

from bricksmith.monitor.bins import BinSpec
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import Limits
from bricksmith.monitor.stability.config import (
    METRICS,
    SHAPE_LIMITS,
    SHAPE_METRICS,
    parse_config,
)
from bricksmith.monitor.stability.distribution import (
    js_distance,
    quantile,
    quantile_shift,
    vanished_rate,
    wasserstein,
)
from bricksmith.monitor.stability.engine import MemoryStore, check


def test_config_shape_defaults_leave_serialized_forms_unchanged() -> None:
    plain = parse_config({"period": "month"})
    for metric in (*SHAPE_METRICS, "duplicate_key_rate", "distinct_count", "distinct_change"):
        assert metric in METRICS and metric not in plain.to_dict()["limits"]
    for metric, limits in SHAPE_LIMITS.items():
        assert plain.limits_for(metric, "x") == limits
    assert plain.limits_for("distinct_count", "x") is None
    assert "keys" not in plain.to_dict() and "distinct" not in plain.to_dict()


def test_config_shape_limits_are_configurable_and_can_be_disabled() -> None:
    custom = parse_config({"period": "month", "limits": {"vanished_rate": {"green": 0.1, "red": 0.3}}})
    assert custom.limits_for("vanished_rate", "x") == Limits(0.1, 0.3, "higher")
    assert parse_config({"period": "month", "limits": {"wasserstein": None}}).limits_for("wasserstein", "x") is None


def test_config_keys_and_distinct_are_validated_and_serialized() -> None:
    config = parse_config({"period": "month", "keys": ["id", "line"], "distinct": ["customer", "customer"]})
    assert config.keys == ("id", "line") and config.distinct == ("customer",)
    assert config.to_dict()["keys"] == ["id", "line"] and config.to_dict()["distinct"] == ["customer"]
    for bad in ("id", [1], [""]):
        with pytest.raises(MonitorError):
            parse_config({"period": "month", "keys": bad})
    with pytest.raises(MonitorError):
        parse_config({"period": "month", "distinct": "id"})


def _bins(**counts: int) -> dict[str, list[float]]:
    return {k.replace("__", ":"): [float(v), 0.0, 0.0, 0.0] for k, v in counts.items()}


def test_vanished_rate_is_the_baseline_share_of_the_missing_categories() -> None:
    base = _bins(cat__a=70, cat__b=28, cat__c=2)
    assert vanished_rate(base, _bins(cat__a=70, cat__b=30)) == pytest.approx(0.02)
    assert vanished_rate(base, base) == 0.0
    assert vanished_rate({}, base) is None


def test_js_distance_is_zero_for_equal_shares_and_one_for_disjoint_ones() -> None:
    same = _bins(cat__a=5, cat__b=5)
    assert js_distance(same, _bins(cat__a=50, cat__b=50)) == pytest.approx(0.0)
    assert js_distance(_bins(cat__a=10), _bins(cat__b=10)) == pytest.approx(1.0)
    assert 0 < (js_distance(same, _bins(cat__a=9, cat__b=1)) or 0) < 1
    assert js_distance(same, {}) is None


SPEC = BinSpec(column="x", kind="numeric", edges=(0.0, 10.0, 20.0, 30.0, 40.0))


def test_quantiles_interpolate_inside_bins() -> None:
    even = _bins(bin_0=25, bin_1=25, bin_2=25, bin_3=25)
    assert quantile(SPEC, even, 0.5) == pytest.approx(20.0)
    assert quantile(SPEC, even, 0.05) == pytest.approx(2.0)
    assert quantile(SPEC, even, 0.95) == pytest.approx(38.0)
    assert quantile(SPEC, {}, 0.5) is None


def test_a_tail_shift_moves_p95_while_the_median_stays() -> None:
    base = _bins(bin_0=25, bin_1=25, bin_2=25, bin_3=25)
    tail = _bins(bin_0=25, bin_1=25, bin_2=25, bin_3=10, above=15)
    shift, _ = quantile_shift(SPEC, base, tail, 0.95, 10.0)
    assert shift is not None and shift > 0
    assert quantile_shift(SPEC, base, tail, 0.05, 10.0)[0] == pytest.approx(0.0)
    assert quantile_shift(SPEC, base, tail, 0.95, 0.0) == (None, "baseline has no variance")


def test_wasserstein_grows_with_the_distance_moved() -> None:
    base = _bins(bin_0=50, bin_1=50)
    near = _bins(bin_0=40, bin_1=60)
    far = _bins(bin_2=50, bin_3=50)
    close, _ = wasserstein(SPEC, base, near, 5.0)
    away, _ = wasserstein(SPEC, base, far, 5.0)
    assert wasserstein(SPEC, base, base, 5.0)[0] == pytest.approx(0.0)
    assert close is not None and away is not None and 0 < close < away and math.isfinite(away)


def _run(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    config = parse_config({
        "period": "month", "columns": ["kind"], "min_rows": 0, "min_rows_per_bin": 0, "baseline": "previous",
    })  # fmt: skip
    found = check(rows, config, MemoryStore(), dataset="d", run_id="r1", now="2026-04-01T00:00:00+00:00")
    return {(r["period"], r["column"], r["metric"]): r for r in found.tables["results"]}


def test_a_removed_two_percent_category_turns_vanished_rate_yellow() -> None:
    kinds = ["a"] * 70 + ["b"] * 28 + ["c"] * 2
    now = ["a"] * 70 + ["b"] * 30
    rows = [{"month": "2026-01", "kind": k} for k in kinds] + [{"month": "2026-02", "kind": k} for k in now]
    found = _run(rows)
    assert found[("2026-02", "kind", "vanished_rate")]["status"] == "yellow"
    assert found[("2026-02", "kind", "js_distance")]["value"] is not None
    assert found[("2026-01", "kind", "vanished_rate")]["status"] == "no_baseline"
