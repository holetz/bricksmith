"""Revision reliability: the declaration, the revision metrics and the first-publication snapshot."""
# ruff: noqa: PT018 - dense assertions and boolean declaration values

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import Limits
from bricksmith.monitor.stability.config import REVISION_LIMITS, REVISION_METRICS, StabilityConfig, parse_config
from bricksmith.monitor.stability.profile import fit_rows_binning, periods_of_rows, profile_rows
from bricksmith.monitor.stability.revision import revision_results, snapshot_rows
from bricksmith.monitor.stability.tables import ALL_KINDS, KEYS, REVISIONS, SCHEMAS

if TYPE_CHECKING:
    from bricksmith.monitor.reference import Reference


def test_config_revision_is_off_by_default_and_outside_serialized_forms() -> None:
    plain = parse_config({"period": "month"})
    declared = parse_config({"period": "month", "revision": True})
    assert not plain.revision and declared.revision
    assert "revision" not in plain.to_dict() and declared.to_dict()["revision"] is True
    assert plain.binning_fingerprint() == declared.binning_fingerprint()
    assert plain.to_dict()["limits"] == declared.to_dict()["limits"]


def test_config_rejects_a_non_boolean_revision() -> None:
    for bad in ("yes", 1, {"on": True}):
        with pytest.raises(MonitorError):
            parse_config({"period": "month", "revision": bad})


def test_config_revision_limits_default_and_override() -> None:
    config = parse_config({"period": "month", "revision": True})
    for metric in REVISION_METRICS:
        assert config.limits_for(metric, "x") == REVISION_LIMITS[metric]
    custom = parse_config({"period": "month", "limits": {"revision_psi": {"green": 0.01, "red": 0.02}}})
    assert custom.limits_for("revision_psi", "x") == Limits(0.01, 0.02, "higher")
    off = parse_config({"period": "month", "limits": {"revision_psi": None}})
    assert off.limits_for("revision_psi", "x") is None


def test_config_revisions_table_has_a_schema_and_a_key() -> None:
    assert REVISIONS in ALL_KINDS and REVISIONS in SCHEMAS and set(KEYS[REVISIONS]) <= set(SCHEMAS[REVISIONS])


def _profile(config: StabilityConfig, rows: list[dict[str, object]]) -> tuple[Reference, list[dict[str, object]]]:
    periods = periods_of_rows(rows, "month")
    binning, _ = fit_rows_binning(rows, config, periods[:1], dataset="orders", version=1)
    return binning, profile_rows(binning, rows, config, periods, dataset="orders")[0]


def _month(month: str, count: int, shift: float = 0.0) -> list[dict[str, object]]:
    return [{"month": month, "amount": float(i % 20) + i * 0.013 + shift} for i in range(count)]


def _compare(first_rows: list[dict[str, object]], now_rows: list[dict[str, object]], config: StabilityConfig) -> dict:
    binning, first = _profile(config, first_rows)
    now = profile_rows(binning, now_rows, config, ["2026-01"], dataset="orders")[0]
    snap = snapshot_rows(first, ["2026-01"], run_id="r1", published_at="t")
    found = revision_results(binning, now, snap, config, dataset="orders", window=["2026-01"], run_id="r2")
    return {(r["column"], r["metric"]): r for r in found if r["segment"] == ""}


def test_metrics_an_unrevised_period_is_green_and_zero() -> None:
    config = parse_config({"period": "month", "revision": True, "min_rows": 10})
    rows = _month("2026-01", 100)
    found = _compare(rows, rows, config)
    assert found[("", "revision_row_change")]["value"] == 0.0
    assert found[("amount", "revision_psi")]["value"] == pytest.approx(0.0, abs=1e-9)
    assert found[("amount", "revision_mean_shift")]["value"] == 0.0
    assert {r["status"] for r in found.values()} == {"green"}
    assert found[("", "revision_row_change")]["baseline_kind"] == "revision"
    assert found[("", "revision_row_change")]["baseline_label"] == "first publication"


def test_metrics_a_republished_period_reports_changed_rows_psi_and_value_delta() -> None:
    config = parse_config({"period": "month", "revision": True, "min_rows": 10})
    found = _compare(_month("2026-01", 100), _month("2026-01", 150, shift=8.0), config)
    assert found[("", "revision_row_change")]["value"] == pytest.approx(0.5)
    assert found[("", "revision_row_change")]["status"] == "red"
    assert found[("amount", "revision_psi")]["status"] == "red"
    assert found[("amount", "revision_mean_shift")]["value"] > 1
    assert found[("amount", "revision_mean_shift")]["status"] == "red"


def test_metrics_a_period_without_a_snapshot_has_no_revision_result() -> None:
    config = parse_config({"period": "month", "revision": True})
    rows = _month("2026-01", 100) + _month("2026-02", 100)
    binning, profiles = _profile(config, rows)
    snap = snapshot_rows(profiles, ["2026-01"], run_id="r1", published_at="t")
    found = revision_results(binning, profiles, snap, config, dataset="orders", window=["2026-01", "2026-02"])
    assert {r["period"] for r in found} == {"2026-01"}


def test_metrics_snapshot_keeps_only_the_wanted_periods_and_the_table_fields() -> None:
    config = parse_config({"period": "month", "revision": True})
    _, profiles = _profile(config, _month("2026-01", 40) + _month("2026-02", 40))
    snap = snapshot_rows(profiles, ["2026-02"], run_id="r9", published_at="t9")
    assert snap and {r["period"] for r in snap} == {"2026-02"}
    assert set(snap[0]) == set(SCHEMAS[REVISIONS])
