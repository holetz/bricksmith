"""Arrow engine per-entity function and engine dispatch, offline (no Spark)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pyarrow as pa
import pytest

from bricksmith.features import CATALOG, CategoricalCount, FeatureError, FeatureSpec, Ratio, WindowFeature, compute_rows
from bricksmith.features.arrow import group_function, output_schema, pandas_function, period_indexes
from bricksmith.features.catalog import FAMILIES
from bricksmith.features.engines import ARROW, DEFAULT_ENGINES, ENGINES, NATIVE, compute_features, route, subset_spec
from bricksmith.features.history import period_index

SPEC = FeatureSpec(
    keys=("entity",),
    closing="closing",
    features=(WindowFeature(("x",), (1, 3, 6), (*CATALOG, "p20")),),
    ratios=(Ratio("ratio", "x_sum_3p", "x"),),
    categorical=(CategoricalCount("status", ("a",), (3,)),),
)

ROWS: list[dict[str, object]] = [
    {"entity": 7, "closing": date(2025, m, 28), "x": x, "status": s}
    for m, x, s in [(1, 3.0, "a"), (2, None, "b"), (4, 5.0, "a"), (5, 5.0, None), (6, -1.0, "a"), (7, 2.5, "a")]
]


def table(rows: list[dict[str, object]]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=pa.schema([
        ("entity", pa.int64()), ("closing", pa.date32()), ("x", pa.float64()), ("status", pa.string()),
    ]))  # fmt: skip


def same(a: object, b: object) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
    return a == b


def test_group_function_equals_the_rows_engine() -> None:
    result = group_function(SPEC)(table(ROWS)).to_pylist()
    expected = compute_rows(ROWS, SPEC)
    assert len(result) == len(expected)
    for got, want in zip(result, expected, strict=True):
        assert list(got) == list(want)
        assert all(same(got[k], want[k]) for k in want), [k for k in want if not same(got[k], want[k])]


def test_group_function_types_and_cutoff() -> None:
    out = group_function(SPEC, date(2025, 6, 28))(table(ROWS))
    assert out.num_rows == 1
    assert out.schema.field("x_count_6p").type == pa.int64()
    assert out.schema.field("x_mean_6p").type == pa.float64()
    assert out.column("closing").to_pylist() == [date(2025, 6, 28)]
    empty = group_function(SPEC, date(2025, 3, 28))(table(ROWS))
    assert empty.num_rows == 0
    assert empty.column_names == out.column_names


def test_pandas_variant() -> None:
    frame = table(ROWS).to_pandas()
    result = pandas_function(SPEC)(frame)
    assert list(result["x_count_3p"]) == [r["x_count_3p"] for r in compute_rows(ROWS, SPEC)]


def test_period_indexes_match_the_pure_ones() -> None:
    days = [date(1969, 12, 28), date(1970, 1, 4), date(1970, 1, 5), date(2026, 3, 31), date(2026, 4, 1)]
    values = np.array(days, dtype="datetime64[D]")
    for period in ("month", "quarter", "week", "day"):
        assert period_indexes(values, period).tolist() == [period_index(d, period) for d in days], period
    with pytest.raises(ValueError, match="unknown period"):
        period_indexes(values, "year")


def test_output_schema() -> None:
    @dataclass
    class Type:
        name: str

        def simpleString(self) -> str:  # noqa: N802 - the pyspark method name
            return self.name

    @dataclass
    class Field:
        name: str
        dataType: Type  # noqa: N815 - the pyspark attribute name

    @dataclass
    class Schema:
        fields: list[Field]

    @dataclass
    class Frame:
        schema: Schema

    spec = FeatureSpec(("entity",), "closing", (WindowFeature(("x",), (3,), ("count", "mean")),))
    frame = Frame(Schema([Field("entity", Type("bigint")), Field("closing", Type("string"))]))
    schema = output_schema(frame, spec)  # pyright: ignore[reportArgumentType]
    assert schema == "`entity` bigint, `closing` date, `x_count_3p` bigint, `x_mean_3p` double"


def test_routing_by_family() -> None:
    assert set(DEFAULT_ENGINES) == set(FAMILIES)
    assert set(DEFAULT_ENGINES.values()) <= set(ENGINES)
    assert set(DEFAULT_ENGINES.values()) == {NATIVE}  # the recorded benchmark's choice
    routed = route(SPEC, dict.fromkeys(FAMILIES, NATIVE) | {"trend": ARROW})
    assert routed[ARROW] == [
        f"x_{a}_{w}p" for w in (1, 3, 6) for a in ("slope", "intercept", "r2", "resid_std", "trend_distance")
    ]
    assert "status_count_a_3p" in routed[NATIVE]
    assert "ratio" not in routed[NATIVE] + routed[ARROW]
    with pytest.raises(FeatureError, match="unknown"):
        route(SPEC, {"trend": "gpu"})
    with pytest.raises(FeatureError, match="unknown"):
        route(SPEC, {"shapes": NATIVE})


def test_subset_spec_keeps_only_the_named_window_outputs() -> None:
    sub = subset_spec(SPEC, ["x_mean_3p", "x_slope_6p"])
    assert [o.name for o in sub.outputs] == ["x_mean_3p", "x_slope_6p"]
    assert (sub.keys, sub.closing, sub.period) == (SPEC.keys, SPEC.closing, SPEC.period)


def test_unknown_engine_is_refused_before_spark() -> None:
    with pytest.raises(FeatureError, match="engine must be one of"):
        compute_features(object(), SPEC, engine="gpu")  # pyright: ignore[reportArgumentType]
