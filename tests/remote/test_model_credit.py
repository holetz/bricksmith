"""Credit-model performance checks on the selected workspace: Spark equals the pure engine for every credit metric."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.monitor.credit import rescale
from bricksmith.monitor.model import parse_config
from bricksmith.monitor.model.config import FAMILY_METRICS
from bricksmith.monitor.model.engine import MemoryStore, check

if TYPE_CHECKING:
    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

DDL = (
    "month STRING, sample STRING, score DOUBLE, grade STRING, bad INT, bad_3 INT, exclusion_first STRING, region STRING"
)
DEV = ("2025-01", "2025-02", "2025-03")
PROD = ("2025-04", "2025-05", "2025-06")
#: Grade -> (probability over the 12-period horizon, rows per month); grade D has too few expected events.
GRADES = {"A": (0.02, 300), "B": (0.10, 150), "C": (0.30, 80), "D": (0.001, 40)}
FACTORS = {"2025-01": 0.9, "2025-03": 1.2, "2025-05": 0.5, "2025-06": 1.6}
DECLARATION: dict[str, Any] = {
    "model": "pd",
    "period": "month",
    "score": "score",
    "label": "bad",
    "grade": "grade",
    "segment": "region",
    "sample": "sample",
    "references": {"development": {"sample": "dev"}},
    "model_version": "1",
    "min_rows": 10,
    "credit": {
        "horizon": 12,
        "grades": ["A", "B", "C", "D"],
        "correlation": 0.12,
        "early_warning": [{"label": "bad_3", "periods": 3, "lag": 1}],
        "exclusion": "exclusion_first",
    },
}
CREDIT_METRICS = {m for f in ("grade_calibration", "grade_order", "early_warning", "exclusion_mix", "bad_rate_chart")
                  for m in FAMILY_METRICS[f]}  # fmt: skip


def synthetic() -> list[tuple[Any, ...]]:
    """Return scored rows whose event rates move by month, with a June shift of the exclusion mix."""
    rows = []
    for month in DEV + PROD:
        factor = FACTORS.get(month, 1.0)
        for grade, (pd, count) in GRADES.items():
            events = round(count * min(pd * factor, 1.0)) if grade != "D" else count // 2
            early = round(count * rescale(pd, 3, 12))
            fraud = 5 if month == PROD[-1] else 1
            for i in range(count):
                reason = "fraud" if i % 20 < fraud else "age" if i % 20 == 19 else None
                sample = "dev" if month in DEV else "prod"
                rows.append((month, sample, pd, grade, int(i < events), int(i < early), reason, "ns"[i % 2]))
    return rows


def as_dicts(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    names = [c.split()[0] for c in DDL.split(", ")]
    return [dict(zip(names, r, strict=True)) for r in rows]


def same(a: object, b: object) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) < 1e-9 * max(1.0, abs(a))
    return a == b


def assert_rows_equal(left: list[dict[str, Any]], right: list[dict[str, Any]], keys: tuple[str, ...]) -> None:
    def index(rows: list[dict[str, Any]]) -> dict[tuple[Any, ...], dict[str, Any]]:
        return {tuple(r[k] for k in keys): r for r in rows}

    a, b = index(left), index(right)
    assert a.keys() == b.keys()
    for key, row in a.items():
        other = b[key]
        assert all(same(row[c], other[c]) for c in row if c not in ("computed_at", "finished_at")), (key, row, other)


def test_spark_equals_the_pure_engine_for_credit_metrics(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str
) -> None:
    spark = remote_clients.spark()
    table = f"{remote_context.catalog}.{test_schema}.credit_input"
    spark.createDataFrame(synthetic(), DDL).write.saveAsTable(table)
    config = parse_config(DECLARATION)
    pure = check(as_dicts(synthetic()), config, MemoryStore(), run_id="r", now="t")
    remote = check(spark.table(table), config, MemoryStore(), run_id="r", now="t")
    keys = ("monitoring_period", "segment", "column", "metric")
    assert_rows_equal(pure.tables["results"], remote.tables["results"], keys)
    assert_rows_equal(pure.tables["details"], remote.tables["details"], (*keys, "bin"))
    measured = {r["metric"] for r in remote.tables["results"] if r["value"] is not None}
    assert measured >= CREDIT_METRICS
    lights = {r["light"] for r in remote.tables["results"] if r["metric"] in CREDIT_METRICS}
    assert {"green", "red", "insufficient_volume"} <= lights
    assert remote.outcome.run_status == "success"
