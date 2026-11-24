from __future__ import annotations

import math

import pytest

from bricksmith.mlops.selection.metrics import (
    Moments,
    chi2_ppf,
    correlation_matrix,
    discrimination,
    eta,
    gamma_p,
    mid_rank_map,
    monotone_groups,
    psi_critical,
    quality_metrics,
    rank_moments,
    rate_groups,
    vif,
    woe_table,
)
from bricksmith.monitor.bins import BinSpec
from bricksmith.monitor.stats import mid_ranks


def test_quality_shares_ignore_nulls_and_nan_for_the_constant_share() -> None:
    metrics = quality_metrics({None: 1, math.nan: 1, 1: 7, 2: 1})
    assert metrics == {"missing_share": 0.2, "constant_share": 7 / 8, "cardinality": 2.0}
    assert quality_metrics({None: 3}) == {"missing_share": 1.0, "constant_share": 1.0, "cardinality": 0.0}


def test_iv_and_woe_follow_the_credit_scoring_convention() -> None:
    spec = BinSpec("kind", "categorical", categories=("a", "b"))
    table = woe_table(spec, {"cat:a": (10, 30), "cat:b": (30, 10)})
    assert [g.bins for g in table.groups] == [("cat:a",), ("cat:b",)]
    assert table.groups[0].woe == pytest.approx(math.log(3))
    assert table.groups[1].woe == pytest.approx(-math.log(3))
    assert table.iv == pytest.approx(math.log(3))
    assert table.woe_of("a") == pytest.approx(math.log(3))
    assert table.woe_of("never seen") == 0.0


def test_an_empty_class_is_smoothed_and_the_null_bin_stays_apart() -> None:
    spec = BinSpec("kind", "categorical", categories=("a", "b"))
    table = woe_table(spec, {"cat:a": (0, 20), "cat:b": (20, 20), "null": (5, 5)}, smoothing=0.5)
    total_e, total_n = 0.5 + 20 + 5, 20.5 + 20 + 5
    assert table.groups[0].woe == pytest.approx(math.log((20.5 / total_n) / (0.5 / total_e)))
    assert table.groups[-1].bins == ("null",)
    assert table.woe_of(None) == pytest.approx(math.log((5 / total_n) / (5 / total_e)))


def test_monotone_binning_pools_adjacent_violators() -> None:
    groups = [(("bin_0",), 10, 90), (("bin_1",), 5, 95), (("bin_2",), 20, 80)]
    assert monotone_groups(groups, increasing=True) == [(("bin_0", "bin_1"), 15, 185), (("bin_2",), 20, 80)]
    assert monotone_groups(groups, increasing=False) == [(("bin_0", "bin_1", "bin_2"), 35, 265)]
    spec = BinSpec("x", "numeric", edges=(0.0, 1.0, 2.0, 3.0))
    table = woe_table(spec, {"bin_0": (10, 90), "bin_1": (5, 95), "bin_2": (20, 80)}, monotone=True)
    assert [g.bins for g in table.groups] == [("bin_0", "bin_1"), ("bin_2",)]


def test_discrimination_gives_ks_gini_and_direction() -> None:
    assert discrimination({1.0: (0, 2), 2.0: (2, 0)}) == {"ks": 1.0, "gini": 1.0, "direction": 1.0}
    assert discrimination({1.0: (2, 0), 2.0: (0, 2)}) == {"ks": 1.0, "gini": 1.0, "direction": -1.0}
    assert discrimination({1.0: (0, 2)}) is None
    ordered = rate_groups({"a": (10, 30), "b": (30, 10), "c": (0, 0)})
    assert ordered == {0.25: (10, 30), 0.75: (30, 10)}
    assert discrimination(ordered) == {"ks": 0.5, "gini": 0.5, "direction": 1.0}


def test_spearman_is_the_pearson_correlation_of_mid_ranks() -> None:
    assert mid_rank_map({1.0: 1, 2.0: 2, 5.0: 1}) == {1.0: 1.0, 2.0: 2.5, 5.0: 4.0}
    pairs = [(1.0, 2.0), (2.0, 1.0), (3.0, 4.0), (4.0, 3.0)]
    assert rank_moments(pairs).correlation() == pytest.approx(0.6)
    tied = [(1.0, 1.0), (1.0, 2.0), (2.0, 2.0), (3.0, 5.0), (3.0, 4.0)]
    xs, ys = mid_ranks([x for x, _ in tied]), mid_ranks([y for _, y in tied])
    expected = Moments.of(zip(xs, ys, strict=True)).correlation()
    assert rank_moments(tied).correlation() == pytest.approx(expected)


def test_pearson_is_undefined_for_a_constant_series() -> None:
    assert Moments.of([(1.0, 2.0), (2.0, 4.0), (3.0, 6.0)]).correlation() == pytest.approx(1.0)
    assert Moments.of([(1.0, 2.0), (1.0, 4.0)]).correlation() is None
    assert Moments.of([(1.0, 2.0)]).correlation() is None


def test_eta_measures_a_categorical_against_a_continuous_target() -> None:
    assert eta({"a": (2, 2.0, 2.0), "b": (2, 6.0, 18.0)}) == pytest.approx(1.0)
    assert eta({"a": (2, 4.0, 10.0), "b": (2, 4.0, 10.0)}) == pytest.approx(0.0)
    assert eta({"a": (1, 1.0, 1.0)}) is None


def test_vif_is_the_diagonal_of_the_inverse_correlation_matrix() -> None:
    assert vif([[1.0, 0.6], [0.6, 1.0]]) == pytest.approx([1 / (1 - 0.36)] * 2, rel=1e-6)
    assert vif([[1.0]]) == [1.0]
    collinear = correlation_matrix([[1.0, 2.0, 3.0, 4.0], [0.0, 1.0, 0.0, 1.0], [1.0, 3.0, 3.0, 5.0]])
    assert min(vif(collinear)) > 1e6
    assert correlation_matrix([[1.0, 1.0, 1.0], [1.0, 2.0, 3.0]]) == [[1.0, 0.0], [0.0, 1.0]]


def test_chi_square_quantiles_and_the_psi_critical_value() -> None:
    assert gamma_p(1.0, 2.0) == pytest.approx(1 - math.exp(-2.0))
    assert chi2_ppf(0.95, 1) == pytest.approx(3.841458820694124, rel=1e-9)
    assert chi2_ppf(0.95, 9) == pytest.approx(16.918977604620448, rel=1e-9)
    assert chi2_ppf(0.99, 40) == pytest.approx(63.690739751564465, rel=1e-9)
    assert psi_critical(10, 1000, 1000) == pytest.approx(16.918977604620448 * 0.002, rel=1e-9)
    assert psi_critical(1, 1000, 1000) is None
