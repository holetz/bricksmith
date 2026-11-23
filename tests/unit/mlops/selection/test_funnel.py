from __future__ import annotations

import subprocess
import sys
from datetime import date
from typing import Any

import pytest

from bricksmith.mlops.selection import SelectionError, apply_woe, select_features
from bricksmith.mlops.selection.funnel import DROP, INFO, KEEP, PASS

DEV = [date(2026, m, 28) for m in range(1, 7)]
OOT = [date(2026, m, 28) for m in (7, 8)]
HEAVY = ("pyspark", "databricks", "mlflow", "sklearn", "pandas", "numpy")


def synthetic() -> list[dict[str, Any]]:
    rows = []
    for closing in (date(2025, 12, 28), *DEV, *OOT):  # the first closing lies outside both samples
        late = closing in OOT
        for i in range(200):
            y = i % 2
            base = (i // 2) % 10
            rows.append({
                "id": f"{closing}-{i}",
                "closing": closing,
                "y": y,
                "strong": base + 5 * y,
                "copy": 2 * (base + 5 * y) + 1,
                "noise": base,
                "mostly_missing": None if i % 50 else 1.0,
                "constant": 1,
                "drifting": base + 3 * y + (100 if late else 0),
                "kind": "a" if (y == 1 and i % 10 != 1) or (y == 0 and i % 10 == 0) else "b",
                "wrong_sign": 20 - base - 5 * y + (i % 3),
                "kept_noise": (i // 2) % 7,
                "vetoed": ((i // 2) * 3) % 7 + 4 * y,
            })  # fmt: skip
    return rows


CONFIG: dict[str, Any] = {
    "name": "churn_selection",
    "closing": "closing",
    "target": "y",
    "development": [str(DEV[0]), str(DEV[-1])],
    "out_of_time": [str(OOT[0]), str(OOT[-1])],
    "exclude": ["id"],
    "expected_signs": {"wrong_sign": "+", "strong": "+"},
    "judgment": {"keep": {"kept_noise": "required by the business owner"}, "drop": {"vetoed": "not available"}},
}


def outcomes(result: Any) -> dict[str, tuple[bool, str]]:  # noqa: ANN401
    return {v.variable: (v.selected, v.left_at) for v in result.variables}


def entry(result: Any, variable: str, metric: str) -> Any:  # noqa: ANN401
    return next(e for e in result.ledger if e.variable == variable and e.metric == metric)


def test_the_funnel_drops_each_variable_at_the_step_that_explains_it() -> None:
    result = select_features(synthetic(), CONFIG)
    assert result.development_rows == 1200
    assert result.out_of_time_rows == 400
    assert outcomes(result) == {
        "strong": (True, ""),
        "copy": (False, "redundancy"),
        "noise": (False, "univariate"),
        "mostly_missing": (False, "quality"),
        "constant": (False, "quality"),
        "drifting": (False, "stability"),
        "kind": (True, ""),
        "wrong_sign": (False, "univariate"),
        "kept_noise": (True, ""),
        "vetoed": (False, "judgment"),
    }
    assert result.selected == ("strong", "kind", "kept_noise")
    assert entry(result, "mostly_missing", "missing_share").value == pytest.approx(1 - 4 / 200)
    assert entry(result, "constant", "constant_share").decision == DROP
    assert entry(result, "drifting", "psi_oot").decision == DROP
    assert entry(result, "strong", "psi_mom_max").decision == PASS
    assert entry(result, "noise", "iv").value == pytest.approx(0.0, abs=1e-12)
    assert entry(result, "wrong_sign", "direction").reason == "direction - disagrees with the expected +"
    assert entry(result, "strong", "direction").decision == PASS
    copy = entry(result, "copy", "max_correlation")
    assert copy.value == pytest.approx(1.0)
    assert copy.reason.startswith("correlated with strong")
    assert entry(result, "kind", "max_correlation").decision == INFO
    kept = [e for e in result.ledger if e.variable == "kept_noise" and e.step == "judgment"]
    assert [(e.decision, e.reason) for e in kept] == [(KEEP, "required by the business owner")]
    by_variable = {v.variable: v for v in result.variables}
    assert by_variable["kept_noise"].reason == "kept by judgment: required by the business owner"
    assert by_variable["noise"].reason == "iv 0 below the minimum 0.02"
    assert result.dropped_by_step()["quality"] == 2


def test_the_ledger_is_reproducible_and_plain() -> None:
    first = select_features(synthetic(), CONFIG)
    again = select_features(list(reversed(synthetic())), CONFIG)
    assert first.ledger_rows() == again.ledger_rows()
    row = first.ledger_rows()[0]
    assert set(row) == {"variable", "step", "step_order", "metric", "value", "threshold", "rule", "decision", "reason"}
    assert first.to_dict()["selected"] == ["strong", "kind", "kept_noise"]
    assert set(first.to_dict()["woe"]) >= {"strong", "kind"}


def test_woe_tables_encode_rows_without_changing_them() -> None:
    result = select_features(synthetic(), CONFIG)
    rows = [{"strong": 0, "kind": "a"}, {"strong": 14, "kind": "b"}, {"strong": None, "kind": "zz"}]
    encoded = apply_woe(rows, {k: result.woe[k] for k in ("strong", "kind")})
    assert rows[0] == {"strong": 0, "kind": "a"}
    assert encoded[0]["strong_woe"] > 0 > encoded[1]["strong_woe"]
    assert encoded[0]["kind_woe"] < 0 < encoded[1]["kind_woe"]
    assert encoded[2]["kind_woe"] == 0.0


def test_monotone_binning_and_critical_psi() -> None:
    config = CONFIG | {"univariate": {"binning": "monotone"}, "stability": {"psi_max": "critical"}}
    result = select_features(synthetic(), config)
    rates = [g.events / (g.events + g.non_events) for g in result.woe["strong"].groups]
    assert rates == sorted(rates)
    psi_entry = entry(result, "strong", "psi_oot")
    assert psi_entry.threshold is not None
    assert 0 < psi_entry.threshold < 0.1
    assert entry(result, "drifting", "psi_oot").decision == DROP


def test_vif_removes_one_of_an_exactly_collinear_set() -> None:
    rows = []
    for i in range(120):
        x1, x2 = float(i % 7), float((i * 3) % 11)
        rows.append({"closing": "2026-01-31", "y": i % 2, "x1": x1, "x2": x2, "x3": x1 + x2})
    config = {"closing": "closing", "target": "y", "development": ["2026-01-31", "2026-01-31"],
              "steps": {"quality": False, "stability": False, "univariate": False},
              "redundancy": {"max_correlation": 0.99}}  # fmt: skip
    result = select_features(rows, config)
    vif_drops = [e for e in result.ledger if e.metric == "vif" and e.decision == DROP]
    assert len(vif_drops) == 1
    assert len(result.selected) == 2
    assert all(e.value is not None and e.value < 10 for e in result.ledger if e.metric == "vif" and e.decision == PASS)


def test_a_continuous_target_uses_spearman_and_eta() -> None:
    rows = []
    for i in range(200):
        y = float(i % 20)
        rows.append({"closing": "2026-01-31", "y": y, "mono": y * y, "flat": float((i // 20) % 10),
                     "band": "hi" if y >= 10 else "lo"})  # fmt: skip
    config = {"closing": "closing", "target": "y", "target_kind": "continuous",
              "development": ["2026-01-31", "2026-01-31"], "expected_signs": {"mono": "+"}}  # fmt: skip
    result = select_features(rows, config)
    assert entry(result, "mono", "spearman").value == pytest.approx(1.0)
    assert entry(result, "flat", "spearman").value == pytest.approx(0.0, abs=1e-12)
    assert entry(result, "band", "eta").value > 0.8
    assert result.selected == ("mono", "band")


def test_inputs_and_names_are_checked() -> None:
    with pytest.raises(SelectionError, match="not candidates: ghost"):
        select_features(synthetic(), CONFIG | {"judgment": {"keep": {"ghost": "x"}}})
    with pytest.raises(SelectionError, match="needs the closing column"):
        select_features([{"a": 1}], CONFIG)
    with pytest.raises(SelectionError, match="input must be a list of mappings"):
        select_features("rows", CONFIG)


def test_import_loads_no_heavy_module() -> None:
    code = (
        "import sys, bricksmith.mlops.selection\n"
        f"bad = [m for m in {HEAVY!r} if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
