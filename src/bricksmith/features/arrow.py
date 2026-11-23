"""The Arrow engine: each entity's history goes to Python as an Arrow table and runs the numpy reference.

``groupBy(keys).applyInArrow`` where the runtime has it (pyspark 4.0 and later), else ``applyInPandas``. The
per-entity function (:func:`group_function`) turns the group into numpy blocks and calls the same code as the
rows engine, so its values equal the reference by construction; the benchmark measures its cost. The function
ships to the workers by value (``bricksmith`` is registered with Spark's cloudpickle), so the cluster needs
numpy and pyarrow but not bricksmith itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.features._numpy import np
from bricksmith.features.catalog import INTEGER
from bricksmith.features.engine import Frame, compute_frame
from bricksmith.features.spec import DAY, MONTH, QUARTER, WEEK

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    import pyarrow as pa  # pyright: ignore[reportMissingTypeStubs]
    from numpy.typing import NDArray
    from pyspark.sql import DataFrame  # pyright: ignore[reportMissingImports]

    from bricksmith.features.spec import FeatureSpec

APPLY_IN_ARROW = "applyInArrow"
APPLY_IN_PANDAS = "applyInPandas"


def period_indexes(days: NDArray[Any], period: str) -> NDArray[np.int64]:
    """Return the period index of ``datetime64[D]`` closings (the same numbers as ``history.period_index``)."""
    if period in (MONTH, QUARTER):
        months = days.astype("datetime64[M]").astype(np.int64)
        return months if period == MONTH else months // 3
    count = days.astype("datetime64[D]").astype(np.int64)
    if period == WEEK:
        return (count - 4) // 7  # 1970-01-05, a Monday, is day 4
    if period == DAY:
        return count
    msg = f"unknown period {period!r}"
    raise ValueError(msg)


def output_schema(df: DataFrame, spec: FeatureSpec) -> str:
    """Return the DDL schema of the engine's output: keys, the closing as a date, then every output."""
    types = {field.name: field.dataType.simpleString() for field in df.schema.fields}
    parts = [f"`{k}` {types[k]}" for k in spec.keys]
    parts.append(f"`{spec.closing}` date")
    parts += [f"`{o.name}` {'bigint' if o.dtype == INTEGER else 'double'}" for o in spec.outputs]
    return ", ".join(parts)


def group_function(spec: FeatureSpec, cutoff: date | None = None) -> Callable[[pa.Table], pa.Table]:
    """Return the function that computes every output of ``spec`` for one entity's Arrow table."""

    def compute(table: pa.Table) -> pa.Table:
        import pyarrow as pa  # noqa: PLC0415 - runs on the workers  # pyright: ignore[reportMissingTypeStubs]
        import pyarrow.compute as pc  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]

        closings = table.column(spec.closing).cast(pa.date32())
        days = closings.to_numpy(zero_copy_only=False).astype("datetime64[D]")
        frame = Frame(
            entities=np.zeros(table.num_rows, dtype=np.int64),
            periods=period_indexes(days, spec.period),
            values={
                c: pc.cast(table.column(c), pa.float64()).to_numpy(zero_copy_only=False).astype(np.float64)
                for c in spec.value_columns
            },
            categories={
                c: np.array(table.column(c).cast(pa.string()).to_pylist(), dtype=object)
                for c in spec.categorical_columns
            },
        )
        if cutoff is None:
            targets = np.arange(table.num_rows, dtype=np.int64)
        else:
            targets = np.flatnonzero(days == np.datetime64(cutoff, "D")).astype(np.int64)
        outputs = compute_frame(spec, frame, targets) if targets.size else {o.name: np.array([]) for o in spec.outputs}
        columns: dict[str, Any] = {k: table.column(k).take(pa.array(targets)) for k in spec.keys}
        columns[spec.closing] = closings.take(pa.array(targets))
        for output in spec.outputs:
            values = np.asarray(outputs[output.name], dtype=np.float64)
            missing = np.isnan(values)
            if output.dtype == INTEGER:
                columns[output.name] = pa.array(np.where(missing, 0, values).astype(np.int64), mask=missing)
            else:
                columns[output.name] = pa.array(values, type=pa.float64(), mask=missing)
        return pa.table(columns)

    return compute


def pandas_function(spec: FeatureSpec, cutoff: date | None = None) -> Callable[[Any], Any]:
    """Return the ``applyInPandas`` variant of :func:`group_function`."""
    compute = group_function(spec, cutoff)

    def apply(frame: Any) -> Any:  # noqa: ANN401 - pandas frames
        import pyarrow as pa  # noqa: PLC0415 - runs on the workers  # pyright: ignore[reportMissingTypeStubs]

        return compute(pa.Table.from_pandas(frame, preserve_index=False)).to_pandas()

    return apply


def ship_by_value() -> None:
    """Pickle bricksmith by value in Spark UDFs, so workers need not have it installed."""
    from pyspark import cloudpickle  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    import bricksmith  # noqa: PLC0415

    cloudpickle.register_pickle_by_value(bricksmith)


def arrow_path(df: DataFrame) -> str:
    """Return which grouped-map API the runtime offers: ``applyInArrow`` or ``applyInPandas``."""
    grouped = df.groupBy(df.columns[0])
    return APPLY_IN_ARROW if hasattr(grouped, APPLY_IN_ARROW) else APPLY_IN_PANDAS


def compute_arrow(df: DataFrame, spec: FeatureSpec, as_of: date | str | None = None) -> DataFrame:
    """Return one row per entity and closing of ``df`` (or only ``as_of``) with every output of ``spec``."""
    from bricksmith.features.spark import refuse_future  # noqa: PLC0415 - pyspark-side helper

    cutoff = refuse_future(df, spec, as_of)
    ship_by_value()
    columns = [*spec.keys, spec.closing, *spec.value_columns, *spec.categorical_columns]
    source = df.select(*columns)
    schema = output_schema(source, spec)
    grouped = source.groupBy(*spec.keys)
    if arrow_path(source) == APPLY_IN_ARROW:
        return grouped.applyInArrow(group_function(spec, cutoff), schema)
    return grouped.applyInPandas(pandas_function(spec, cutoff), schema)
