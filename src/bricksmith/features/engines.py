"""Engine dispatch on Spark: the JVM kernel by default, native expressions as its recorded fallback, Arrow on request.

``DEFAULT_ENGINES`` maps every aggregate family to the engine ``engine="auto"`` routes it to: the JVM kernel
(decision 0019). When the kernel cannot be registered or run on the current compute, ``auto`` computes those outputs
with native Spark expressions and records why; ``engine="jvm"`` raises instead. Category counts always run native,
partial results are joined on keys and closing, and ratios and differences are computed last. Every call records
its choice (:mod:`bricksmith.features.record`).
"""

from __future__ import annotations

from dataclasses import replace
from functools import reduce
from typing import TYPE_CHECKING

from bricksmith.features.catalog import FAMILIES, aggregate
from bricksmith.features.errors import FeatureError
from bricksmith.features.kernel import JVM
from bricksmith.features.record import EngineChoice, record
from bricksmith.features.spec import CATEGORY, WINDOW, WindowFeature

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from pyspark.sql import DataFrame  # pyright: ignore[reportMissingImports]

    from bricksmith.features.spec import FeatureSpec

NATIVE = "spark"
ARROW = "arrow"
AUTO = "auto"
MIXED = "mixed"
ENGINES = (NATIVE, ARROW, JVM)

#: The engine each aggregate family runs on under ``engine="auto"``: the JVM kernel for every family, falling back
#: to native Spark where the kernel is unavailable (decision 0019). Arrow runs only when asked for.
DEFAULT_ENGINES: dict[str, str] = dict.fromkeys(FAMILIES, JVM)


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


def _requested(spec: FeatureSpec, engine: str, engines: Mapping[str, str] | None) -> dict[str, list[str]]:
    """Return the outputs per engine that ``engine`` asks for, before the kernel probe."""
    if engine == AUTO:
        return route(spec, engines)
    if engine in (ARROW, JVM):
        routed: dict[str, list[str]] = {name: [] for name in ENGINES}
        routed[engine] = [o.name for o in spec.window_outputs()]
        routed[NATIVE] = [o.name for o in spec.outputs if o.kind == CATEGORY]
        return routed
    if engine == NATIVE:
        return {NATIVE: [o.name for o in spec.outputs if o.kind in (WINDOW, CATEGORY)], ARROW: [], JVM: []}
    msg = f"engine must be one of {AUTO}, {', '.join(ENGINES)}, got {engine!r}"
    raise FeatureError(msg)


def _label(routed: Mapping[str, list[str]], spec: FeatureSpec) -> str:
    windows = {o.name for o in spec.window_outputs()}
    used = [engine for engine in ENGINES if windows & set(routed[engine])]
    return used[0] if len(used) == 1 else (MIXED if used else NATIVE)


def compute_features(  # noqa: PLR0913 - one keyword per documented engine option
    df: DataFrame,
    spec: FeatureSpec,
    engine: str = AUTO,
    as_of: date | str | None = None,
    engines: Mapping[str, str] | None = None,
    *,
    kernel_function: str | None = None,
) -> DataFrame:
    """Compute every output of ``spec`` on Spark with ``engine`` (``auto``, ``jvm``, ``spark`` or ``arrow``).

    ``engines`` overrides ``DEFAULT_ENGINES`` per family under ``auto``. ``kernel_function`` names an installed Unity
    Catalog kernel function, tried before the session registration. Category counts always run native.
    """
    from bricksmith.features.spark import compute_spark  # noqa: PLC0415 - loads pyspark

    routed = _requested(spec, engine, engines)
    path, reason, function = "", "", ""
    if routed[JVM]:
        from bricksmith.features.kernel import KernelUnavailableError, probe  # noqa: PLC0415

        status = probe(df.sparkSession, kernel_function)
        if status.available:
            path, function = status.path, status.function
        elif engine == JVM:
            msg = f"the JVM kernel is unavailable on this compute: {status.reason}"
            raise KernelUnavailableError(msg)
        else:
            reason = status.reason
            routed[NATIVE] = [*routed[NATIVE], *routed[JVM]]
            routed[JVM] = []
    record(EngineChoice(engine, _label(routed, spec), path, reason, len(spec.window_outputs())))
    if not routed[JVM] and not routed[ARROW]:
        return compute_spark(df, spec, as_of)
    parts: list[DataFrame] = []
    if routed[JVM]:
        from bricksmith.features.kernel import compute_jvm  # noqa: PLC0415

        parts.append(compute_jvm(df, subset_spec(spec, routed[JVM]), as_of, function))
    if routed[ARROW]:
        from bricksmith.features.arrow import compute_arrow  # noqa: PLC0415 - loads numpy and pyarrow

        parts.append(compute_arrow(df, subset_spec(spec, routed[ARROW]), as_of))
    if routed[NATIVE]:
        parts.append(compute_spark(df, spec, as_of, routed[NATIVE]))
    keys = [*spec.keys, spec.closing]
    result = reduce(lambda left, right: left.join(right, on=keys, how="inner"), parts)
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
