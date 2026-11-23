"""Offline tests for the closing-based feature store: declaration, calendar, lookups and the row engine."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

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
from bricksmith.mlops.featurestore.config import FeatureTable, plan_columns, require_closing
from bricksmith.mlops.featurestore.rows import build_training_set, check_rows, stamp_availability

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


# --- row engine ------------------------------------------------------------------------------------------


def balances() -> FeatureTable:
    return parse_feature_table(declaration())


def source_rows() -> list[dict[str, Any]]:
    """Rows as computed at the closing they describe (before availability stamping)."""
    rows = []
    for account in (1, 2):
        for month, described in enumerate(("2024-01-31", "2024-02-29", "2024-03-31")):
            if account == 2 and month == 0:
                continue
            value = float(account * 100 + month)
            rows.append({
                "account_id": account,
                "described": described,
                "balance_avg_3": value,
                "segment": "ab"[account - 1],
                "balance_hist": [value, value - 1.0],
            })  # fmt: skip
    return rows


def test_stamp_availability_moves_rows_to_the_closing_they_became_known() -> None:
    stamped = stamp_availability(source_rows(), balances(), described="described")
    assert [r["closing_date"] for r in stamped[:3]] == [date(2024, 2, 29), date(2024, 3, 31), date(2024, 4, 30)]
    assert all("described" not in r for r in stamped)
    lagless = parse_feature_table(declaration(availability_lag=0))
    assert stamp_availability([{"closing_date": "2024-01-31"}], lagless)[0]["closing_date"] == date(2024, 1, 31)
    with pytest.raises(FeatureStoreError, match="no column"):
        stamp_availability([{"x": 1}], lagless)
    with pytest.raises(FeatureStoreError, match="not a monthly closing"):
        stamp_availability([{"closing_date": "2024-01-15"}], lagless)


def test_check_rows_refuses_broken_primary_keys_and_types() -> None:
    table = balances()
    rows = stamp_availability(source_rows(), table, described="described")
    assert len(check_rows(rows, table)) == len(rows)
    with pytest.raises(FeatureStoreError, match="appear more than once"):
        check_rows([*rows, rows[0]], table)
    with pytest.raises(FeatureStoreError, match="null"):
        check_rows([{**rows[0], "account_id": None}], table)
    with pytest.raises(FeatureStoreError, match="miss declared columns: segment"):
        check_rows([{k: v for k, v in rows[0].items() if k != "segment"}], table)
    with pytest.raises(FeatureStoreError, match="not a monthly closing"):
        check_rows([{**rows[0], "closing_date": date(2024, 2, 1)}], table)
    for column, value in (
        ("balance_avg_3", "x"),
        ("balance_avg_3", True),
        ("segment", 1),
        ("balance_hist", [1.0, "a"]),
    ):
        with pytest.raises(FeatureStoreError, match=f"feature {column}"):
            check_rows([{**rows[0], column: value}], table)
    nulls = check_rows([{**rows[0], "balance_avg_3": None, "balance_hist": None}], table)
    assert nulls[0]["balance_avg_3"] is None


def test_map_features_are_type_checked() -> None:
    table = parse_feature_table(
        declaration(features={"m": {"description": "d", "type": "map<string,double>", "owner": "o", "order": "k"}})
    )
    row = {"account_id": 1, "closing_date": "2024-01-31"}
    assert check_rows([{**row, "m": {"a": 1.0}}], table)[0]["m"] == {"a": 1.0}
    with pytest.raises(FeatureStoreError, match="feature m"):
        check_rows([{**row, "m": {"a": "x"}}], table)
    with pytest.raises(FeatureStoreError, match="feature m"):
        check_rows([{**row, "m": [1.0]}], table)


def training_inputs() -> tuple[list[dict[str, Any]], dict[str, FeatureTable], dict[str, list[dict[str, Any]]]]:
    table = balances()
    stored = stamp_availability(source_rows(), table, described="described")
    spine = [
        {"account_id": 1, "closing_date": "2024-03-31", "label": 1},
        {"account_id": 2, "closing_date": date(2024, 3, 31), "label": 0},
        {"account_id": 3, "closing_date": "2024-03-31", "label": 0},
        {"account_id": 2, "closing_date": "2024-02-29", "label": None},
    ]
    return spine, {"fs.balances": table}, {"fs.balances": stored}


def test_training_set_joins_by_exact_closing_with_offsets_and_defaults() -> None:
    spine, definitions, data = training_inputs()
    lookups = [Lookup("fs.balances"), Lookup("fs.balances", features=("balance_avg_3",), offset=1, prefix="prev_")]
    built = build_training_set(spine, lookups, definitions, data)
    by_key = {(r["account_id"], r["closing_date"]): r for r in built.data}
    first = by_key[1, date(2024, 3, 31)]
    # Known at March: the February closing (lag 1); one period back: the January closing.
    assert first["balance_avg_3"] == 101.0 and first["prev_balance_avg_3"] == 100.0
    assert first["balance_hist"] == [101.0, 100.0] and first["label"] == 1
    second = by_key[2, date(2024, 3, 31)]
    assert second["balance_avg_3"] == 201.0 and second["prev_balance_avg_3"] is None
    missing = by_key[3, date(2024, 3, 31)]
    assert missing["segment"] == "unknown" and missing["balance_avg_3"] is None and missing["balance_hist"] is None
    early = by_key[2, date(2024, 2, 29)]
    assert early["balance_avg_3"] is None and early["segment"] == "unknown"
    assert list(first) == ["account_id", "closing_date", "label", "balance_avg_3", "segment", "balance_hist",
                           "prev_balance_avg_3"]  # fmt: skip
    assert set(built.reads) == {"fs.balances"} and len(str(built.reads["fs.balances"])) == 64


def test_training_and_scoring_differ_only_by_the_spine() -> None:
    spine, definitions, data = training_inputs()
    lookups = [Lookup("fs.balances")]
    training = build_training_set(spine, lookups, definitions, data).data
    scoring = build_training_set(
        [{k: v for k, v in r.items() if k != "label"} for r in spine], lookups, definitions, data
    )
    assert [{k: v for k, v in r.items() if k != "label"} for r in training] == scoring.data


def test_reads_fingerprint_is_order_independent_and_data_sensitive() -> None:
    spine, definitions, data = training_inputs()
    lookups = [Lookup("fs.balances")]
    one = build_training_set(spine, lookups, definitions, data).reads
    shuffled = build_training_set(spine, lookups, definitions, {"fs.balances": list(reversed(data["fs.balances"]))})
    assert one == shuffled.reads
    changed = [dict(r) for r in data["fs.balances"]]
    changed[0]["balance_avg_3"] = 0.5
    assert build_training_set(spine, lookups, definitions, {"fs.balances": changed}).reads != one


def test_training_set_guards() -> None:
    spine, definitions, data = training_inputs()
    lookups = [Lookup("fs.balances")]
    with pytest.raises(FeatureStoreError, match="appear more than once"):
        build_training_set(spine, lookups, definitions, {"fs.balances": [*data["fs.balances"], data["fs.balances"][0]]})
    with pytest.raises(FeatureStoreError, match="observation closing 2024-03-15"):
        build_training_set([{"account_id": 1, "closing_date": "2024-03-15"}], lookups, definitions, data)
    with pytest.raises(FeatureStoreError, match="no observation closing"):
        build_training_set([{"account_id": 1}], lookups, definitions, data)
    with pytest.raises(FeatureStoreError, match="no rows or no feature table definition"):
        build_training_set(spine, lookups, definitions, {})
    with pytest.raises(FeatureStoreError, match="produced twice"):
        build_training_set([{**spine[0], "segment": "x"}], lookups, definitions, data)
    assert build_training_set([], lookups, definitions, data).data == []
    with pytest.raises(FeatureStoreError, match="no feature table definition"):
        build_training_set([], lookups, {}, data)
