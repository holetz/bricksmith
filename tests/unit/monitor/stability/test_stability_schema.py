"""Schema drift: the declaration, the schema results and alerts, extend refits and missing columns."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

import json

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import Limits
from bricksmith.monitor.stability.config import (
    METRICS,
    SCHEMA_LIMITS,
    SCHEMA_METRICS,
    parse_config,
)
from bricksmith.monitor.stability.engine import CheckResult, MemoryStore, check
from bricksmith.monitor.stability.tables import BINNINGS, RUNS, SCHEMAS, merge_rows


def test_config_schema_defaults_leave_serialized_forms_unchanged() -> None:
    plain = parse_config({"period": "month"})
    extended = parse_config({"period": "month", "auto_columns": "extend"})
    assert plain.auto_columns == "freeze" and "auto_columns" not in plain.to_dict()
    assert extended.to_dict()["auto_columns"] == "extend"
    assert plain.binning_fingerprint() == extended.binning_fingerprint()
    for metric in SCHEMA_METRICS:
        assert metric in METRICS and metric not in plain.to_dict()["limits"]
        assert plain.limits_for(metric, "x") == SCHEMA_LIMITS[metric]


def test_config_schema_limits_are_configurable() -> None:
    custom = parse_config({"period": "month", "limits": {"schema_change": {"green": 1, "red": 4}}})
    assert custom.limits_for("schema_change", "") == Limits(1.0, 4.0, "higher")
    off = parse_config({"period": "month", "limits": {"missing_column": None}})
    assert off.limits_for("missing_column", "x") is None


def test_config_auto_columns_is_validated() -> None:
    for bad in ("grow", True, 1):
        with pytest.raises(MonitorError):
            parse_config({"period": "month", "auto_columns": bad})
    with pytest.raises(MonitorError, match="columns: auto"):
        parse_config({"period": "month", "columns": ["a"], "auto_columns": "extend"})
    assert parse_config({"period": "month", "columns": ["a"], "auto_columns": "freeze"}).auto_columns == "freeze"


def test_binnings_and_runs_tables_carry_the_schema() -> None:
    assert "schema" in SCHEMAS[BINNINGS] and "schema" in SCHEMAS[RUNS]


def _month(month: str, **extra: object) -> list[dict[str, object]]:
    return [{"month": month, "x": i % 10, "y": i % 4, **dict.fromkeys(extra, i % 3)} for i in range(60)]


def _rows(months: int, *, extra: tuple[str, ...] = (), drop: tuple[str, ...] = ()) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for m in range(months):
        late = m == months - 1
        out += [
            {k: v for k, v in row.items() if k not in drop}
            for row in _month(f"2026-{m + 1:02d}", **dict.fromkeys(extra if late else (), 1))
        ]
    return out


def _check(rows: list[dict[str, object]], store: MemoryStore, run_id: str, **declared: object) -> CheckResult:
    config = parse_config({"period": "month", "min_rows": 0, **declared})
    return check(rows, config, store, dataset="d", run_id=run_id, now="2026-06-01T00:00:00+00:00")


def _commit(store: MemoryStore, result: CheckResult) -> None:
    for kind, rows in result.tables.items():
        store.tables[kind] = merge_rows(kind, store.tables.get(kind, []), rows)


def _metric(result: CheckResult, metric: str, column: str = "") -> list[dict]:
    return [r for r in result.tables["results"] if r["metric"] == metric and r["column"] == column]


def test_engine_a_quiet_run_stores_the_schema_and_a_green_schema_change() -> None:
    store = MemoryStore()
    first = _check(_rows(3), store, "r1")
    _commit(store, first)
    second = _check(_rows(3), store, "r2")
    assert json.loads(first.tables["binnings"][0]["schema"]) == [
        ["month", "string", False],
        ["x", "bigint", False],
        ["y", "bigint", False],
    ]
    assert first.tables["runs"][0]["schema"] == first.tables["binnings"][0]["schema"]
    (change,) = _metric(second, "schema_change")
    assert change["value"] == 0.0 and change["status"] == "green" and change["period"] == "2026-03"
    assert not [a for a in second.tables["alerts"] if a["metric"] == "schema_change"]


def test_engine_an_added_column_alerts_and_stays_unmonitored_when_frozen() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(3), store, "r1"))
    result = _check(_rows(4, extra=("z",)), store, "r2")
    (change,) = _metric(result, "schema_change")
    assert change["value"] == 1.0 and change["status"] == "yellow"
    assert "added: z" in change["reason"] and "auto_columns: freeze" in change["reason"]
    (alert,) = [a for a in result.tables["alerts"] if a["metric"] == "schema_change"]
    assert alert["state"] == "open" and alert["severity"] == "warning" and "added: z" in alert["message"]
    assert not [r for r in result.tables["profiles"] if r["column"] == "z"] and not result.tables["binnings"]


def test_engine_the_frozen_drift_persists_against_the_fit_schema() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(3), store, "r1"))
    _commit(store, _check(_rows(4, extra=("z",)), store, "r2"))
    again = _check(_rows(4, extra=("z",)), store, "r3")
    (change,) = _metric(again, "schema_change")
    assert change["value"] == 1.0 and change["baseline_label"] == "fit schema"


def test_engine_extend_fits_a_new_version_that_profiles_the_new_column() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(3), store, "r1", auto_columns="extend"))
    result = _check(_rows(4, extra=("z",)), store, "r2", auto_columns="extend")
    (binning,) = result.tables["binnings"]
    assert binning["version"] == 2 and binning["fit_periods"] == "2026-04" and "z" in binning["body"]
    assert {r["column"] for r in result.tables["profiles"]} == {"x", "y", "z"}
    (change,) = _metric(result, "schema_change")
    assert "added: z" in change["reason"] and "refitted as version 2" in change["reason"]
    _commit(store, result)
    quiet = _check(_rows(4, extra=("z",)), store, "r3", auto_columns="extend")
    assert not quiet.tables["binnings"] and _metric(quiet, "schema_change")[0]["value"] == 0.0


def test_engine_a_retyped_column_is_reported() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(3), store, "r1"))
    rows = _rows(3)
    for row in rows:
        row["x"] = float(row["x"])  # type: ignore[arg-type]
    (change,) = _metric(_check(rows, store, "r2"), "schema_change")
    assert change["value"] == 1.0 and "retyped: x (bigint -> double)" in change["reason"]


def test_engine_excluded_columns_are_ignored_by_the_drift() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(3), store, "r1", exclude=["z"]))
    (change,) = _metric(_check(_rows(4, extra=("z",)), store, "r2", exclude=["z"]), "schema_change")
    assert change["value"] == 0.0


def test_engine_the_run_without_a_stored_fit_schema_compares_with_the_previous_run() -> None:
    store = MemoryStore()
    first = _check(_rows(3), store, "r1")
    first.tables["binnings"][0].pop("schema")
    _commit(store, first)
    _commit(store, _check(_rows(3), store, "r2"))
    (change,) = _metric(_check(_rows(4, extra=("z",)), store, "r3"), "schema_change")
    assert change["value"] == 1.0 and change["baseline_label"] == "schema of the previous run"


def test_engine_a_dropped_monitored_column_is_reported_not_raised() -> None:
    store = MemoryStore()
    declared = {"columns": ["x", "y"]}
    _commit(store, _check(_rows(3), store, "r1", **declared))
    result = _check(_rows(3, drop=("y",)), store, "r2", **declared)
    (missing,) = _metric(result, "missing_column", "y")
    assert missing["value"] == 1.0 and missing["status"] == "red" and missing["period"] == "2026-03"
    assert not [r for r in result.tables["results"] if r["column"] == "y" and r["metric"] != "missing_column"]
    assert not [r for r in result.tables["profiles"] if r["column"] == "y"]
    assert {r["column"] for r in _metric_rows(result, "psi")} == {"x"}
    (alert,) = [a for a in result.tables["alerts"] if a["metric"] == "missing_column"]
    assert alert["severity"] == "critical" and alert["state"] == "open" and alert["column"] == "y"
    (change,) = _metric(result, "schema_change")
    assert "dropped: y" in change["reason"]


def _metric_rows(result: CheckResult, metric: str) -> list[dict]:
    return [r for r in result.tables["results"] if r["metric"] == metric]


def test_engine_a_returned_column_closes_its_missing_column_alert() -> None:
    store = MemoryStore()
    declared = {"columns": ["x", "y"], "persistence": {"close_after": 1}}
    _commit(store, _check(_rows(3), store, "r1", **declared))
    _commit(store, _check(_rows(3, drop=("y",)), store, "r2", **declared))
    back = _check(_rows(3), store, "r3", **declared)
    (returned,) = _metric(back, "missing_column", "y")
    assert returned["value"] == 0.0 and returned["status"] == "green"
    (alert,) = [a for a in back.tables["alerts"] if a["metric"] == "missing_column"]
    assert alert["state"] == "closed"
    assert {r["column"] for r in back.tables["profiles"]} == {"x", "y"}


def test_engine_every_monitored_column_missing_raises() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(3), store, "r1", columns=["y"]))
    with pytest.raises(MonitorError, match="every monitored column is missing"):
        _check(_rows(3, drop=("y",)), store, "r2", columns=["y"])


def test_engine_schema_results_feed_the_gate() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(3), store, "r1"))
    result = _check(_rows(4, extra=("z",)), store, "r2", gate={"fail_on": ["yellow"]})
    assert result.outcome.failed and result.outcome.yellow >= 1


def test_spark_schema_reads_names_types_and_nullability() -> None:
    from types import SimpleNamespace  # noqa: PLC0415

    from bricksmith.monitor.stability.spark import schema_spark  # noqa: PLC0415

    def field(name: str, kind: str, *, nullable: bool) -> SimpleNamespace:
        return SimpleNamespace(name=name, dataType=SimpleNamespace(simpleString=lambda: kind), nullable=nullable)

    frame = SimpleNamespace(
        schema=SimpleNamespace(fields=[field("b", "int", nullable=False), field("a", "string", nullable=True)])
    )
    assert schema_spark(frame) == (("a", "string", True), ("b", "int", False))  # type: ignore[arg-type]
