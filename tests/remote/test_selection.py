"""Feature selection on the selected workspace: the Spark engine's ledger equals the pure engine's."""

from __future__ import annotations

import math
from datetime import date
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.selection import select_features

if TYPE_CHECKING:
    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

DDL = (
    "closing DATE, y INT, amount DOUBLE, copy DOUBLE, count_n BIGINT, noise INT, sparse DOUBLE, constant INT, "
    "drifting DOUBLE, kind STRING, flag BOOLEAN, target_c DOUBLE"
)
NAMES = [c.split()[0] for c in DDL.split(", ")]
CLOSINGS = [date(2026, m, 28) for m in range(1, 9)]


def synthetic() -> list[tuple[Any, ...]]:
    rows = []
    for m, closing in enumerate(CLOSINGS):
        late = m >= 6  # the last two closings are out of time
        for i in range(150):
            y = i % 2
            base = (i // 2) % 10
            amount = None if i % 37 == 0 else float(base + 5 * y) + (math.nan if i % 53 == 0 else 0.0)
            rows.append((
                closing, y, amount, None if amount is None else 2 * amount + 1, (i * 7) % 23 + 3 * y, base,
                None if i % 25 else 1.5, 1, float(base + 3 * y + (50 if late else 0)),
                "a" if (y == 1 and i % 10 != 1) or (y == 0 and i % 10 == 0) else "b", i % 3 == 0,
                float(i % 20) + 0.5 * y,
            ))  # fmt: skip
    return rows


def as_dicts(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    return [dict(zip(NAMES, r, strict=True)) for r in rows]


def same(a: object, b: object) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    return a == b


def assert_same_result(pure: Any, remote: Any) -> None:  # noqa: ANN401
    assert remote.selected == pure.selected
    assert remote.variable_rows() == pure.variable_rows()
    assert (remote.development_rows, remote.out_of_time_rows) == (pure.development_rows, pure.out_of_time_rows)
    left, right = pure.ledger_rows(), remote.ledger_rows()
    assert len(left) == len(right)
    for a, b in zip(left, right, strict=True):
        assert all(same(a[k], b[k]) for k in a), (a, b)


def test_spark_engine_equals_the_pure_engine(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str
) -> None:
    spark = remote_clients.spark()
    table = f"{remote_context.catalog}.{test_schema}.selection_input"
    spark.createDataFrame(synthetic(), DDL).write.saveAsTable(table)
    frame = spark.table(table)
    rows = as_dicts(synthetic())
    config: dict[str, Any] = {
        "closing": "closing",
        "target": "y",
        "development": ["2026-01-01", "2026-06-30"],
        "out_of_time": ["2026-07-01", "2026-08-31"],
        "exclude": ["target_c"],
        "categorical": ["noise"],
        "expected_signs": {"amount": "+"},
        "univariate": {"binning": "monotone"},
        "stability": {"psi_max": "critical"},
    }
    pure = select_features(rows, config)
    remote = select_features(frame, config)
    assert_same_result(pure, remote)
    assert {"amount", "kind"} <= set(remote.selected)
    assert {v.variable: v.left_at for v in remote.variables}["drifting"] == "stability"

    continuous = config | {"target": "target_c", "exclude": ["y"], "expected_signs": {}, "univariate": {}}
    assert_same_result(select_features(rows, continuous), select_features(frame, continuous))
