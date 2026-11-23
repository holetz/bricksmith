"""Offline tests for the closing-based feature store: declaration, closing calendar and lookups."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from typing import Any

import pytest

from bricksmith.mlops.featurestore import (
    FeatureStoreError,
    Lookup,
    feature_table_of,
    is_closing,
    parse_feature_table,
    shift,
)
from bricksmith.mlops.featurestore.config import plan_columns, require_closing

HEAVY = ("pyspark", "databricks", "mlflow", "sklearn", "pandas", "numpy", "pyarrow")


def declaration(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401 - plain metadata values
    base: dict[str, Any] = {
        "entity_keys": ["account_id"],
        "frequency": "monthly",
        "availability_lag": 1,
        "features": {
            "balance_avg_3": {"description": "Mean balance over three closings", "type": "DOUBLE", "owner": "team-a"},
            "segment": {"description": "Account segment", "type": "string", "owner": "team-a", "default": "unknown"},
            "balance_hist": {
                "description": "Balances of the last three closings",
                "type": "array<double>",
                "owner": "team-a",
                "order": "index 0 is the most recent closing",
            },
        },
    }
    base.update(overrides)
    return base


def test_importing_the_package_loads_no_heavy_module() -> None:
    code = (
        "import sys, bricksmith.mlops.featurestore, bricksmith.mlops.featurestore.config\n"
        f"bad = [m for m in {HEAVY!r} if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def test_declaration_parses_with_defaults_and_normalized_types() -> None:
    table = parse_feature_table(declaration())
    assert table.entity_keys == ("account_id",)
    assert table.closing == "closing_date"
    assert table.primary_key == ("account_id", "closing_date")
    assert table.availability_lag == 1
    assert table.feature("balance_avg_3").type == "double"
    assert table.feature("segment").default == "unknown"
    assert table.feature("balance_hist").order.startswith("index 0")
    assert table.write_options() == {
        "write": "replace_partitions",
        "partition_by": ["closing_date"],
        "keys": ["account_id", "closing_date"],
    }
    assert feature_table_of({"feature_table": declaration()}) == table
    assert feature_table_of({"write": "overwrite"}) is None
    assert table.to_dict()["features"][0]["name"] == "balance_avg_3"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"entity_keys": []}, "entity_keys"),
        ({"entity_keys": ["a", "a"]}, "repeat"),
        ({"frequency": "hourly"}, "frequency"),
        ({"availability_lag": -1}, "availability_lag"),
        ({"availability_lag": True}, "availability_lag"),
        ({"features": {}}, "features"),
        ({"bogus": 1}, "unknown keys"),
        ({"features": {"x": {"description": "d", "type": "vector", "owner": "o"}}}, "vectors are never stored"),
        ({"features": {"x": {"description": "d", "type": "int", "owner": "o"}}}, "type"),
        ({"features": {"x": {"description": "", "type": "double", "owner": "o"}}}, "description"),
        ({"features": {"x": {"description": "d", "type": "double"}}}, "owner"),
        ({"features": {"x": {"description": "d", "type": "array<string>", "owner": "o"}}}, "order"),
        ({"features": {"x": {"description": "d", "type": "double", "owner": "o", "order": "?"}}}, "order applies"),
        ({"features": {"x": {"description": "d", "type": "double", "owner": "o", "default": "a"}}}, "default"),
        ({"features": {"x": {"description": "d", "type": "double", "owner": "o", "default": True}}}, "default"),
        (
            {
                "features": {
                    "x": {"description": "d", "type": "map<string,double>", "owner": "o", "order": "k", "default": {}}
                }
            },
            "only for double and string",
        ),
        ({"features": {"x": {"description": "d", "type": "double", "owner": "o", "extra": 1}}}, "unknown keys"),
        ({"features": {"account_id": {"description": "d", "type": "double", "owner": "o"}}}, "declared twice"),
    ],
)
def test_invalid_declarations_are_refused(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(FeatureStoreError, match=message):
        parse_feature_table(declaration(**overrides))


def test_monthly_calendar_uses_month_ends_including_leap_years() -> None:
    assert is_closing(date(2024, 2, 29), "monthly")
    assert not is_closing(date(2024, 2, 28), "monthly")
    assert shift(date(2024, 1, 31), 1, "monthly") == date(2024, 2, 29)
    assert shift(date(2024, 3, 31), -1, "monthly") == date(2024, 2, 29)
    assert shift(date(2023, 3, 31), -1, "monthly") == date(2023, 2, 28)
    assert shift(date(2025, 12, 31), 1, "monthly") == date(2026, 1, 31)
    assert shift(date(2026, 1, 31), -13, "monthly") == date(2024, 12, 31)


def test_other_frequencies() -> None:
    assert is_closing(date(2026, 3, 31), "quarterly")
    assert not is_closing(date(2026, 4, 30), "quarterly")
    assert shift(date(2026, 3, 31), 1, "quarterly") == date(2026, 6, 30)
    assert shift(date(2026, 6, 30), -1, "semiannual") == date(2025, 12, 31)
    assert not is_closing(date(2026, 3, 31), "semiannual")
    assert shift(date(2025, 12, 31), 2, "annual") == date(2027, 12, 31)
    assert not is_closing(date(2026, 6, 30), "annual")
    assert shift(date(2026, 1, 4), 2, "weekly") == date(2026, 1, 18)
    assert shift(date(2026, 3, 1), -1, "daily") == date(2026, 2, 28)


def test_unaligned_closings_are_refused_not_snapped() -> None:
    with pytest.raises(FeatureStoreError, match="not a monthly closing"):
        shift(date(2026, 1, 15), 1, "monthly")
    with pytest.raises(FeatureStoreError, match="not a quarterly closing"):
        require_closing(date(2026, 1, 31), "quarterly")


def test_lookup_refuses_future_offsets_and_bad_names() -> None:
    with pytest.raises(FeatureStoreError, match="after the observation"):
        Lookup("t", offset=-1)
    with pytest.raises(FeatureStoreError, match="whole number"):
        Lookup("t", offset=True)
    with pytest.raises(FeatureStoreError, match="prefix"):
        Lookup("t", prefix="1x")
    with pytest.raises(FeatureStoreError, match="lookup table"):
        Lookup("")


def test_lookup_resolution_and_column_plan() -> None:
    table = parse_feature_table(declaration())
    definitions = {"cat.sch.balances": table}
    lookup = Lookup("cat.sch.balances", features=("balance_avg_3",), keys=("acct",), offset=1, prefix="prev_")
    assert lookup.spine_keys(table) == ("acct",)
    assert lookup.output_names(table) == {"balance_avg_3": "prev_balance_avg_3"}
    columns = plan_columns(["acct", "closing_date"], [lookup, Lookup("cat.sch.balances", keys=("acct",))], definitions)
    assert columns[-3:] == ["balance_avg_3", "segment", "balance_hist"]
    with pytest.raises(FeatureStoreError, match="produced twice"):
        plan_columns(["acct", "closing_date"], [Lookup("cat.sch.balances", keys=("acct",))] * 2, definitions)
    with pytest.raises(FeatureStoreError, match="produced twice"):
        plan_columns(["acct", "closing_date", "segment"], [Lookup("cat.sch.balances", keys=("acct",))], definitions)
    with pytest.raises(FeatureStoreError, match="no column acct"):
        plan_columns(["account", "closing_date"], [lookup], definitions)
    with pytest.raises(FeatureStoreError, match="no feature table definition"):
        plan_columns(["acct"], [Lookup("other", keys=("acct",))], definitions)
    with pytest.raises(FeatureStoreError, match="must match entity keys"):
        Lookup("cat.sch.balances", keys=("a", "b")).spine_keys(table)
    with pytest.raises(FeatureStoreError, match="not declared"):
        Lookup("cat.sch.balances", features=("nope",)).selected(table)
