"""Hand-derived stability metrics: declaration parsing, profiles, baselines, PSI, shifts, volumes and alerts."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

import math
from datetime import date
from typing import TYPE_CHECKING

import pytest

from bricksmith.monitor import MonitorError
from bricksmith.monitor.bins import NULL, ZERO
from bricksmith.monitor.stability import MemoryStore, check, parse_config, parse_declarations
from bricksmith.monitor.stability.profile import (
    bin_labels,
    fit_rows_binning,
    period_date,
    period_text,
    profile_rows,
    segment_key,
)
from bricksmith.monitor.stability.tables import merge_rows

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

A, B, C = "2026-01", "2026-02", "2026-03"


def rows_of(values: Mapping[str, Sequence[object]], **extra: Sequence[object]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for period, xs in values.items():
        for i, x in enumerate(xs):
            row: dict[str, object] = {"month": period, "x": x}
            for name, column in extra.items():
                row[name] = column[len(out) % len(column)] if name != "w" else column[i]
            out.append(row)
    return out


def config(**overrides: object):  # noqa: ANN201 - test helper
    base = {"period": "month", "columns": {"x": {"edges": [0, 1.5, 3]}}, "min_rows": 0}
    return parse_config({**base, **overrides})


def results_of(rows, cfg, store=None):  # noqa: ANN001, ANN201 - test helper
    found = check(rows, cfg, store or MemoryStore(), dataset="d", run_id="r1", now="2026-04-01T00:00:00+00:00")
    return found, {(r["period"], r["segment"], r["column"], r["metric"]): r for r in found.tables["results"]}


def test_declaration_errors_are_specific() -> None:
    with pytest.raises(MonitorError, match="period"):
        parse_config({})
    with pytest.raises(MonitorError, match="unknown keys colour"):
        parse_config({"period": "p", "colour": 1})
    with pytest.raises(MonitorError, match="unknown metric"):
        parse_config({"period": "p", "limits": {"ks": {"green": 1, "red": 2}}})
    with pytest.raises(MonitorError, match="not supported by stability"):
        parse_config({"period": "p", "limits": {"psi": {"green": 0.1, "red": 0.2, "kind": "relative"}}})
    with pytest.raises(MonitorError, match="not supported by stability"):
        parse_config({"period": "p", "column_limits": {"x": {"psi": {"green": 2, "red": 3, "kind": "control"}}}})
    with pytest.raises(MonitorError, match="baseline kind"):
        parse_config({"period": "p", "baseline": "moving"})
    with pytest.raises(MonitorError, match="start <= end"):
        parse_config({"period": "p", "baseline": {"start": "2026-03"}})
    with pytest.raises(MonitorError, match="fail_on"):
        parse_config({"period": "p", "gate": {"fail_on": ["purple"]}})
    with pytest.raises(MonitorError, match="same output"):
        parse_declarations([{"period": "p"}, {"period": "q"}])
    parsed = parse_config({"period": "p", "segments": ["a", ["a", "b"]], "weight": "w", "gate": {"fail_on": "red"}})
    assert parsed.segments == (("a",), ("a", "b")) and parsed.gate.fail_on == ("red",)
    assert parsed.excluded() == ("a", "b", "p", "w") and parsed.auto
    assert parse_config({"period": "p", "limits": {"psi": None}}).limits_for("psi", "x") is None
    custom = parse_config({"period": "p", "column_limits": {"x": {"psi": {"green": 0.2, "red": 0.4}}}})
    assert custom.limits_for("psi", "x").green == 0.2 and custom.limits_for("psi", "y").green == 0.1  # type: ignore[union-attr]


def test_fingerprints_separate_bins_from_judgment() -> None:
    plain = config()
    assert config(limits={"psi": {"green": 0.2, "red": 0.3}}).binning_fingerprint() == plain.binning_fingerprint()
    assert config(columns={"x": {"bins": 3}}).binning_fingerprint() != plain.binning_fingerprint()


def test_period_and_segment_texts() -> None:
    assert period_text(date(2026, 1, 31)) == "2026-01-31" and period_text(None) is None
    assert period_text(True)  # noqa: FBT003 == "true" and period_text(202601) == "202601"
    assert [period_date(t) for t in ("2026-01", "2026-01-31 10:00", "202602", "20260315", "Q1", "2026-13")] == [
        "2026-01-01", "2026-01-31", "2026-02-01", "2026-03-15", None, None,
    ]  # fmt: skip
    assert segment_key(("a", "b"), ("x", None)) == "a=x,b=(null)" and segment_key((), ()) == ""


def test_profile_rows_carry_every_bin_with_moments() -> None:
    rows = rows_of({A: [0, 1, 2, None]}, region=["n", "n", "s", "s"])
    cfg = parse_config(
        {"period": "month", "columns": {"x": {"edges": [1, 2], "zero": "separate"}}, "segments": ["region"]}
    )
    binning, skipped = fit_rows_binning(rows, cfg, [A], dataset="d", version=1)
    assert skipped == {} and binning.fit_window["periods"] == A
    labels = bin_labels(binning.columns[0])
    assert [v[1] for v in labels.values()] == ["< 1", "= 0", "[1, 2]", "> 2", "null"]
    profile, info = profile_rows(binning, rows, cfg, [A], dataset="d", run_id="r")
    assert info.rows == 4 and info.truncated == {}
    whole = {r["bin"]: r for r in profile if r["segment"] == ""}
    assert whole[ZERO]["count"] == 1 and whole["bin_0"]["count"] == 2 and whole[NULL]["count"] == 1
    assert whole["bin_0"]["value_sum"] == 3.0 and whole["bin_0"]["value_sum_sq"] == 5.0
    assert whole[NULL]["value_sum"] == 0.0 and whole["below"]["count"] == 0
    south = {r["bin"]: r["count"] for r in profile if r["segment"] == "region=s"}
    assert south == {"below": 0, ZERO: 0, "bin_0": 1, "above": 0, NULL: 1}
    assert {r["period_date"] for r in profile} == {"2026-01-01"}


def test_psi_shift_and_volume_against_the_previous_period() -> None:
    # A: x = 1, 1, 2, 2 -> bin_0 2, bin_1 2; B: 1, 2, 2, 2 -> bin_0 1, bin_1 3.
    # PSI = (0.25 - 0.5) ln(0.25/0.5) + (0.75 - 0.5) ln(0.75/0.5) = 0.25 (ln 2 + ln 1.5) = 0.274653 (red).
    # Mean A 1.5, std A sqrt(10/4 - 2.25) = 0.5; mean B 1.75 -> shift 0.5 (yellow, boundary inclusive).
    rows = rows_of({A: [1, 1, 2, 2], B: [1, 2, 2, 2]})
    _, found = results_of(rows, config(baseline="previous"))
    psi = found[(B, "", "x", "psi")]
    assert psi["value"] == pytest.approx(0.25 * (math.log(2) + math.log(1.5))) and psi["status"] == "red"
    assert psi["green_limit"] == 0.10 and psi["red_limit"] == 0.25 and psi["status_rank"] == 2
    shift = found[(B, "", "x", "mean_shift")]
    assert shift["value"] == pytest.approx(0.5) and shift["status"] == "yellow" and shift["baseline"] == 1.5
    volume = found[(B, "", "", "volume_change")]
    assert volume["value"] == 0.0 and volume["status"] == "green" and volume["baseline_rows"] == 4
    assert found[(A, "", "x", "psi")]["status"] == "no_baseline"
    assert found[(B, "", "", "row_count")]["status"] == "no_rule" and found[(B, "", "", "row_count")]["value"] == 4


def test_null_unseen_rates_and_min_rows() -> None:
    rows = rows_of({A: [1, 1, 2, 2], B: [1, None, 5, 2]})
    _, found = results_of(rows, config(baseline="previous"))
    assert found[(B, "", "x", "null_rate")]["value"] == 0.25
    assert (
        found[(B, "", "x", "null_rate_delta")]["value"] == 0.25
        and found[(B, "", "x", "null_rate_delta")]["status"] == "red"
    )
    assert found[(B, "", "x", "unseen_rate_delta")]["value"] == 0.25
    _, small = results_of(rows, config(baseline="previous", min_rows=5))
    assert small[(B, "", "x", "psi")]["status"] == "insufficient_volume"
    assert small[(B, "", "", "volume_change")]["status"] == "green"  # volume metrics still judge


def test_fixed_rolling_baselines_and_weights() -> None:
    rows = rows_of({A: [1, 1], B: [2, 2], C: [1, 2, 2, 2]}, w=[1.0, 1.0, 2.0, 2.0])
    _, fixed = results_of(rows, config(fit_periods=2))
    # Fixed baseline = fit periods A and B: bin_0 2, bin_1 2; C: bin_0 1, bin_1 3 -> same PSI as above.
    assert fixed[(C, "", "x", "psi")]["value"] == pytest.approx(0.274653, abs=1e-6)
    assert fixed[(A, "", "x", "psi")]["in_baseline"] and not fixed[(C, "", "x", "psi")]["in_baseline"]
    assert fixed[(C, "", "", "volume_change")]["value"] == 1.0  # 4 rows / mean 2 - 1
    _, rolling = results_of(rows, config(baseline={"kind": "rolling", "periods": 2, "min_periods": 2}))
    assert rolling[(B, "", "x", "psi")]["status"] == "no_baseline"
    assert rolling[(C, "", "x", "psi")]["value"] == pytest.approx(0.274653, abs=1e-6)
    _, weighted = results_of(rows, config(baseline="previous", weight="w"))
    # Weights of C: 1, 1, 2, 2 -> bin_0 1/6, bin_1 5/6 against B (all bin_1, floored at 1e-4).
    assert weighted[(C, "", "", "weight_change")]["value"] == pytest.approx(6 / 2 - 1)
    assert weighted[(C, "", "x", "psi")]["value"] > fixed[(C, "", "x", "psi")]["value"]


def test_trend_and_persistence_alerts() -> None:
    stable = {f"2025-{m:02d}": [1, 2, 1, 2] for m in range(1, 13)}
    drifted = {**stable, "2026-01": [2, 2, 2, 2], "2026-02": [2, 2, 2, 2]}
    cfg = config(baseline={"kind": "fixed", "start": "2025-01", "end": "2025-06"})
    result, found = results_of(rows_of(drifted), cfg)
    # The stable year has PSI 0 exactly, so the first drifted period is infinitely far from its trend.
    assert found[("2026-01", "", "x", "psi")]["trend_status"] == "red"
    assert found[("2026-02", "", "x", "psi")]["status"] == "red"
    persistence = [a for a in result.tables["alerts"] if a["kind"] == "persistence" and a["metric"] == "psi"]
    assert len(persistence) == 1 and persistence[0]["severity"] == "critical" and persistence[0]["state"] == "open"
    assert persistence[0]["opened_period"] == "2026-02" and persistence[0]["flagged_periods"] == 1
    assert result.outcome.red > 0 and result.outcome.status == "warned"


def test_incremental_runs_and_new_versions() -> None:
    rows = rows_of({A: [1, 1, 2, 2], B: [1, 2, 2, 2]})
    store = MemoryStore()
    first, _ = results_of(rows, config(baseline="previous"), store)
    for kind, items in first.tables.items():
        store.tables[kind] = merge_rows(kind, store.tables.get(kind, []), items)
    assert first.outcome.periods_profiled == 2 and len(first.tables["binnings"]) == 1
    again, _ = results_of(rows, config(baseline="previous"), store)
    assert again.outcome.periods_profiled == 1 and again.tables["binnings"] == []
    assert {r["period"] for r in again.tables["results"]} == {B}
    assert {a["alert_id"] for a in again.tables["alerts"]} == {a["alert_id"] for a in first.tables["alerts"]}
    judged, _ = results_of(rows, config(baseline="previous", limits={"psi": {"green": 0.5, "red": 0.9}}), store)
    assert {r["period"] for r in judged.tables["results"]} == {A, B}  # new judgment rewrites the window
    refit, _ = results_of(rows, config(baseline="previous", columns={"x": {"edges": [0, 2, 3]}}), store)
    assert refit.outcome.version == 2 and refit.tables["binnings"][0]["version"] == 2


def test_gate_conditions_and_messages() -> None:
    rows = rows_of({A: [1, 1, 2, 2], B: [1, 2, 2, 2]})
    failed, _ = results_of(rows, config(baseline="previous", gate={"fail_on": ["red"]}))
    assert failed.outcome.failed and "gate failed on 1 red" in failed.outcome.message
    passed, _ = results_of(rows, config(baseline="previous", gate={"fail_on": ["critical"]}))
    assert not passed.outcome.failed and passed.outcome.status == "warned"
    with pytest.raises(MonitorError, match="no rows"):
        results_of([{"month": None, "x": 1}], config())
    with pytest.raises(MonitorError, match="fixed baseline"):
        results_of(rows, config(baseline={"start": "2027-01", "end": "2027-02"}))


def test_auto_columns_skip_with_reasons() -> None:
    rows = [
        {
            "month": A if i < 40 else B,
            "amount": float(i % 25) * 3,
            "kind": "abc"[i % 3],
            "id": i,
            "day": date(2026, 1, 1),
        }
        for i in range(80)
    ]
    result, found = results_of(rows, parse_config({"period": "month", "exclude": ["id"], "baseline": "previous"}))
    assert result.skipped == {"day": "unsupported_type", "id": "excluded", "month": "excluded"}
    assert (B, "", "amount", "psi") in found and (B, "", "kind", "psi") in found
    assert result.binning is not None and result.binning.column("amount").zero


def test_open_alert_keeps_its_identity_when_its_opening_leaves_the_window() -> None:
    stable = {f"2026-{m:02d}": [1, 2, 1, 2] for m in range(1, 5)}
    drifted = {f"2026-{m:02d}": [2, 2, 2, 2] for m in range(5, 13)}
    cfg = config(max_periods=3, baseline={"kind": "fixed", "start": "2026-01", "end": "2026-04"})
    store = MemoryStore()
    opened: dict[str, object] = {}
    months = [*stable, *drifted]
    for count in range(5, len(months) + 1):
        rows = rows_of({m: {**stable, **drifted}[m] for m in months[:count]})
        found, _ = results_of(rows, cfg, store)
        for kind, items in found.tables.items():
            store.tables[kind] = merge_rows(kind, store.tables.get(kind, []), items)
        mine = [a for a in store.tables["alerts"] if a["metric"] == "psi" and a["kind"] == "persistence"]
        if not opened and mine:
            opened = mine[0]
    final = [a for a in store.tables["alerts"] if a["metric"] == "psi" and a["kind"] == "persistence"]
    assert len(final) == 1 and final[0]["state"] == "open"
    assert final[0]["alert_id"] == opened["alert_id"] and final[0]["opened_period"] == opened["opened_period"]
    assert final[0]["opened_period"] < "2026-10"  # the opening period is outside the last three periods
    assert final[0]["flagged_periods"] > 3
    assert not [a for a in store.tables["alerts"] if a["state"] == "superseded"]
