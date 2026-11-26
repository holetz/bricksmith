"""The Spark stability engine equals the pure engine on the schema, scan and shape metrics (local pyspark).

Skipped where pyspark is missing or cannot start a local session (Databricks Connect refuses one); the local-Spark
gate (``scripts/local_spark_check.py``) runs it with the skip turned into a failure. The key and distinct scan
metrics use ``approx_count_distinct`` (a relative standard deviation of about 5%), so they are compared within a
tolerance and every other metric exactly.
"""

from __future__ import annotations

import math
from typing import Any

from bricksmith.monitor.stability.config import StabilityConfig, parse_config
from bricksmith.monitor.stability.engine import CheckResult, MemoryStore, check
from bricksmith.monitor.stability.tables import merge_rows

NOW = "2026-06-01T00:00:00+00:00"
MONTHS = 4
APPROXIMATE = {"duplicate_key_rate", "distinct_count", "distinct_change"}
TOLERANCE = 0.12  # a few times the 5% relative standard deviation of approx_count_distinct


def _rows(*, drop: tuple[str, ...] = (), extra: bool = False, shift: bool = False) -> list[dict[str, Any]]:
    """Return ``MONTHS`` months; the last drifts when ``shift`` (new categories, shifted values, fewer customers)."""
    out: list[dict[str, Any]] = []
    for m in range(MONTHS):
        late = m == MONTHS - 1
        for i in range(120):
            row: dict[str, Any] = {
                "month": f"2026-{m + 1:02d}",
                "id": i if not (late and shift and i % 10 == 0) else i - 1,
                "customer": f"c{i % (4 if late and shift else 30)}",
                "amount": (i % 12) + i / 200 + (8 if late and shift else 0),
                "kind": ("a", "b", "c")[i % 3] if not (late and shift) else ("a", "z")[i % 2],
            }
            if extra and late:
                row["added"] = i % 5
            out.append({k: v for k, v in row.items() if k not in drop})
    return out


def _config(**declared: Any) -> StabilityConfig:  # noqa: ANN401
    return parse_config(
        {
            "period": "month",
            "columns": ["amount", "kind"],
            "keys": ["id"],
            "distinct": ["customer"],
            "baseline": "previous",
            "min_rows": 0,
            "min_rows_per_bin": 0,
            **declared,
        }
    )


def _commit(store: MemoryStore, result: CheckResult) -> None:
    for kind, rows in result.tables.items():
        store.tables[kind] = list(merge_rows(kind, store.tables.get(kind, []), rows))


def _same(a: Any, b: Any) -> bool:  # noqa: ANN401
    if isinstance(a, float) and isinstance(b, float):
        return (math.isnan(a) and math.isnan(b)) or abs(a - b) < 1e-9
    return bool(a == b)


def _frame(spark: Any, rows: list[dict[str, Any]]) -> Any:  # noqa: ANN401
    columns = list(dict.fromkeys(c for r in rows for c in r))
    return spark.createDataFrame([tuple(r.get(c) for c in columns) for r in rows], columns)


def _run(
    spark: Any,  # noqa: ANN401
    first: list[dict[str, Any]],
    second: list[dict[str, Any]],
    **declared: Any,  # noqa: ANN401
) -> tuple[dict[tuple[str, str, str, str], dict[str, Any]], dict[tuple[str, str, str, str], dict[str, Any]], Any]:
    """Run ``first`` then ``second`` on each engine and return both engines' second-run results (keyed)."""
    config = _config(**declared)
    out = []
    for engine in ("pure", "spark"):
        store = MemoryStore()
        data = (lambda r: r) if engine == "pure" else (lambda r: _frame(spark, r))
        _commit(store, check(data(first), config, store, dataset="d", run_id="r1", now=NOW))
        result = check(data(second), config, store, dataset="d", run_id="r2", now=NOW)
        keyed = {(r["period"], r["segment"], r["column"], r["metric"]): r for r in result.tables["results"]}
        out.append((keyed, result))
    return out[0][0], out[1][0], out[1][1]


def _assert_equal(pure: dict[Any, dict[str, Any]], spark: dict[Any, dict[str, Any]]) -> None:
    assert sorted(spark) == sorted(pure)
    for key, expected in pure.items():
        got = spark[key]
        if key[3] in APPROXIMATE and None not in (got["value"], expected["value"]):
            low, high = sorted((float(got["value"]), float(expected["value"])))
            assert high - low <= TOLERANCE * max(1.0, abs(high)), (key, got["value"], expected["value"])
            continue
        assert got["status"] == expected["status"], key
        assert got.get("reason") == expected.get("reason"), key
        if expected["value"] is None or got["value"] is None:
            assert got["value"] is expected["value"], key
        else:
            assert _same(float(got["value"]), float(expected["value"])), (key, got["value"], expected["value"])


def test_shape_scan_and_schema_metrics_match_on_a_drifted_month(local_spark: Any) -> None:  # noqa: ANN401
    pure, spark, _ = _run(local_spark, _rows(), _rows(shift=True))
    metrics = {m for _, _, _, m in pure}
    assert {
        "vanished_rate",
        "js_distance",
        "p50_shift",
        "wasserstein",
        "duplicate_key_rate",
        "distinct_change",
    } <= metrics
    assert "schema_change" in metrics
    _assert_equal(pure, spark)


def test_a_quiet_month_matches(local_spark: Any) -> None:  # noqa: ANN401
    pure, spark, _ = _run(local_spark, _rows(), _rows())
    _assert_equal(pure, spark)


def test_missing_column_and_schema_change_match_when_a_column_is_dropped(local_spark: Any) -> None:  # noqa: ANN401
    pure, spark, result = _run(local_spark, _rows(), _rows(drop=("amount",)))
    assert pure["2026-04", "", "amount", "missing_column"]["value"] == 1.0
    assert pure["2026-04", "", "", "schema_change"]["value"] == 1.0
    _assert_equal(pure, spark)
    assert not [r for r in result.tables["profiles"] if r["column"] == "amount" and r["period"] == "2026-04"]


def test_an_added_column_matches_when_frozen_and_when_extended(local_spark: Any) -> None:  # noqa: ANN401
    for declared in ({}, {"columns": "auto", "auto_columns": "extend"}):
        pure, spark, _ = _run(local_spark, _rows(), _rows(extra=True), **declared)
        assert pure["2026-04", "", "", "schema_change"]["value"] == 1.0
        _assert_equal(pure, spark)
