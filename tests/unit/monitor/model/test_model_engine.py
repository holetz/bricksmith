"""The model monitoring engine on synthetic predictions: families, periods, references, isolation and alerts."""
# ruff: noqa: PT018 - dense assertions on result rows

from __future__ import annotations

from typing import Any

import pytest

from bricksmith.monitor.bins import BinSpec
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.model.actions import action_row
from bricksmith.monitor.model.config import Lag, parse_config
from bricksmith.monitor.model.engine import (
    MemoryStore,
    check,
    data_period,
    jobs_of,
    resolve_data_period,
    shift_months,
)
from bricksmith.monitor.reference import Reference

DEV = ["2025-01", "2025-02", "2025-03"]
OOT = ["2025-04", "2025-05"]
PROD = ["2025-06", "2025-07", "2025-08", "2025-09"]


def predictions(
    *, drift: bool = False, per_month: int = 200, drifted: tuple[str, ...] = (), noisy: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """Return scored rows: score and label agree, grade buckets the score, the last month drifts on request.

    ``drifted`` months shift the score and age; ``noisy`` months draw labels unrelated to the score.
    """
    rows: list[dict[str, Any]] = []
    shifted_months = set(drifted) | ({PROD[-1]} if drift else set())
    for sample, months in (("dev", DEV), ("oot", OOT), ("prod", PROD)):
        for month in months:
            shifted = month in shifted_months
            for i in range(per_month):
                score = ((i * 37) % per_month) / per_month
                if shifted:
                    score = min(score + 0.4, 0.999)
                label = 1 if ((i * 61) % 100) / 100 < score else 0
                if month in noisy:
                    label = (i * 7 // 3) % 2
                rows.append({
                    "month": month, "sample": sample, "score": score, "label": label,
                    "grade": "A" if score < 0.3 else "B" if score < 0.7 else "C",
                    "region": "n" if i % 2 else "s", "age": float(20 + i % 40 + (30 if shifted else 0)),
                    "plan": ["basic", "plus", "pro"][i % 3], "income": None if i % 10 == 0 else float(i),
                    "version": "1" if month < "2025-08" else "2",
                })  # fmt: skip
    return rows


DECLARATION: dict[str, Any] = {
    "model": "churn",
    "period": "month",
    "score": "score",
    "label": "label",
    "grade": "grade",
    "segment": "region",
    "sample": "sample",
    "references": {"development": {"sample": "dev"}, "out_of_time": {"sample": "oot"}},
    "features": ["age", "plan"],
    "critical_fields": ["income"],
    "model_version_column": "version",
    "min_rows": 10,
    "limits": {"ks": {"green": 0.3, "red": 0.2, "direction": "lower"}},
}


def run(
    rows: list[dict[str, Any]],
    store: MemoryStore | None = None,
    run_id: str = "r1",
    now: str = "t0",
    **changes: Any,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    return check(rows, parse_config({**DECLARATION, **changes}), store or MemoryStore(), run_id=run_id, now=now)


def pick(results: list[dict[str, Any]], **where: object) -> list[dict[str, Any]]:
    return [r for r in results if all(r.get(k) == v for k, v in where.items())]


def test_stable_run_measures_every_family_per_monitoring_period() -> None:
    result = run(predictions())
    results = result.tables["results"]
    assert {r["monitoring_period"] for r in results} == set(PROD)
    assert {r["family"] for r in results} == {
        "score_psi", "feature_csi", "grade_psi", "discrimination", "calibration", "volumetry", "completeness",
    }  # fmt: skip
    psi = pick(results, metric="psi", segment="", monitoring_period="2025-06")[0]
    assert psi["light"] == "green" and psi["data_period"] == "2025-06" and psi["reference"] == "development"
    assert psi["development_value"] == pytest.approx(0.0, abs=1e-9) and psi["out_of_time_value"] is not None
    ks = pick(results, metric="ks", segment="", monitoring_period="2025-07")[0]
    assert ks["value"] == pytest.approx(ks["development_value"]) and ks["light"] == "green"
    assert ks["out_of_time_value"] == pytest.approx(ks["development_value"])
    assert {r["segment"] for r in pick(results, metric="psi")} == {"", "n", "s"}
    ratio = pick(results, metric="volume_ratio", segment="", monitoring_period="2025-06")[0]
    assert ratio["value"] == pytest.approx(1.0) and ratio["light"] == "green"
    nulls = pick(results, metric="null_rate", column="income", segment="")[0]
    assert nulls["value"] == pytest.approx(0.1)
    calibration = pick(results, metric="calibration_ratio", segment="")[0]
    assert calibration["value"] is not None and calibration["development_value"] is not None
    assert {r["model_version"] for r in results} == {"1", "2"}
    run_row = result.tables["runs"][0]
    assert run_row["status"] == "success" and run_row["errors"] == 0 and run_row["model_version"] == "1,2"
    assert run_row["references"] == "development: sample=dev; out_of_time: sample=oot"
    assert result.outcome.status == "passed" and result.outcome.periods == len(PROD)
    assert result.tables["alerts"] == [] and len(result.tables["binnings"]) == 1


def test_details_carry_bins_and_calibration() -> None:
    details = run(predictions()).tables["details"]
    psi = pick(details, metric="psi", segment="", monitoring_period="2025-06")
    assert [d["bin_order"] for d in psi] == sorted(d["bin_order"] for d in psi)
    assert sum(d["current_share"] for d in psi) == pytest.approx(1.0, abs=1e-3)
    ece = pick(details, metric="ece", segment="", monitoring_period="2025-06")
    assert ece and all(d["mean_score"] is not None and d["count"] > 0 for d in ece)


def test_label_lag_moves_the_data_period_back() -> None:
    results = run(predictions(), label_lag=1).tables["results"]
    first = pick(results, metric="ks", segment="", monitoring_period="2025-06")[0]
    assert first["value"] is None and first["reason"] == "data_period_unavailable" and first["light"] == "no_data"
    lagged = pick(results, metric="auc", segment="", monitoring_period="2025-08")[0]
    assert lagged["data_period"] == "2025-07" and lagged["value"] is not None
    psi = pick(results, metric="psi", segment="", monitoring_period="2025-08")[0]
    assert psi["data_period"] == "2025-08"
    assert data_period(["a", "b", "c"], "c", 2) == "a" and data_period(["a", "b"], "a", 1) is None


def test_drift_turns_red_raises_alerts_and_fails_the_gate() -> None:
    result = run(predictions(drift=True), gate={"fail_on": "red"})
    latest = pick(result.tables["results"], metric="psi", segment="", monitoring_period=PROD[-1])[0]
    assert latest["light"] == "red" and latest["light_rank"] == 2
    age = pick(result.tables["results"], metric="csi", column="age", segment="", monitoring_period=PROD[-1])[0]
    assert age["light"] == "red"
    alerts = result.tables["alerts"]
    assert alerts and all(a["monitoring_period"] == PROD[-1] for a in alerts)
    assert {a["severity"] for a in alerts if a["light"] == "red"} == {"critical"}
    assert result.outcome.status == "failed" and "gate failed on" in result.outcome.message
    assert result.tables["runs"][0]["status"] == "success"  # the gate is not a computation error
    warned = run(predictions(drift=True))
    assert warned.outcome.status == "warned"


def test_a_failing_family_marks_the_run_partial_and_spares_the_others() -> None:
    rows = predictions()
    for row in rows:
        row["broken"] = None
    result = run(rows, features={"age": {}, "broken": {"type": "numeric"}})
    broken = pick(result.tables["results"], column="broken")
    assert broken and all(r["value"] is None and r["reason"].startswith("error: MonitorError") for r in broken)
    assert pick(result.tables["results"], metric="csi", column="age", segment="")[0]["value"] is not None
    assert result.tables["runs"][0]["status"] == "partial" and result.outcome.errors > 0
    assert result.outcome.run_status == "partial"


def test_binning_is_frozen_and_reused_across_runs() -> None:
    store = MemoryStore()
    first = run(predictions(), store)
    store.tables["binnings"] = first.tables["binnings"]
    second = run(predictions(), store, run_id="r2")
    assert second.tables["binnings"] == [] and second.binning == first.binning
    third = run(predictions(), store, run_id="r3", score_bins=5)
    assert third.tables["binnings"][0]["version"] == 2


def test_feature_bins_are_shared_with_a_stability_binning() -> None:
    edges = (0.0, 30.0, 45.0, 100.0)
    shared = Reference("customers", 3, (BinSpec("age", "numeric", edges=edges),))
    store = MemoryStore(stability=[{"dataset": "customers", "version": 3, "body": shared.to_json()}])
    result = run(predictions(), store, feature_binning={"stability": "customers"})
    assert result.binning.column("age").edges == edges
    assert result.binning.column("plan").kind == "categorical"
    assert result.tables["binnings"][0]["shared_from"] == "customers v3"
    store.tables["binnings"] = result.tables["binnings"]
    refit = Reference("customers", 4, (BinSpec("age", "numeric", edges=(0.0, 50.0, 100.0)),))
    store.stability.append({"dataset": "customers", "version": 4, "body": refit.to_json()})
    again = run(predictions(), store, run_id="r2", feature_binning={"stability": "customers"})
    assert again.tables["binnings"][0]["version"] == 2  # a stability refit is visible as a new binning
    with pytest.raises(MonitorError, match="no stored stability binning"):
        run(predictions(), MemoryStore(), feature_binning={"stability": "missing"})


def test_period_range_references_and_literal_version() -> None:
    rows = [{k: v for k, v in r.items() if k != "sample"} for r in predictions()]
    declaration = {k: v for k, v in DECLARATION.items() if k not in ("sample", "model_version_column")}
    references = {"development": {"start": DEV[0], "end": DEV[-1]}, "out_of_time": {"start": OOT[0], "end": OOT[-1]}}
    result = check(
        rows, parse_config({**declaration, "references": references, "model_version": "7", "max_periods": 2}),
        MemoryStore(), run_id="r", now="t0",
    )  # fmt: skip
    assert {r["monitoring_period"] for r in result.tables["results"]} == set(PROD[-2:])
    assert {r["model_version"] for r in result.tables["results"]} == {"7"}


def test_matured_results_carry_the_version_that_scored_the_measured_rows() -> None:
    # Version 2 is released in 2025-08; with a two-period lag, 2025-08 and 2025-09 measure v1's rows.
    result = run(predictions(), label_lag=2, model_version_column="version")
    results = result.tables["results"]
    for period in ("2025-08", "2025-09"):
        gini = pick(results, metric="gini", segment="", monitoring_period=period)[0]
        assert gini["data_period"] < "2025-08" and gini["model_version"] == "1"
        psi = pick(results, metric="psi", segment="", monitoring_period=period)[0]
        assert psi["data_period"] == period and psi["model_version"] == "2"
    v2_gini = pick(results, metric="gini", model_version="2", segment="")
    assert v2_gini == [] and result.tables["runs"][0]["model_version"] == "1,2"


def test_a_lagged_period_scored_by_two_versions_is_stamped_with_both() -> None:
    rows = predictions()
    for i, row in enumerate(r for r in rows if r["month"] == "2025-07"):
        row["version"] = "1" if i % 2 else "2"
    results = run(rows, label_lag=1).tables["results"]
    gini = pick(results, metric="gini", segment="", monitoring_period="2025-08")[0]
    assert gini["data_period"] == "2025-07" and gini["model_version"] == "1,2"
    assert len(pick(results, metric="gini", segment="", monitoring_period="2025-08")) == 1
    assert run(rows, label_lag=1).tables["runs"][0]["model_version"] == "1,2"


def test_errors_when_nothing_can_be_measured() -> None:
    with pytest.raises(MonitorError, match="development reference"):
        run(predictions(), references={"development": {"sample": "none"}})
    only_references = [r for r in predictions() if r["sample"] != "prod"]
    with pytest.raises(MonitorError, match="no monitoring periods"):
        run(only_references)


def test_jobs_follow_the_declared_roles() -> None:
    minimal = parse_config({k: DECLARATION[k] for k in ("model", "period", "score", "sample", "references")})
    assert jobs_of(minimal) == [("score_psi", "score"), ("volumetry", "")]
    full = jobs_of(parse_config(DECLARATION))
    assert ("feature_csi", "age") in full and ("grade_psi", "grade") in full and ("completeness", "") in full


def test_relative_limits_judge_the_change_against_a_reference() -> None:
    ks = {"kind": "relative", "green": -0.15, "red": -0.30, "direction": "lower"}
    result = run(predictions(noisy=(PROD[-1],)), limits={"ks": ks, "auc": {**ks, "reference": "out_of_time"}})
    stable = pick(result.tables["results"], metric="ks", segment="", monitoring_period=PROD[0])[0]
    assert stable["light"] == "green" and stable["compared_value"] == pytest.approx(0.0, abs=1e-9)
    assert stable["limit_kind"] == "relative" and stable["limit_reference"] == "development"
    noisy = pick(result.tables["results"], metric="ks", segment="", monitoring_period=PROD[-1])[0]
    expected = (noisy["value"] - noisy["development_value"]) / abs(noisy["development_value"])
    assert noisy["compared_value"] == pytest.approx(expected) and expected < -0.30 and noisy["light"] == "red"
    assert noisy["green_limit"] == -0.15 and noisy["baseline_mean"] is None
    auc = pick(result.tables["results"], metric="auc", segment="", monitoring_period=PROD[-1])[0]
    assert auc["limit_reference"] == "out_of_time" and auc["light"] == "red"
    assert auc["compared_value"] == pytest.approx((auc["value"] - auc["out_of_time_value"]) / auc["out_of_time_value"])


def test_control_limits_use_development_and_out_of_time_periods() -> None:
    chart = {"kind": "control", "green": -2, "red": -3, "direction": "lower"}
    result = run(predictions(noisy=(PROD[-1],)), limits={"ks": chart})
    stable = pick(result.tables["results"], metric="ks", segment="", monitoring_period=PROD[0])[0]
    assert stable["baseline_points"] == len(DEV) + len(OOT) and stable["limit_kind"] == "control"
    assert stable["baseline_mean"] == pytest.approx(stable["development_value"]) and stable["baseline_sd"] == 0.0
    assert stable["light"] == "green" and stable["compared_value"] == 0.0
    noisy = pick(result.tables["results"], metric="ks", segment="", monitoring_period=PROD[-1])[0]
    assert noisy["light"] == "red" and noisy["compared_value"] is None  # an infinite distance is not stored
    short = run(predictions(), limits={"ks": chart}, control_min_points=6)
    row = pick(short.tables["results"], metric="ks", segment="", monitoring_period=PROD[0])[0]
    assert row["light"] == "no_baseline" and row["baseline_points"] == 5 and row["baseline_mean"] is None


def test_calibration_ratio_is_colored_by_default() -> None:
    results = run(predictions()).tables["results"]
    ratio = pick(results, metric="calibration_ratio", segment="", monitoring_period=PROD[0])[0]
    assert ratio["light"] in ("green", "yellow", "red") and ratio["direction"] == "both"
    assert ratio["green_limit"] == 0.3 and ratio["red_limit"] == 0.5


def test_persistent_drift_opens_a_critical_alert_and_fails_the_gate() -> None:
    result = run(predictions(drifted=tuple(PROD[-2:])), gate={"fail_on": ["critical"]}, trend=None)
    spans = [a for a in result.tables["alerts"] if a["kind"] == "persistence"]
    psi = next(a for a in spans if a["metric"] == "psi" and a["segment"] == "" and a["model_version"] == "2")
    assert psi["severity"] == "critical" and psi["state"] == "open" and psi["opened_period"] == PROD[-1]
    assert psi["monitoring_period"] == PROD[-1] and psi["flagged_periods"] == 1
    lights = [a for a in result.tables["alerts"] if a["kind"] == "light"]
    assert lights and all(a["state"] == "open" and a["opened_period"] == a["monitoring_period"] for a in lights)
    assert result.outcome.critical_alerts > 0 and result.outcome.status == "failed"
    assert "open critical alert(s)" in result.outcome.message
    assert result.tables["runs"][0]["critical_alerts"] == result.outcome.critical_alerts
    quiet = run(predictions(drifted=tuple(PROD[-2:])), persistence={"red": 3}, trend=None)
    assert not [a for a in quiet.tables["alerts"] if a["kind"] == "persistence"]


def test_trend_check_flags_a_departure_from_the_series() -> None:
    result = run(predictions(drift=True), trend={"window": 3, "min_points": 3}, model_version_column=None,
                 model_version="1", gate={"fail_on": "alert"})  # fmt: skip
    psi = pick(result.tables["results"], metric="psi", segment="", monitoring_period=PROD[-1])[0]
    assert psi["trend_status"] == "red" and psi["trend_expected"] is not None
    early = pick(result.tables["results"], metric="psi", segment="", monitoring_period=PROD[0])[0]
    assert early["trend_status"] == "insufficient_history"
    trends = [a for a in result.tables["alerts"] if a["kind"] == "trend" and a["metric"] == "psi"]
    assert trends and trends[0]["severity"] == "critical" and trends[0]["state"] == "open"
    assert result.outcome.open_alerts >= len(trends) and result.outcome.status == "failed"
    untrended = run(predictions(drift=True), trend=None)
    assert {r["trend_status"] for r in untrended.tables["results"]} == {""}


def test_default_rows_carry_lag_zero_and_label_lag_is_stamped() -> None:
    plain = run(predictions()).tables["results"]
    assert {(r["lag"], r["lag_unit"]) for r in plain} == {(0, "periods")}
    lagged = run(predictions(), label_lag=1).tables["results"]
    assert {r["lag"] for r in pick(lagged, family="discrimination")} == {1}
    assert {r["lag"] for r in pick(lagged, family="score_psi")} == {0}


def test_lags_are_per_metric_and_calendar_lags_never_shift_over_a_gap() -> None:
    rows = [r for r in predictions() if r["month"] != "2025-07"]
    results = run(rows, lags={"auc": {"months": 1}, "gini": 1, "calibration": {"months": 2}}).tables["results"]
    ks = pick(results, metric="ks", segment="", monitoring_period="2025-08")[0]
    assert ks["data_period"] == "2025-08" and ks["lag"] == 0 and ks["value"] is not None
    gini = pick(results, metric="gini", segment="", monitoring_period="2025-08")[0]
    assert gini["data_period"] == "2025-06" and gini["lag"] == 1 and gini["lag_unit"] == "periods"
    auc = pick(results, metric="auc", segment="", monitoring_period="2025-08")[0]
    assert auc["data_period"] == "" and auc["value"] is None and auc["reason"] == "data_period_unavailable"
    assert (auc["lag"], auc["lag_unit"]) == (1, "months")
    auc_next = pick(results, metric="auc", segment="", monitoring_period="2025-09")[0]
    assert auc_next["data_period"] == "2025-08" and auc_next["value"] is not None
    brier = pick(results, metric="brier", segment="", monitoring_period="2025-09")[0]
    assert brier["data_period"] == "" and brier["value"] is None and brier["lag"] == 2


def test_calendar_lag_equals_the_observed_lag_when_nothing_is_missing() -> None:
    observed = run(predictions(), lags={"discrimination": 1}).tables["results"]
    calendar = run(predictions(), lags={"discrimination": {"months": 1}}).tables["results"]
    for metric in ("ks", "auc", "gini"):
        for period in PROD:
            a = pick(observed, metric=metric, segment="", monitoring_period=period)[0]
            b = pick(calendar, metric=metric, segment="", monitoring_period=period)[0]
            assert (a["data_period"], a["value"]) == (b["data_period"], b["value"])


def test_shift_months_keeps_the_period_format() -> None:
    assert shift_months("2025-03", 12) == "2024-03" and shift_months("2025-01", 1) == "2024-12"
    assert shift_months("202503", 3) == "202412" and shift_months("2025-03", 0) == "2025-03"
    assert shift_months("2025-03-31", 1) == "2025-02-28" and shift_months("20240331", 1) == "20240229"
    assert shift_months("2025-02-30", 1) is None and shift_months("2025-13", 1) is None
    assert shift_months("Q1-2025", 1) is None and shift_months("", 1) is None


@pytest.mark.parametrize(
    ("source", "months", "expected"),
    [
        ("2025-03-31", 1, "2025-02-28"),  # 31 -> 28
        ("2024-03-31", 1, "2024-02-29"),  # 31 -> 29 in a leap year
        ("2025-04-30", 1, "2025-03-31"),  # 30 -> 31
        ("2025-05-31", 1, "2025-04-30"),  # 31 -> 30
        ("2025-02-28", 1, "2025-01-31"),  # 28 -> 31
        ("2024-02-29", 1, "2024-01-31"),  # leap 29 -> 31
        ("2024-02-29", 12, "2023-02-28"),  # leap 29 -> 28
        ("2025-02-28", 12, "2024-02-29"),  # 28 -> leap 29, still a month end
        ("2025-06-30", 3, "2025-03-31"),  # several months
        ("2025-08-31", 6, "2025-02-28"),
        ("20250430", 1, "20250331"),  # compact format
        ("2025-04-15", 1, "2025-03-15"),  # mid-month days keep the day
        ("2025-03-30", 1, "2025-02-28"),  # mid-month day clamps down
    ],
)
def test_shift_months_follows_the_month_end(source: str, months: int, expected: str) -> None:
    assert shift_months(source, months) == expected


def test_month_end_closings_resolve_their_data_period() -> None:
    closings = ["2025-01-31", "2025-02-28", "2025-03-31", "2025-04-30", "2025-05-31"]
    found = {c: resolve_data_period(closings, c, Lag(1, "months")) for c in closings[1:]}
    assert found == {"2025-02-28": "2025-01-31", "2025-03-31": "2025-02-28",
                     "2025-04-30": "2025-03-31", "2025-05-31": "2025-04-30"}  # fmt: skip


def test_resolve_data_period_by_unit() -> None:
    periods = ["2025-01", "2025-03", "2025-04"]
    assert resolve_data_period(periods, "2025-04", Lag(1, "periods")) == "2025-03"
    assert resolve_data_period(periods, "2025-04", Lag(1, "months")) == "2025-03"
    assert resolve_data_period(periods, "2025-03", Lag(1, "months")) is None
    assert resolve_data_period(["a", "b"], "b", Lag(1, "months")) is None
    assert resolve_data_period(periods, "2025-03", Lag(0, "months")) == "2025-03"


NOW = "2026-02-01T00:00:00+00:00"


def _act(alert_id: str, action: str, at: str, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return action_row(model="churn", alert_id=alert_id, action=action, reason="reviewed", author="ana",
                      created_at=at, **extra)  # fmt: skip


def _stored(actions: list[dict[str, Any]]) -> MemoryStore:
    return MemoryStore(tables={"actions": actions})


def test_accepted_spans_stop_failing_the_critical_gate_until_they_expire() -> None:
    rows = predictions(drifted=tuple(PROD[-2:]))
    gate = {"gate": {"fail_on": ["critical"]}, "trend": None}
    first = run(rows, **gate)
    spans = [a for a in first.tables["alerts"] if a["kind"] == "persistence" and a["severity"] == "critical"]
    assert first.outcome.status == "failed" and spans[0]["action"] is None
    accept = [_act(a["alert_id"], "risk_accepted", "2026-01-01T00:00:00", valid_until="2999-01-01") for a in spans]
    held = run(rows, _stored(accept), run_id="r2", now=NOW, **gate)
    same = next(a for a in held.tables["alerts"] if a["alert_id"] == spans[0]["alert_id"])
    assert same["state"] == "accepted" and same["action"] == "risk_accepted" and same["valid_until"] == "2999-01-01"
    assert held.outcome.critical_alerts == 0 and held.outcome.status != "failed"
    assert f"{len(spans)} accepted" in held.outcome.message
    lapsed = [_act(a["alert_id"], "risk_accepted", "2026-01-01T00:00:00", valid_until="2026-01-31") for a in spans]
    again = run(rows, _stored(lapsed), run_id="r3", now=NOW, **gate)
    assert again.outcome.status == "failed" and again.outcome.critical_alerts == first.outcome.critical_alerts


def test_closed_spans_leave_the_open_counts_and_analysis_keeps_them() -> None:
    rows = predictions(drifted=tuple(PROD[-2:]))
    first = run(rows, trend=None)
    spans = [a for a in first.tables["alerts"] if a["kind"] == "persistence"]
    closed = _act(spans[0]["alert_id"], "closed", "2026-01-01T00:00:00")
    analysed = [_act(s["alert_id"], "analysis", "2026-01-01T00:00:00") for s in spans[1:]]
    after = run(rows, _stored([closed, *analysed]), run_id="r2", now=NOW, trend=None)
    states = {a["alert_id"]: a["state"] for a in after.tables["alerts"] if a["kind"] == "persistence"}
    assert states[spans[0]["alert_id"]] == "closed" and all(states[s["alert_id"]] == "open" for s in spans[1:])
    assert after.outcome.open_alerts == first.outcome.open_alerts - 1
    assert after.tables["actions"] == []  # a run never writes actions


def test_an_accepted_light_alert_is_not_counted_by_the_red_gate() -> None:
    rows = predictions(drift=True)
    first = run(rows, gate={"fail_on": ["red"]}, trend=None)
    reds = [a for a in first.tables["alerts"] if a["kind"] == "light" and a["light"] == "red"
            and a["monitoring_period"] == PROD[-1]]  # fmt: skip
    assert first.outcome.status == "failed" and len(reds) == first.outcome.red
    every = [_act(a["alert_id"], "risk_accepted", "2026-01-01T00:00:00", valid_until="2999-01-01") for a in reds]
    held = run(rows, _stored(every), run_id="r2", now=NOW, gate={"fail_on": ["red"]}, trend=None)
    assert held.outcome.red == 0 and held.outcome.status != "failed"
    closed = [_act(a["alert_id"], "closed", "2026-01-01T00:00:00") for a in reds]
    still = run(rows, _stored(closed), run_id="r3", now=NOW, gate={"fail_on": ["red"]}, trend=None)
    assert still.outcome.status == "failed"  # closing a light alert is bookkeeping, not suppression


def _small(**changes: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    results = run(predictions(per_month=40), **changes).tables["results"]
    return pick(results, metric="csi", column="age", segment="n")


def test_default_limits_and_gate_are_unchanged_without_the_sample_size_keys() -> None:
    explicit = _small(psi_limits="fixed", min_rows_per_bin=0, psi_smoothing="epsilon")
    default = _small()
    assert default == explicit
    assert {(r["green_limit"], r["red_limit"]) for r in default} == {(0.10, 0.25)}


def test_sample_size_limits_raise_small_segment_csi_limits_and_store_them() -> None:
    rows = _small(psi_limits="sample_size")
    assert rows
    for row in rows:
        assert row["green_limit"] > 0.10
        assert row["red_limit"] > 0.25
        assert row["light"] != "red"  # the periods carry no real change


def test_sample_size_limits_never_lower_a_declared_limit() -> None:
    declared = {"csi": {"green": 5.0, "red": 9.0}}
    rows = _small(psi_limits="sample_size", limits=declared)
    assert {(r["green_limit"], r["red_limit"]) for r in rows} == {(5.0, 9.0)}


def test_min_rows_per_bin_reports_insufficient_volume_and_laplace_changes_the_value() -> None:
    gated = _small(min_rows_per_bin=1000)
    assert gated
    assert {r["light"] for r in gated} == {"insufficient_volume"}
    plain, smooth = _small(), _small(psi_smoothing="laplace")
    assert [r["value"] for r in plain] != [r["value"] for r in smooth]
