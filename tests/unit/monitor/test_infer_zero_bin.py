"""The optional exact-zero bin keeps older references byte-identical; type inference explains every skip."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from bricksmith.monitor import MonitorError, Reference, fit_reference
from bricksmith.monitor.bins import ABOVE, BELOW, NULL, ZERO, BinSpec, fit_bins
from bricksmith.monitor.infer import InferenceOptions, infer_columns, python_type


def test_default_serialization_is_unchanged() -> None:
    spec = fit_bins("x", dict.fromkeys(range(1, 9), 1), {"type": "numeric", "bins": 4})
    assert "zero" not in spec.to_dict() and not spec.zero
    rows = [{"x": float(v)} for v in range(1, 9)]
    reference = fit_reference(rows, {"x": {"bins": 4}}, name="r", version=1)
    assert "zero" not in reference.to_json()
    assert Reference.from_json(reference.to_json()).fingerprint == reference.fingerprint


def test_zero_bin_takes_exact_zeros_and_edges_ignore_them() -> None:
    counts = {0: 6, 1: 1, 2: 1, 3: 1, 4: 1, None: 2}
    spec = fit_bins("amount", counts, {"type": "numeric", "bins": 2, "zero": "separate"})
    # Non-zero values 1..4, B = 2: cut rank ceil(1 * 4 / 2) = 2 -> x = 2; edges [1, 2, 4].
    assert spec.edges == (1.0, 2.0, 4.0) and spec.zero
    assert spec.bin_ids() == (BELOW, ZERO, "bin_0", "bin_1", ABOVE, NULL)
    assert spec.counts == {BELOW: 0, ZERO: 6, "bin_0": 2, "bin_1": 2, ABOVE: 0, NULL: 2}
    assert [spec.assign(v) for v in (0, 0.0, -0.0, 0.5, 5, None)] == [ZERO, ZERO, ZERO, BELOW, ABOVE, NULL]
    assert spec.to_dict()["zero"] is True and BinSpec.from_dict(spec.to_dict()) == spec
    clipped = fit_bins("a", {0: 1, 5: 1, 9: 1}, {"edges": [1, 10], "zero": True, "outliers": "clip"})
    assert clipped.assign(0) == ZERO and clipped.assign(-3) == "bin_0"


def test_all_zero_column_and_bad_option() -> None:
    spec = fit_bins("z", {0: 5, None: 1}, {"type": "numeric", "bins": 3, "zero": "separate"})
    assert spec.edges == (0.0, 0.0) and spec.counts[ZERO] == 5 and spec.counts[NULL] == 1
    with pytest.raises(MonitorError, match="zero must be"):
        fit_bins("z", {1: 1}, {"type": "numeric", "bins": 2, "zero": "merge"})


def test_inference_by_type_and_cardinality() -> None:
    types = {
        "amount": "decimal(18,2)", "days": "int", "status": "string", "flag": "boolean", "id": "string",
        "ref_date": "date", "loaded_at": "timestamp", "tags": "array<string>", "attrs": "map<string,string>",
        "one": "string", "region": "string", "blob": "binary",
    }  # fmt: skip
    distinct = {"amount": 5000, "days": 12, "status": 4, "flag": 2, "id": 90000, "one": 1, "region": 3}
    result = infer_columns(types, distinct, exclude=["region"])
    assert result.columns == {
        "amount": {"type": "numeric", "bins": 10, "zero": "separate"},
        "days": {"type": "categorical", "max_categories": 20},
        "flag": {"type": "categorical", "max_categories": 20},
        "status": {"type": "categorical", "max_categories": 20},
    }
    assert result.skipped == {
        "attrs": "unsupported_type", "blob": "unsupported_type", "id": "high_cardinality",
        "loaded_at": "unsupported_type", "one": "constant", "ref_date": "unsupported_type",
        "region": "excluded", "tags": "unsupported_type",
    }  # fmt: skip
    plain = infer_columns({"x": "double"}, {"x": 50}, options=InferenceOptions(numeric_bins=4, zero=False))
    assert plain.columns == {"x": {"type": "numeric", "bins": 4}}


def test_python_type_names() -> None:
    assert python_type([None, 1]) == "bigint" and python_type([True]) == "boolean"
    assert python_type([float("nan"), 2.5]) == "double" and python_type([Decimal("1.5")]) == "double"
    assert python_type(["a"]) == "string" and python_type([date(2026, 1, 1)]) == "date"
    assert python_type([None]) == "void" and python_type([[1]]) == "struct"
