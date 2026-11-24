"""The JVM kernel equals the numpy reference on a local Spark session, and ``auto`` records what it ran."""

from __future__ import annotations

import json
import math
import random
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from bricksmith.features import CATALOG, FeatureSpec, WindowFeature, compute_rows, kernel
from bricksmith.features.engines import AUTO, JVM, NATIVE, compute_features
from bricksmith.features.kernel import SESSION, compute_jvm, probe
from bricksmith.features.record import collecting

GOLDEN: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "features" / "golden.json").read_text(encoding="utf-8")
)
TOLERANCE = 1e-9
SCHEMA = "entity bigint, closing date, x double, y double"
SPEC = FeatureSpec(
    keys=("entity",),
    closing="closing",
    features=(WindowFeature(("x", "y"), (1, 3, 6, 12), (*CATALOG, "p10", "p50", "p95")),),
)
AS_OF = date(2025, 12, 28)


def month(index: int) -> date:
    return date(2024 + index // 12, index % 12 + 1, 28)


def synthetic() -> list[dict[str, Any]]:
    rnd = random.Random(91)  # noqa: S311 - seeded synthetic data
    rows = []
    for entity in range(25):
        for index in range(24):
            if rnd.random() < 0.15:
                continue
            x = None if rnd.random() < 0.2 else round(rnd.uniform(-5, 10), 2)
            rows.append({"entity": entity, "closing": month(index), "x": x, "y": float(rnd.choice([0, 1, 1, 2, 3]))})
    return rows


def same(actual: object, expected: object) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    if isinstance(expected, float) or isinstance(actual, float):
        return math.isclose(float(actual), float(expected), rel_tol=TOLERANCE, abs_tol=TOLERANCE)  # pyright: ignore[reportArgumentType]
    return actual == expected


def mismatches(frame: Any, want: list[dict[str, Any]]) -> list[tuple[Any, ...]]:  # noqa: ANN401 - a pyspark frame
    got = {(r["entity"], r["closing"]): r.asDict() for r in frame.collect()}
    assert set(got) == {(w["entity"], w["closing"]) for w in want}
    return [
        (w["entity"], w["closing"], name, value, got[(w["entity"], w["closing"])][name])
        for w in want
        for name, value in w.items()
        if not same(got[(w["entity"], w["closing"])][name], value)
    ]


def test_the_kernel_registers_on_the_session(spark: Any) -> None:  # noqa: ANN401
    status = probe(spark, refresh=True)
    assert status.available, status.reason
    assert status.path == SESSION


def test_every_closing_matches_the_reference(spark: Any) -> None:  # noqa: ANN401
    rows = synthetic()
    function = probe(spark).function
    assert (
        mismatches(compute_jvm(spark.createDataFrame(rows, SCHEMA), SPEC, function=function), compute_rows(rows, SPEC))
        == []
    )


def test_one_closing_matches_the_reference(spark: Any) -> None:  # noqa: ANN401
    rows = [r for r in synthetic() if r["closing"] <= AS_OF]
    frame = spark.createDataFrame(rows, SCHEMA)
    want = compute_rows(rows, SPEC, as_of=AS_OF)
    assert {w["closing"] for w in want} == {AS_OF}
    assert mismatches(compute_jvm(frame, SPEC, AS_OF, probe(spark).function), want) == []


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda case: case["name"])
def test_the_golden_vectors_hold_on_the_kernel(spark: Any, case: dict[str, Any]) -> None:  # noqa: ANN401
    values = case["chronological"]
    rows = [
        {"entity": 1, "closing": month(i), "x": None if v is None else float(v), "y": 0.0} for i, v in enumerate(values)
    ]
    spec = FeatureSpec(("entity",), "closing", (WindowFeature(("x",), (len(values),), tuple(case["expected"])),))
    frame = spark.createDataFrame(rows, SCHEMA)
    [row] = compute_jvm(frame, spec, month(len(values) - 1), probe(spark).function).collect()
    tolerance = GOLDEN["tolerance"]
    for name, expected in case["expected"].items():
        actual = row[f"x_{name}_{len(values)}p"]
        if expected is None or actual is None:
            assert actual is expected, (name, actual)
        else:
            assert math.isclose(actual, expected, rel_tol=tolerance["relative"], abs_tol=tolerance["absolute"]), (
                name,
                actual,
            )


def test_auto_records_the_kernel(spark: Any) -> None:  # noqa: ANN401
    rows = synthetic()
    with collecting() as choices:
        frame = compute_features(spark.createDataFrame(rows, SCHEMA), SPEC)
    assert [(c.requested, c.engine, c.path, c.reason) for c in choices] == [(AUTO, JVM, SESSION, "")]
    assert mismatches(frame, compute_rows(rows, SPEC)) == []


def test_auto_without_the_jar_records_the_native_fallback(spark: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
    monkeypatch.setattr(kernel, "packaged_jar", lambda: None)
    monkeypatch.setattr(kernel, "_PROBED", {})
    rows = synthetic()
    other = spark.newSession()
    with collecting() as choices:
        frame = compute_features(other.createDataFrame(rows, SCHEMA), SPEC)
    [choice] = choices
    assert (choice.requested, choice.engine) == (AUTO, NATIVE)
    assert choice.reason.startswith("session: KernelUnavailableError: the kernel JAR is not packaged")
    assert mismatches(frame, compute_rows(rows, SPEC)) == []
