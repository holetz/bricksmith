"""Offline tests for the optional Feature Engineering adapter, against a fake client module."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

from bricksmith.mlops.featurestore import FeatureStoreError, Lookup, parse_feature_table, uc


@dataclass
class FakeLookup:
    table_name: str
    feature_names: list[str]
    lookup_key: list[str]
    timestamp_lookup_key: str | None = None
    rename_outputs: dict[str, str] = field(default_factory=dict)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create_training_set(self, **kwargs: Any) -> str:  # noqa: ANN401 - mirrors the client
        self.calls.append(kwargs)
        return "training-set"


class FakeFrame:
    def __init__(self, columns: list[str]) -> None:
        self.columns = columns

    def withColumn(self, name: str, value: object) -> FakeFrame:  # noqa: N802 - mirrors pyspark
        assert value is not None
        return FakeFrame([*self.columns, name])


class FakeColumn:
    def cast(self, kind: str) -> FakeColumn:
        assert kind == "date"
        return self


class FakeSpark:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def sql(self, statement: str) -> None:
        self.statements.append(statement)


def definition(**overrides: Any) -> Any:  # noqa: ANN401
    base: dict[str, Any] = {
        "entity_keys": ["account_id"],
        "closing_key": "closing_key",
        "features": {
            "balance": {"description": "Balance", "type": "double", "owner": "team-a"},
            "segment": {"description": "Segment", "type": "string", "owner": "team-a"},
        },
    }
    base.update(overrides)
    return parse_feature_table(base)


@pytest.fixture
def client_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = types.ModuleType(uc.CLIENT_MODULE)
    module.FeatureLookup = FakeLookup  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, uc.CLIENT_MODULE, module)
    return module


def test_missing_extra_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, uc.CLIENT_MODULE, None)  # type: ignore[arg-type]
    with pytest.raises(FeatureStoreError, match="'features' extra"):
        uc.feature_lookups([Lookup("fs.t")], {"fs.t": definition()}, mode="exact")


def test_mode_is_explicit_and_exact_needs_a_string_closing_key() -> None:
    with pytest.raises(FeatureStoreError, match="mode must be one of"):
        uc.primary_key_statements("fs.t", definition(), mode="asof")
    with pytest.raises(FeatureStoreError, match="closing_key"):
        uc.primary_key_statements("fs.t", definition(closing_key=None), mode="exact")


def test_primary_key_statements_per_mode() -> None:
    exact = uc.primary_key_statements("main.fs.balances", definition(), mode="exact")
    assert exact == [
        "ALTER TABLE `main`.`fs`.`balances` ALTER COLUMN `account_id` SET NOT NULL",
        "ALTER TABLE `main`.`fs`.`balances` ALTER COLUMN `closing_key` SET NOT NULL",
        "ALTER TABLE `main`.`fs`.`balances` ADD CONSTRAINT `balances_pk` PRIMARY KEY (`account_id`, `closing_key`)",
    ]
    series = uc.primary_key_statements("main.fs.balances", definition(closing_key=None), mode="timeseries")
    assert series[-1].endswith("PRIMARY KEY (`account_id`, `closing_date` TIMESERIES)")
    spark = FakeSpark()
    assert uc.publish_primary_key(spark, "main.fs.balances", definition(), mode="exact") == spark.statements == exact  # type: ignore[arg-type]


def test_feature_lookups_per_mode(client_module: types.ModuleType) -> None:
    assert client_module.FeatureLookup is FakeLookup
    definitions = {"fs.t": definition()}
    lookups = [Lookup("fs.t"), Lookup("fs.t", features=("balance",), keys=("acct",), offset=1, prefix="prev_")]
    exact = uc.feature_lookups(lookups, definitions, mode="exact")
    assert exact == [
        FakeLookup("fs.t", ["balance", "segment"], ["account_id", "__fs0_lookup"]),
        FakeLookup("fs.t", ["balance"], ["acct", "__fs1_lookup"], rename_outputs={"balance": "prev_balance"}),
    ]
    series = uc.feature_lookups(lookups[:1], definitions, mode="timeseries")
    assert series == [FakeLookup("fs.t", ["balance", "segment"], ["account_id"], "__fs0_lookup")]


def test_create_training_set_adds_and_excludes_helper_columns(
    client_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client_module is sys.modules[uc.CLIENT_MODULE]
    functions = types.ModuleType("pyspark.sql.functions")
    functions.col = lambda name: FakeColumn()  # type: ignore[attr-defined]  # noqa: ARG005
    sql = types.ModuleType("pyspark.sql")
    sql.functions = functions  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyspark", types.ModuleType("pyspark"))
    monkeypatch.setitem(sys.modules, "pyspark.sql", sql)
    monkeypatch.setitem(sys.modules, "pyspark.sql.functions", functions)
    monkeypatch.setattr(uc, "_lookup_value", lambda *args: "wanted")  # noqa: ARG005
    client = FakeClient()
    spine = FakeFrame(["account_id", "closing_date", "label"])
    lookups = [Lookup("fs.t"), Lookup("fs.t", features=("balance",), offset=1, prefix="prev_")]
    result = uc.create_training_set(client, spine, lookups, {"fs.t": definition()}, mode="exact", label="label")  # type: ignore[arg-type]
    assert result == "training-set"
    call = client.calls[0]
    assert call["df"].columns == ["account_id", "closing_date", "label", "__fs0_lookup", "__fs1_lookup"]
    assert call["exclude_columns"] == ["__fs0_lookup", "__fs1_lookup"]
    assert call["label"] == "label"
    assert [lookup.lookup_key for lookup in call["feature_lookups"]] == [
        ["account_id", "__fs0_lookup"],
        ["account_id", "__fs1_lookup"],
    ]
    with pytest.raises(FeatureStoreError, match="no observation closing"):
        uc.create_training_set(client, FakeFrame(["account_id"]), lookups, {"fs.t": definition()}, mode="exact")  # type: ignore[arg-type]
    with pytest.raises(FeatureStoreError, match="produced twice"):
        uc.create_training_set(
            client,
            FakeFrame(["account_id", "closing_date", "segment"]),
            lookups[:1],
            {"fs.t": definition()},
            mode="exact",
        )  # type: ignore[arg-type]
