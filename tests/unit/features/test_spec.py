"""Feature spec: naming, validation, the catalog, period lags, point-in-time refusal and a light import."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, date, datetime

import pytest

from bricksmith.features import (
    CATALOG,
    FAMILIES,
    CategoricalCount,
    FeatureError,
    FeatureSpec,
    PointInTimeError,
    Ratio,
    WindowFeature,
    aggregate,
    parse_spec,
)
from bricksmith.features.catalog import DISTRIBUTION, INTEGER
from bricksmith.features.history import check_point_in_time, period_index, to_date
from bricksmith.features.spec import CATEGORY, RATIO, WINDOW, category_slug


def spec(**extra: object) -> FeatureSpec:
    base: dict[str, object] = {
        "keys": ("entity",),
        "closing": "closing",
        "features": (WindowFeature(("balance", "limit"), (3, 6), ("mean", "count", "p50")),),
    }
    base.update(extra)
    return FeatureSpec(**base)  # pyright: ignore[reportArgumentType]


def test_outputs_are_named_and_typed() -> None:
    s = spec(ratios=(Ratio("use_3p", "balance_mean_3p", "limit_mean_3p"),))
    names = [o.name for o in s.outputs]
    assert names[:3] == ["balance_mean_3p", "balance_count_3p", "balance_p50_3p"]
    assert "limit_p50_6p" in names
    assert names[-1] == "use_3p"
    kinds = {o.name: (o.kind, o.dtype) for o in s.outputs}
    assert kinds["balance_count_6p"] == (WINDOW, INTEGER)
    assert kinds["balance_mean_6p"] == (WINDOW, "double")
    assert kinds["use_3p"] == (RATIO, "double")
    assert s.max_window == 6
    assert s.value_columns == ("balance", "limit")


def test_categorical_counts_are_named_from_slugs() -> None:
    s = spec(categorical=(CategoricalCount("status", ("Paid", "Late 30+"), (3,)),))
    cat = [o for o in s.outputs if o.kind == CATEGORY]
    assert [o.name for o in cat] == ["status_count_paid_3p", "status_count_late_30_3p"]
    assert cat[1].category == "Late 30+"
    assert category_slug("Credit Card") == "credit_card"
    with pytest.raises(FeatureError, match="no letters"):
        category_slug("++")


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"keys": ()}, "entity key"),
        ({"period": "year"}, "period"),
        ({"features": (), "categorical": ()}, "at least one"),
        ({"ratios": (Ratio("r", "balance_mean_3p", "nope"),)}, "unknown outputs"),
        ({"ratios": (Ratio("balance_mean_3p", "balance", "limit"),)}, "declared twice"),
        ({"keys": ("balance",)}, "cannot be feature columns"),
        ({"categorical": (CategoricalCount("balance", ("a",), (3,)),)}, "both numeric and categorical"),
        ({"categorical": (CategoricalCount("status", ("a b", "a-b"), (3,)),)}, "declared twice"),
    ],
)
def test_invalid_specs_are_refused(extra: dict[str, object], message: str) -> None:
    with pytest.raises(FeatureError, match=message):
        spec(**extra)


def test_repeated_categories_are_refused() -> None:
    with pytest.raises(FeatureError, match="distinct categories"):
        CategoricalCount("status", ("a", "a"), (3,))


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ((("Balance",), (3,), ("mean",)), "snake case"),
        ((("balance",), (0,), ("mean",)), "window"),
        ((("balance",), (3, 3), ("mean",)), "repeats"),
        ((("balance",), (3,), ("median",)), "unknown aggregate"),
        ((("balance",), (3,), ("p100",)), "unknown aggregate"),
        (((), (3,), ("mean",)), "at least one column"),
    ],
)
def test_invalid_window_features_are_refused(args: tuple[object, ...], message: str) -> None:
    with pytest.raises(FeatureError, match=message):
        WindowFeature(*args)  # pyright: ignore[reportArgumentType]


def test_catalog_covers_every_family_and_percentiles() -> None:
    assert {a.family for a in CATALOG.values()} == set(FAMILIES)
    p90 = aggregate("p90")
    assert (p90.family, p90.min_points) == (DISTRIBUTION, 1)
    assert aggregate("tercile_diff").min_window == 3
    assert aggregate("kurtosis").min_points == 4
    assert all(a.description for a in CATALOG.values())


def test_parse_spec_builds_the_same_spec() -> None:
    parsed = parse_spec(
        {
            "keys": ["entity"],
            "closing": "closing",
            "features": [{"columns": ["balance", "limit"], "windows": [3, 6], "aggregates": ["mean", "count", "p50"]}],
            "ratios": [{"name": "use_3p", "numerator": "balance_mean_3p", "denominator": "limit_mean_3p"}],
            "categorical": [{"column": "status", "categories": ["paid"], "windows": [3]}],
            "period": "quarter",
        }
    )
    assert parsed.period == "quarter"
    assert [o.name for o in parsed.outputs][-2:] == ["status_count_paid_3p", "use_3p"]
    with pytest.raises(FeatureError, match="unknown feature spec keys"):
        parse_spec({"keys": ["e"], "closing": "c", "windows": [3]})
    with pytest.raises(FeatureError, match="takes exactly"):
        parse_spec({"keys": ["e"], "closing": "c", "features": [{"columns": ["x"], "windows": [3]}]})
    with pytest.raises(FeatureError, match="must be a list"):
        parse_spec({"keys": "e", "closing": "c", "features": []})
    with pytest.raises(FeatureError, match="needs keys and closing"):
        parse_spec({"features": []})


def test_period_lags() -> None:
    def lag(a: date, b: date, period: str) -> int:
        return period_index(b, period) - period_index(a, period)

    assert lag(date(2025, 11, 30), date(2026, 2, 28), "month") == 3
    assert lag(date(2025, 12, 31), date(2026, 1, 1), "quarter") == 1
    assert lag(date(2026, 1, 1), date(2026, 3, 31), "quarter") == 0
    assert lag(date(2026, 1, 4), date(2026, 1, 5), "week") == 1  # Sunday -> Monday
    assert lag(date(2026, 1, 5), date(2026, 1, 11), "week") == 0
    assert lag(date(1969, 12, 31), date(1970, 1, 2), "day") == 2
    assert to_date("2026-01-31") == to_date(datetime(2026, 1, 31, 10, tzinfo=UTC)) == date(2026, 1, 31)
    with pytest.raises(FeatureError, match="closing must be a date"):
        to_date("31/01/2026")


def test_rows_after_the_closing_are_refused() -> None:
    closings = [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]
    check_point_in_time(closings, None)
    check_point_in_time(closings, date(2026, 3, 31))
    with pytest.raises(PointInTimeError, match="2026-03-31") as caught:
        check_point_in_time(closings, date(2026, 2, 28))
    assert caught.value.code == "point_in_time"


def test_import_stays_light() -> None:
    code = (
        "import sys, bricksmith.features\n"
        "heavy = {'numpy', 'pyarrow', 'pandas', 'pyspark'} & {m.split('.')[0] for m in sys.modules}\n"
        "assert not heavy, heavy\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
