"""Engine dispatch on Spark: native expressions, the Arrow engine, or each aggregate family on its default engine.

``DEFAULT_ENGINES`` maps every aggregate family to the engine the recorded benchmark chose for it (see the
features guide). With ``engine="auto"``, window outputs are routed by family, category counts stay native, the
partial results are joined on keys and closing, and ratios and differences are computed last.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from bricksmith.features.catalog import FAMILIES, aggregate
from bricksmith.features.errors import FeatureError
from bricksmith.features.spec import CATEGORY, WINDOW, WindowFeature

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from pyspark.sql import DataFrame  # pyright: ignore[reportMissingImports]

    from bricksmith.features.spec import FeatureSpec

NATIVE = "spark"
ARROW = "arrow"
AUTO = "auto"
ENGINES = (NATIVE, ARROW)

#: The engine each aggregate family runs on under ``engine="auto"``.
DEFAULT_ENGINES: dict[str, str] = dict.fromkeys(FAMILIES, NATIVE)


def route(spec: FeatureSpec, engines: Mapping[str, str] | None = None) -> dict[str, list[str]]:
    """Return the window and category output names each engine computes (category counts are native)."""
    chosen = {**DEFAULT_ENGINES, **(engines or {})}
    unknown = {f for f in chosen if f not in FAMILIES} | {e for e in chosen.values() if e not in ENGINES}
    if unknown:
        msg = f"engines map families {list(FAMILIES)} to {list(ENGINES)}; unknown {sorted(unknown)}"
        raise FeatureError(msg)
    routed: dict[str, list[str]] = {engine: [] for engine in ENGINES}
    for output in spec.outputs:
        if output.kind == WINDOW:
            routed[chosen[aggregate(output.aggregate).family]].append(output.name)
        elif output.kind == CATEGORY:
            routed[NATIVE].append(output.name)
    return routed


def subset_spec(spec: FeatureSpec, names: list[str]) -> FeatureSpec:
    """Return a spec with only the window outputs ``names``, no category counts, ratios or differences."""
    wanted = set(names)
    features = tuple(
        WindowFeature((o.column,), (o.window,), (o.aggregate,)) for o in spec.window_outputs() if o.name in wanted
    )
    return replace(spec, features=features, ratios=(), differences=(), categorical=())


def compute_features(
    df: DataFrame,
    spec: FeatureSpec,
    engine: str = AUTO,
    as_of: date | str | None = None,
    engines: Mapping[str, str] | None = None,
) -> DataFrame:
    """Compute every output of ``spec`` on Spark with ``engine`` (``auto``, ``spark`` or ``arrow``).

    ``engines`` overrides ``DEFAULT_ENGINES`` per family under ``auto``. Category counts always run native.
    """
    from bricksmith.features.spark import compute_spark  # noqa: PLC0415 - loads pyspark

    if engine == ARROW:
        routed = {
            ARROW: [o.name for o in spec.window_outputs()],
            NATIVE: [o.name for o in spec.outputs if o.kind == CATEGORY],
        }
    elif engine == AUTO:
        routed = route(spec, engines)
    elif engine == NATIVE:
        routed = {ARROW: []}
    else:
        msg = f"engine must be one of {AUTO}, {', '.join(ENGINES)}, got {engine!r}"
        raise FeatureError(msg)
    if not routed[ARROW]:
        return compute_spark(df, spec, as_of)
    from bricksmith.features.arrow import compute_arrow  # noqa: PLC0415 - loads numpy and pyarrow

    keys = [*spec.keys, spec.closing]
    result = compute_arrow(df, subset_spec(spec, routed[ARROW]), as_of)
    if routed[NATIVE]:
        result = result.join(compute_spark(df, spec, as_of, routed[NATIVE]), on=keys, how="inner")
    return _apply_combined(result, df, spec)


def _apply_combined(result: DataFrame, df: DataFrame, spec: FeatureSpec) -> DataFrame:
    """Add the ratios and differences, reading source columns at the current closing, in spec order."""
    from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration  # pyright: ignore[reportMissingImports]

    from bricksmith.features.spark import quote, source_column, sql_plan  # noqa: PLC0415 - loads nothing heavy

    plan = sql_plan(spec)
    keys = [*spec.keys, spec.closing]
    if plan.sources:
        closing = f.expr(f"CAST({quote(spec.closing)} AS DATE)").alias(spec.closing)
        carried = [f.col(quote(c)).alias(source_column(c)) for c in plan.sources]
        sources = df.select(*(f.col(quote(k)) for k in spec.keys), closing, *carried)
        result = result.join(sources, on=keys, how="inner")
    for name, sql in plan.combined:
        result = result.withColumn(name, f.expr(sql))
    return result.select(*(f.col(quote(c)) for c in (*keys, *(o.name for o in spec.outputs))))
