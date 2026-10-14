"""Hand-derived bins, PSI/CSI, discrimination and calibration values (offline, pure Python)."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

import math

import pytest

from bricksmith.monitor import Measurement, MonitorError, Reference, fit_reference, measure
from bricksmith.monitor.bins import ABOVE, BELOW, NULL, UNSEEN, fit_bins
from bricksmith.monitor.stats import auc_groups, auc_rank, groups_from_lists, ks_groups, psi

NAN = float("nan")


def by_key(measurements: list[Measurement]) -> dict[tuple[str, str, str], Measurement]:
    return {m.key: m for m in measurements}


def value(measurements: list[Measurement], metric: str, subject: str = "", segment: str = "") -> float | None:
    return by_key(measurements)[(metric, subject, segment)].value


def test_quantile_bins_nearest_rank_and_counts() -> None:
    # n = 8, B = 4: cut ranks ceil(2), ceil(4), ceil(6) -> x2 = 2, x4 = 4, x6 = 6; edges [1, 2, 4, 6, 8].
    spec = fit_bins("x", dict.fromkeys(range(1, 9), 1), {"type": "numeric", "bins": 4})
    assert spec.edges == (1.0, 2.0, 4.0, 6.0, 8.0)
    assert spec.counts == {BELOW: 0, "bin_0": 2, "bin_1": 2, "bin_2": 2, "bin_3": 2, ABOVE: 0, NULL: 0}
    # Boundaries: right-closed, first bin closed on the left, outliers and null bins explicit.
    assert [spec.assign(v) for v in (0, 1, 2, 2.5, 8, 9, None, NAN)] == [
        BELOW, "bin_0", "bin_0", "bin_1", "bin_3", ABOVE, NULL, NULL,
    ]  # fmt: skip
    clip = fit_bins("x", dict.fromkeys(range(1, 9), 1), {"type": "numeric", "bins": 4, "outliers": "clip"})
    assert clip.assign(0) == "bin_0" and clip.assign(9) == "bin_3" and BELOW not in clip.bin_ids()
    assert spec.assign(float("inf")) == ABOVE and spec.assign(float("-inf")) == BELOW


def test_quantile_ties_and_constant_columns_never_make_empty_bins() -> None:
    # [1,1,1,1,2,2,3,3], B = 4: cuts x2 = 1, x4 = 1, x6 = 2 -> distinct {1, 2}; edges [1, 1, 2, 3].
    spec = fit_bins("x", {1: 4, 2: 2, 3: 2}, {"type": "numeric", "bins": 4})
    assert spec.edges == (1.0, 1.0, 2.0, 3.0)
    assert [spec.counts[f"bin_{i}"] for i in range(3)] == [4, 2, 2]
    constant = fit_bins("x", {5: 3}, {"type": "numeric", "bins": 3})
    assert constant.edges == (5.0, 5.0) and constant.counts["bin_0"] == 3


def test_fixed_edges_validation_and_unfittable_data() -> None:
    spec = fit_bins("x", {0.5: 1, 5: 1, None: 2}, {"type": "numeric", "edges": [0, 1, 10]})
    assert spec.counts == {BELOW: 0, "bin_0": 1, "bin_1": 1, ABOVE: 0, NULL: 2} and spec.method == "fixed"
    with pytest.raises(MonitorError, match="strictly increasing"):
        fit_bins("x", {1: 1}, {"edges": [1, 1, 2]})
    with pytest.raises(MonitorError, match="no non-null"):
        fit_bins("x", {None: 3}, {"type": "numeric", "bins": 2})
    with pytest.raises(MonitorError, match="outliers"):
        fit_bins("x", {1: 1}, {"edges": [0, 1], "outliers": "drop"})


def test_categorical_bins_null_unseen_and_rare_categories() -> None:
    counts = {"a": 5, "b": 3, "c": 1, True: 1, None: 2}
    spec = fit_bins("k", counts, {"type": "categorical", "min_count": 2})
    assert spec.categories == ("a", "b")  # count desc then value; rare c and true fall to unseen
    assert spec.counts == {"cat:a": 5, "cat:b": 3, UNSEEN: 2, NULL: 2}
    assert spec.assign("a") == "cat:a" and spec.assign("zzz") == UNSEEN and spec.assign(None) == NULL
    capped = fit_bins("k", {"x": 3, "y": 3, "z": 3}, {"type": "categorical", "max_categories": 2})
    assert capped.categories == ("x", "y") and capped.counts[UNSEEN] == 3
    assert fit_bins("k", {True: 2, False: 1}, {"type": "categorical"}).categories == ("true", "false")


def test_psi_hand_values_and_zero_count_handling() -> None:
    # p = (.5, .3, .2), q = (.2, .3, .5): (.2-.5) ln(.4) + 0 + (.5-.2) ln(2.5) = 0.6 ln 2.5
    assert psi({"a": 5, "b": 3, "c": 2}, {"a": 2, "b": 3, "c": 5}) == pytest.approx(0.6 * math.log(2.5))
    assert psi({"a": 4, "b": 6}, {"a": 8, "b": 12}) == pytest.approx(0.0)
    # Zero reference share is floored at 1e-4: p = (.5, .5, 0), q = (.4, .4, .2)
    # = 2 (-.1) ln(.8) + (.2 - 1e-4) ln(.2 / 1e-4)
    expected = 2 * (-0.1) * math.log(0.8) + 0.1999 * math.log(2000)
    assert psi({"a": 5, "b": 5, "c": 0}, {"a": 4, "b": 4, "c": 2}) == pytest.approx(expected)
    # A zero current share is floored the same way: p = (.5, .5, .0) vs q = (.5, .25, .25) is finite.
    assert math.isfinite(psi({"a": 2, "b": 2, "c": 0}, {"a": 2, "b": 1, "c": 1}))
    with pytest.raises(ZeroDivisionError):
        psi({"a": 1}, {"a": 0})


def test_psi_and_csi_through_the_reference_and_zero_bins() -> None:
    rows = [{"score": float(v), "age": v, "plan": "a" if v < 5 else "b"} for v in range(1, 9)]
    reference = fit_reference(
        rows,
        {"score": {"bins": 4}, "age": {"bins": 4}, "plan": {"type": "categorical"}},
        name="r", version=1, score="score",
    )  # fmt: skip
    shifted = [{"score": v, "age": v, "plan": "a" if v < 3 else "c"} for v in (1, 2, 1, 2, 3, 4, 5, 7)]
    out = measure(reference, shifted)
    # Counts [4, 2, 1, 1] of 8 against [2, 2, 2, 2]: 0.25 ln 2 + 0 + 2 (-.125) ln(.5) = 0.5 ln 2.
    assert value(out, "psi", "score") == pytest.approx(0.5 * math.log(2))
    assert value(out, "csi", "age") == pytest.approx(0.5 * math.log(2))
    assert ("csi", "score", "") not in by_key(out) and ("psi", "age", "") not in by_key(out)
    # plan: reference a:4 b:4; current a:4 unseen(c):4 (b disappears -> floored to 1e-4 only on the current side).
    plan = by_key(out)[("csi", "plan", "")]
    assert plan.value == pytest.approx(
        (0.5 - 0.5) * 0 + (1e-4 - 0.5) * math.log(1e-4 / 0.5) + (0.5 - 1e-4) * math.log(0.5 / 1e-4)
    )
    assert by_key(out)[("coverage", "plan", "")].value == pytest.approx(0.5)
    assert by_key(out)[("coverage", "plan", "")].detail[UNSEEN] == pytest.approx(0.5)


def test_auc_with_ties_gini_and_ks() -> None:
    scores, labels = [0.1, 0.4, 0.4, 0.8], [0, 0, 1, 1]
    # Pairs (pos, neg): .4>.1 win, .4=.4 tie (half), .8>.1 win, .8>.4 win: (1 + .5 + 1 + 1) / 4
    assert auc_rank(scores, labels) == pytest.approx(0.875)
    assert auc_groups(groups_from_lists(scores, labels)) == pytest.approx(0.875)
    # KS: |F_pos - F_neg| at .1: |0 - .5|, at .4: |.5 - 1|, at .8: 0.
    assert ks_groups(groups_from_lists(scores, labels)) == pytest.approx(0.5)
    assert auc_rank([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == 1.0
    assert ks_groups(groups_from_lists([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1])) == pytest.approx(1.0)
    assert auc_rank([0.5, 0.5, 0.5, 0.5], [0, 1, 0, 1]) == 0.5  # all tied: no discrimination
    assert auc_rank([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1]) == 0.0
    with pytest.raises(ZeroDivisionError):
        auc_rank([0.1, 0.2], [1, 1])


def test_rank_and_group_auc_agree_on_a_deterministic_sample() -> None:
    state = 12345
    scores, labels = [], []
    for _ in range(200):
        state = (state * 1103515245 + 12345) % 2**31
        scores.append((state % 17) / 16)  # many ties
        labels.append(1 if (state >> 8) % 3 == 0 else 0)
    assert auc_rank(scores, labels) == pytest.approx(auc_groups(groups_from_lists(scores, labels)))


def model_reference(**extra: object) -> Reference:
    fit = [{"s": 0.5, "y": 1}, {"s": 0.5, "y": 0}]
    return fit_reference(fit, {"s": {"edges": [0, 1]}}, name="m", version=1, score="s", label="y", **extra)


def test_discrimination_and_calibration_metrics() -> None:
    reference = model_reference(metric_config={"calibration_bins": 2})
    rows = [{"s": s, "y": y} for s, y in zip([0.1, 0.2, 0.7, 0.9], [0, 1, 1, 1], strict=True)]
    out = measure(reference, rows)
    # AUC: positives {.2, .7, .9} vs negative {.1}: 3 of 3 pairs won.
    assert value(out, "auc", "s") == 1.0 and value(out, "gini", "s") == 1.0 and value(out, "ks", "s") == 1.0
    # Bins of width .5: {.1, .2} mean .15 observed .5; {.7, .9} mean .8 observed 1; ECE = .5*.35 + .5*.2
    assert value(out, "ece", "s") == pytest.approx(0.275)
    # Brier: squared errors .01, .64, .09, .01 averaged over 4 rows
    assert value(out, "brier", "s") == pytest.approx(0.1875)
    table = by_key(out)[("ece", "s", "")].detail["bins"]
    assert table["0"] == {"count": 2, "mean_score": pytest.approx(0.15), "observed_rate": 0.5}
    edge = measure(reference, [{"s": 1.0, "y": 1}, {"s": 0.0, "y": 0}])  # score 1.0 belongs to the last bin
    assert by_key(edge)[("ece", "s", "")].detail["bins"]["1"]["count"] == 1


def test_unavailable_metrics_carry_a_reason() -> None:
    reference = model_reference()
    cases = {
        "missing_labels": [{"s": 0.3, "y": None}, {"s": 0.6, "y": None}],
        "single_class": [{"s": 0.3, "y": 1}, {"s": 0.6, "y": 1}],
        "invalid_labels": [{"s": 0.3, "y": 2}, {"s": 0.6, "y": 1}],
        "missing_scores": [{"s": None, "y": 1}],
    }
    for reason, rows in cases.items():
        m = by_key(measure(reference, rows))[("auc", "s", "")]
        assert (m.value, m.status, m.reason) == (None, "unavailable", reason)
    out = by_key(measure(reference, [{"s": 1.5, "y": 1}, {"s": 0.2, "y": 0}]))
    assert out[("auc", "s", "")].value == 1.0 and out[("brier", "s", "")].reason == "score_out_of_range"
    empty = by_key(measure(reference, []))
    assert empty[("psi", "s", "")].reason == "empty_window" and empty[("auc", "s", "")].reason == "empty_window"
    assert empty[("row_count", "", "")].value == 0.0  # a true zero, not an invented normal value


def test_stability_by_period_and_coverage() -> None:
    fit = [{"x": v, "t": "2026-01-01"} for v in range(1, 9)]
    reference = fit_reference(
        fit, {"x": {"bins": 4}}, name="s", version=1, time="t", metric_config={"stability_period": "month"}
    )
    jan = [{"x": v, "t": "2026-01-15"} for v in (1, 2, 3, 4, 5, 6, 7, 8)]
    feb = [{"x": v, "t": "2026-02-15"} for v in (1, 2, 1, 2, 3, 4, 5, 7)]
    out = by_key(measure(reference, jan + feb))
    stab = out[("stability", "x", "")]
    assert stab.value == pytest.approx(0.5 * math.log(2))
    assert stab.detail["periods"] == {"2026-01": pytest.approx(0.0), "2026-02": pytest.approx(0.5 * math.log(2))}
    single = by_key(measure(reference, jan))[("stability", "x", "")]
    assert (single.value, single.reason) == (None, "insufficient_periods")
    assert out[("volume_ratio", "", "")].value == 2.0 and out[("coverage", "x", "")].value == 1.0


def test_reference_roundtrip_fingerprint_and_no_refit() -> None:
    reference = model_reference(alert_policy=[{"name": "r", "metric": "auc", "op": "<", "threshold": 0.6}])
    assert Reference.from_json(reference.to_json()) == reference
    before = reference.to_json()
    shifted = [{"s": 0.99, "y": 1}] * 5 + [{"s": 0.98, "y": 0}] * 5
    measure(reference, shifted)
    assert reference.to_json() == before  # comparing never changes the bins or counts
    assert reference.column("s").counts["bin_0"] == 2  # fit counts, not the compared rows
    other = model_reference(metric_config={"calibration_bins": 5})
    assert other.fingerprint != reference.fingerprint
    tampered = reference.to_dict() | {"name": "other"}
    with pytest.raises(MonitorError, match="fingerprint"):
        Reference.from_dict(tampered)
    with pytest.raises(MonitorError, match="empty window"):
        fit_reference([], {"s": {"bins": 2}}, name="e", version=1)
