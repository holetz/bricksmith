"""The shared golden vectors (tests/fixtures/features/golden.json) hold on the numpy reference.

The same file drives the Scala kernel's test suite, so both engines pin the same numbers.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from bricksmith.features import CATALOG
from bricksmith.features.reference import compute

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "features" / "golden.json"
DOCUMENT: dict[str, Any] = json.loads(GOLDEN.read_text(encoding="utf-8"))
RELATIVE = DOCUMENT["tolerance"]["relative"]
ABSOLUTE = DOCUMENT["tolerance"]["absolute"]
VECTORS = [
    (case["name"], case["chronological"], name, value)
    for case in DOCUMENT["cases"]
    for name, value in case["expected"].items()
]


def block(chronological: list[float | None]) -> Any:  # noqa: ANN401 - a numpy block
    import numpy as np  # noqa: PLC0415

    return np.array([[math.nan if v is None else float(v) for v in reversed(chronological)]], dtype=np.float64)


@pytest.mark.parametrize(("case", "chronological", "name", "expected"), VECTORS)
def test_reference_matches_the_golden_vector(
    case: str, chronological: list[float | None], name: str, expected: float | None
) -> None:
    value = float(compute(name, block(chronological))[0])
    actual = None if math.isnan(value) else value
    if expected is None or actual is None:
        assert actual is expected, (case, name, actual)
    else:
        assert math.isclose(actual, expected, rel_tol=RELATIVE, abs_tol=ABSOLUTE), (case, name, actual)


def test_every_catalog_aggregate_and_the_named_percentiles_are_pinned() -> None:
    pinned = {name for _, _, name, _ in VECTORS}
    assert set(CATALOG) - pinned == set()
    assert {"p10", "p50", "p95"} <= pinned


def test_the_migration_entities_are_present() -> None:
    names = {case["name"] for case in DOCUMENT["cases"]}
    assert {
        "migration_3_2_1",
        "migration_4_null_1",
        "migration_5_null_null",
        "migration_all_null",
        "migration_lag_index",
    } <= names
