"""Credit-model performance checks on synthetic predictions: grade tests, inversions, horizons, exclusions, bad rate."""
# ruff: noqa: PT018 - dense assertions on result rows

from __future__ import annotations

import statistics
from typing import Any

import pytest

from bricksmith.monitor.credit import binomial_p, jeffreys_p, rescale
from bricksmith.monitor.model.alerts import replay_alerts
from bricksmith.monitor.model.config import parse_config
from bricksmith.monitor.model.credit import grade_order
from bricksmith.monitor.model.engine import MemoryStore, check, frame_of, jobs_of

DEV = ("2025-01", "2025-02", "2025-03")
PROD = ("2025-04", "2025-05", "2025-06")
#: Grade -> (probability over the 12-period horizon, rows per month).
GRADES = {"A": (0.02, 400), "B": (0.10, 200), "C": (0.30, 100)}
#: Month -> grade -> observed event rate (calibrated unless a test changes it).
CALIBRATED = {month: {g: pd for g, (pd, _) in GRADES.items()} for month in DEV + PROD}
DEV_FACTORS = {"2025-01": 0.9, "2025-02": 1.0, "2025-03": 1.2}


def predictions(rates: dict[str, dict[str, float]] | None = None, *, shifted_exclusions: bool = False) -> list[dict]:
    """Return scored rows: each grade scores its probability, the first ``rate * n`` rows of a month have the event.

    ``bad_3`` is the event over 3 periods, observed at the rescaled probability. The exclusion cascade's first reason
    is null for 90% of the rows (5% ``age``, 5% ``fraud``); ``shifted_exclusions`` moves June to 25% ``fraud``.
    """
    rates = rates or CALIBRATED
    rows: list[dict[str, Any]] = []
    for month in DEV + PROD:
        factor = DEV_FACTORS.get(month, 1.0)
        for grade, (pd, count) in GRADES.items():
            events = round(count * min(rates[month][grade] * factor, 1.0))
            early = round(count * rescale(pd, 3, 12))
            for i in range(count):
                share = i % 20
                fraud = 5 if shifted_exclusions and month == PROD[-1] else 1
                reason = "fraud" if share < fraud else "age" if share == 19 else None
                rows.append({
                    "month": month, "sample": "dev" if month in DEV else "prod", "score": pd, "grade": grade,
                    "bad": int(i < events), "bad_3": int(i < early), "exclusion_first": reason,
                    "region": "n" if i % 2 else "s",
                })  # fmt: skip
    return rows


DECLARATION: dict[str, Any] = {
    "model": "pd",
    "period": "month",
    "score": "score",
    "label": "bad",
    "grade": "grade",
    "sample": "sample",
    "references": {"development": {"sample": "dev"}},
    "model_version": "1",
    "min_rows": 10,
    "credit": {
        "horizon": 12,
        "grades": ["A", "B", "C"],
        "early_warning": [{"label": "bad_3", "periods": 3, "lag": 1}],
        "exclusion": "exclusion_first",
    },
}


def run(rows: list[dict], **changes: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    config = parse_config({**DECLARATION, **changes})
    return check(rows, config, MemoryStore(), run_id="r1", now="t0").tables["results"]


def run_all(rows: list[dict], **changes: Any) -> dict[str, list[dict[str, Any]]]:  # noqa: ANN401
    config = parse_config({**DECLARATION, **changes})
    return check(rows, config, MemoryStore(), run_id="r1", now="t0").tables


def find(results: list[dict], metric: str, period: str, column: str = "", segment: str = "") -> dict[str, Any]:
    found = [
        r for r in results
        if r["metric"] == metric and r["monitoring_period"] == period and r["column"] == column
        and r["segment"] == segment
    ]  # fmt: skip
    assert len(found) == 1, (metric, period, column, found)
    return found[0]


def test_without_a_credit_block_nothing_changes() -> None:
    plain = {k: v for k, v in DECLARATION.items() if k != "credit"}
    config = parse_config(plain)
    results = check(predictions(), config, MemoryStore(), run_id="r1", now="t0").tables["results"]
    assert {r["family"] for r in results} == {"score_psi", "grade_psi", "discrimination", "calibration", "volumetry"}
    assert all(not j[0].startswith(("grade_c", "grade_o", "early", "exclusion", "bad")) for j in jobs_of(config))


def test_rows_sums_count_events_and_rescaled_expectation() -> None:
    frame = frame_of([
        {"s": 0.2, "y": 1, "g": "A"}, {"s": 0.2, "y": 0, "g": "A"}, {"s": 1.5, "y": 1, "g": "B"},
        {"s": None, "y": 1, "g": "B"}, {"s": 0.4, "y": None, "g": "B"}, {"s": 0.4, "y": 0, "g": None},
    ])  # fmt: skip
    assert frame.sums("s", "y", 1.0, "g") == {"A": (2, 1, pytest.approx(0.4)), "B": (1, 1, 1.0)}
    assert frame.sums("s", "y", 0.25)[""] == (4, 2, pytest.approx(2 * rescale(0.2, 3, 12) + 1 + rescale(0.4, 3, 12)))


def test_calibrated_grades_are_green() -> None:
    results = run(predictions())
    for grade, (pd, count) in GRADES.items():
        oe = find(results, "grade_oe", "2025-05", f"grade={grade}")
        assert oe["value"] == pytest.approx(round(count * pd) / (count * pd)) and oe["light"] == "green"
        assert oe["family"] == "grade_calibration" and oe["rows"] == count
        jeffreys = find(results, "grade_jeffreys_p", "2025-05", f"grade={grade}")
        assert (
            jeffreys["value"] == pytest.approx(jeffreys_p(count, round(count * pd), pd))
            and jeffreys["light"] == "green"
        )
        binomial = find(results, "grade_binomial_p", "2025-05", f"grade={grade}")
        assert binomial["value"] == pytest.approx(binomial_p(count, round(count * pd), pd))
    assert not [r for r in results if r["metric"] == "grade_binomial_adjusted_p"]
    inversions = find(results, "grade_inversions", "2025-05", "grade")
    assert inversions["value"] == 0 and inversions["light"] == "green" and inversions["family"] == "grade_order"


def test_an_underestimated_grade_is_red() -> None:
    rates = {m: dict(g) for m, g in CALIBRATED.items()}
    rates["2025-06"]["C"] = 0.5
    results = run(predictions(rates), credit={**DECLARATION["credit"], "correlation": 0.15})
    assert find(results, "grade_oe", "2025-06", "grade=C")["light"] == "red"
    assert find(results, "grade_jeffreys_p", "2025-06", "grade=C")["light"] == "red"
    assert find(results, "grade_binomial_p", "2025-06", "grade=C")["light"] == "red"
    adjusted = find(results, "grade_binomial_adjusted_p", "2025-06", "grade=C")
    assert adjusted["value"] > find(results, "grade_binomial_p", "2025-06", "grade=C")["value"]
    assert find(results, "grade_oe", "2025-06", "grade=A")["light"] == "green"


def test_a_swapped_grade_pair_is_one_inversion_with_the_calibration_table() -> None:
    rates = {m: dict(g) for m, g in CALIBRATED.items()}
    rates["2025-05"]["B"] = 0.01
    tables = run_all(predictions(rates))
    inversions = find(tables["results"], "grade_inversions", "2025-05", "grade")
    assert inversions["value"] == 1 and inversions["light"] == "yellow"
    details = [
        d for d in tables["details"] if d["metric"] == "grade_inversions" and d["monitoring_period"] == "2025-05"
    ]
    assert [(d["bin"], d["bin_order"], d["count"]) for d in details] == [("A", 0, 400), ("B", 1, 200), ("C", 2, 100)]
    assert details[1]["observed_rate"] == pytest.approx(0.01) and details[1]["mean_score"] == pytest.approx(0.10)


def test_few_expected_events_are_insufficient_volume_whatever_the_observed_count() -> None:
    rows = predictions()
    rows += [
        {"month": "2025-06", "sample": "prod", "score": 0.001, "grade": "D", "bad": int(i < 50), "bad_3": 0,
         "exclusion_first": None, "region": "n"}
        for i in range(100)
    ]  # fmt: skip
    results = run(rows)
    for metric in ("grade_oe", "grade_jeffreys_p", "grade_binomial_p"):
        row = find(results, metric, "2025-06", "grade=D")
        assert row["light"] == "insufficient_volume" and row["value"] is not None
        assert row["reason"].startswith("expected events 0.1 < min 5")
    assert find(results, "grade_inversions", "2025-06", "grade")["value"] == 0  # D is not compared
    lowered = run(rows, credit={**DECLARATION["credit"], "min_expected_events": 0.05})
    assert find(lowered, "grade_oe", "2025-06", "grade=D")["light"] == "red"


def _replayed(rows: list[dict], **changes: Any) -> tuple[list[dict], list[dict]]:  # noqa: ANN401
    config = parse_config({**DECLARATION, **changes})
    tables = check(rows, config, MemoryStore(), run_id="r1", now="t0").tables
    return replay_alerts(tables["results"], tables["runs"], config=config, created_at="2025-07-01T00:00:00")


def test_replay_with_a_config_keeps_the_expected_events_gate() -> None:
    rows = predictions()
    rows += [
        {"month": "2025-06", "sample": "prod", "score": 0.001, "grade": "D", "bad": int(i < 50), "bad_3": 0,
         "exclusion_first": None, "region": "n"}
        for i in range(100)
    ]  # fmt: skip
    results, alerts = _replayed(rows)
    for metric in ("grade_oe", "grade_jeffreys_p", "grade_binomial_p"):
        row = find(results, metric, "2025-06", "grade=D")
        assert row["light"] == "insufficient_volume" and row["reason"].startswith("expected events 0.1 < min 5")
    assert not [a for a in alerts if "grade=D" in str(a.get("column"))]
    lowered, _ = _replayed(rows, credit={**DECLARATION["credit"], "min_expected_events": 0.05})
    assert find(lowered, "grade_oe", "2025-06", "grade=D")["light"] == "red"


def test_replay_exempts_expected_event_results_from_min_rows() -> None:
    rows = predictions()
    rows += [
        {"month": "2025-06", "sample": "prod", "score": 0.9, "grade": "E", "bad": 6, "bad_3": 0,
         "exclusion_first": None, "region": "n"}
        for _ in range(6)
    ]  # fmt: skip
    run_row = find(run(rows), "grade_oe", "2025-06", "grade=E")
    replayed, _ = _replayed(rows)
    row = find(replayed, "grade_oe", "2025-06", "grade=E")
    assert row["rows"] == 6 < DECLARATION["min_rows"]
    assert row["light"] == run_row["light"] != "insufficient_volume"


def test_early_warning_reads_its_lag_label_and_rescaled_expectation() -> None:
    results = run(predictions())
    oe = find(results, "early_oe", "2025-06", "bad_3")
    assert oe["data_period"] == "2025-05" and oe["lag"] == 1 and oe["family"] == "early_warning"
    events = sum(round(n * rescale(pd, 3, 12)) for pd, n in GRADES.values())
    expected = sum(n * rescale(pd, 3, 12) for pd, n in GRADES.values())
    assert oe["value"] == pytest.approx(events / expected) and oe["light"] == "green"
    ks = find(results, "early_ks", "2025-06", "bad_3")
    assert ks["value"] > 0.2 and find(results, "early_gini", "2025-06", "bad_3")["value"] > 0.2
    assert find(results, "early_oe", "2025-04", "bad_3")["reason"] == "data_period_unavailable"
    unscaled = run(predictions(), credit={**DECLARATION["credit"], "horizon": 3})
    assert find(unscaled, "early_oe", "2025-06", "bad_3")["value"] < 0.5


def test_exclusion_mix_reacts_to_a_changed_first_reason_mix() -> None:
    tables = run_all(predictions(shifted_exclusions=True))
    shifted = find(tables["results"], "exclusion_psi", "2025-06", "exclusion_first")
    steady = find(tables["results"], "exclusion_psi", "2025-05", "exclusion_first")
    assert steady["value"] == pytest.approx(0.0, abs=1e-12) and steady["light"] == "green"
    assert shifted["value"] > 0.25 and shifted["light"] == "red" and shifted["family"] == "exclusion_mix"
    bins = {
        d["bin"]: d for d in tables["details"] if d["metric"] == "exclusion_psi" and d["monitoring_period"] == "2025-06"
    }
    assert set(bins) == {"(none)", "age", "fraud"}
    assert bins["fraud"]["reference_share"] == pytest.approx(0.05) and bins["fraud"]["current_share"] == pytest.approx(
        0.25
    )


def test_bad_rate_chart_is_centred_on_the_development_median() -> None:
    rates = {m: dict(g) for m, g in CALIBRATED.items()}
    rates["2025-06"] = {g: pd * 1.6 for g, (pd, _) in GRADES.items()}
    rows = predictions(rates)
    results = run(rows)
    per_month = []
    for month in DEV:
        month_rows = [r for r in rows if r["month"] == month]
        per_month.append(sum(r["bad"] for r in month_rows) / len(month_rows))
    june = find(results, "bad_rate", "2025-06")
    assert june["limit_kind"] == "control" and june["baseline_points"] == 3
    assert june["baseline_mean"] == pytest.approx(statistics.median(per_month))
    assert june["baseline_mean"] != pytest.approx(statistics.fmean(per_month))
    assert june["baseline_sd"] == pytest.approx(statistics.stdev(per_month)) and june["light"] == "red"
    assert find(results, "bad_rate", "2025-05")["light"] == "green"


def test_credit_metrics_are_measured_per_segment_and_follow_rules() -> None:
    results = run(
        predictions(),
        segment="region",
        rules=[{"id": "c-wide", "column": "grade=C", "metric": "grade_oe", "limits": {"green": 0.9, "red": 0.95,
                                                                                    "direction": "both", "center": 1}}],
    )  # fmt: skip
    north = find(results, "grade_oe", "2025-05", "grade=C", "n")
    assert north["rows"] == 50 and north["rule_id"] == "c-wide"
    assert find(results, "grade_oe", "2025-05", "grade=B", "n")["rule_id"] == ""
    assert find(results, "bad_rate", "2025-05", "", "s")["rows"] == 350


def test_a_failing_credit_family_is_isolated() -> None:
    rows = [{**r, "exclusion_first": {"not": "hashable"}} if r["month"] == "2025-06" else r for r in predictions()]
    tables = run_all(rows)
    broken = find(tables["results"], "exclusion_psi", "2025-06", "exclusion_first")
    assert broken["value"] is None and broken["reason"].startswith("error: TypeError")
    assert find(tables["results"], "grade_oe", "2025-06", "grade=A")["light"] == "green"
    assert tables["runs"][0]["status"] == "partial"


def numeric_grade_rows() -> list[dict]:
    """Return rows of integer grades 1..11 whose probability and observed rate rise with the grade, every month."""
    rows: list[dict[str, Any]] = []
    for month in DEV + PROD:
        for grade in range(1, 12):
            pd, count = 0.01 * grade, 600
            events = round(count * pd)
            rows += [
                {"month": month, "sample": "dev" if month in DEV else "prod", "score": pd, "grade": grade,
                 "bad": int(i < events)}
                for i in range(count)
            ]  # fmt: skip
    return rows


def test_undeclared_numeric_grades_are_ordered_by_number() -> None:
    tables = run_all(numeric_grade_rows(), credit={"min_expected_events": 5})
    for period in PROD:
        inversions = find(tables["results"], "grade_inversions", period, "grade")
        assert inversions["value"] == 0 and inversions["light"] == "green"
        details = sorted(
            (d for d in tables["details"] if d["metric"] == "grade_inversions" and d["monitoring_period"] == period),
            key=lambda d: d["bin_order"],
        )
        assert [d["bin"] for d in details] == [str(g) for g in range(1, 12)]


@pytest.mark.parametrize(
    ("present", "order"),
    [
        (["10", "2", "1.5", "-1", "1.0", "1"], ["-1", "1", "1.0", "1.5", "2", "10"]),
        (["A", "10", "2"], ["10", "2", "A"]),
        (["10", "2", "nan"], ["10", "2", "nan"]),
        (["10", "inf", "2"], ["10", "2", "inf"]),
    ],
)
def test_undeclared_grades_are_numeric_only_when_every_text_is_a_finite_number(
    present: list[str], order: list[str]
) -> None:
    config = parse_config({**DECLARATION, "credit": {}})
    assert grade_order(config, present) == order


def test_declared_grades_come_first_and_the_rest_follow_by_number() -> None:
    config = parse_config({**DECLARATION, "credit": {"grades": ["3"]}})
    assert grade_order(config, ["10", "3", "2"]) == ["3", "2", "10"]
