"""The model monitoring engine on synthetic predictions: families, periods, references, isolation and alerts."""
# ruff: noqa: PT018 - dense assertions on result rows

from __future__ import annotations

from typing import Any

import pytest

from bricksmith.monitor.bins import BinSpec
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.model.config import parse_config
from bricksmith.monitor.model.engine import MemoryStore, check, data_period, jobs_of
from bricksmith.monitor.reference import Reference

DEV = ["2025-01", "2025-02", "2025-03"]
OOT = ["2025-04", "2025-05"]
PROD = ["2025-06", "2025-07", "2025-08", "2025-09"]


def predictions(*, drift: bool = False, per_month: int = 200) -> list[dict[str, Any]]:
    """Return scored rows: score and label agree, grade buckets the score, the last month drifts on request."""
    rows: list[dict[str, Any]] = []
    for sample, months in (("dev", DEV), ("oot", OOT), ("prod", PROD)):
        for month in months:
            shifted = drift and month == PROD[-1]
            for i in range(per_month):
                score = ((i * 37) % per_month) / per_month
                if shifted:
                    score = min(score + 0.4, 0.999)
                label = 1 if ((i * 61) % 100) / 100 < score else 0
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


def run(rows: list[dict[str, Any]], store: MemoryStore | None = None, run_id: str = "r1", **changes: Any) -> Any:  # noqa: ANN401
    return check(rows, parse_config({**DECLARATION, **changes}), store or MemoryStore(), run_id=run_id, now="t0")


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
