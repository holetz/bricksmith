"""Spark engine: the funnel's aggregates computed on the cluster, folded by the same pure code as the row engine.

Value counts return one small row per distinct value (as ``bricksmith.monitor.spark.fit_spark`` does); class
counts and moments are conditional sums; Spearman joins the mid ranks computed from the collected value counts
back onto the rows. Only ``sample`` brings rows to the driver, bounded by ``sample_rows``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.mlops.selection.config import BINARY, fail
from bricksmith.mlops.selection.metrics import Moments, mid_rank_map
from bricksmith.mlops.selection.summary import CATEGORICAL, NUMERIC, normal
from bricksmith.monitor.bins import category_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyspark.sql import Column, DataFrame  # pyright: ignore[reportMissingImports]

    from bricksmith.mlops.selection.config import ClosingRange, SelectionConfig

CLOSING = "_bricksmith_closing"
PAIRS_PER_AGGREGATE = 50
SAMPLE_MARGIN = 1.2


def _functions() -> Any:  # noqa: ANN401 - pyspark.sql.functions
    from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration, loaded where used  # pyright: ignore[reportMissingImports]

    return f


def _present(column: Column) -> Column:
    f = _functions()
    return column.isNotNull() & ~f.isnan(column)


class SparkEngine:
    """The funnel's engine over a Spark DataFrame."""

    def __init__(self, frame: DataFrame, config: SelectionConfig) -> None:
        """Split ``frame`` by closing (compared as text) into development and out-of-time."""
        f = _functions()
        self.config = config
        self.columns = tuple(frame.columns)
        if config.closing not in self.columns or config.target not in self.columns:
            fail(f"the input needs the closing column {config.closing!r} and the target {config.target!r}")
        self.frame = frame
        text = f.col(config.closing).cast("string")

        def within(window: ClosingRange) -> Column:
            return text.isNotNull() & (text >= f.lit(window.start)) & (text <= f.lit(window.end))

        in_dev = within(config.development)
        in_oot = within(config.out_of_time) if config.out_of_time else f.lit(value=False)
        self.dev = frame.filter(in_dev)
        self.oot = frame.filter(in_oot)
        self.both = frame.filter(in_dev | in_oot).withColumn(CLOSING, text)
        self.y = f.col(config.target).cast("double")
        usable = self.y.isin(0.0, 1.0) if config.target_kind == BINARY else _present(self.y)
        self.labelled = self.dev.filter(usable)
        self._sizes: tuple[int, int] | None = None

    def sizes(self) -> tuple[int, int]:
        """Return the development and out-of-time row counts."""
        if self._sizes is None:
            self._sizes = (self.dev.count(), self.oot.count())
        return self._sizes

    def kinds(self, variables: Sequence[str]) -> dict[str, str]:
        """Numeric for Spark numeric types (booleans are not numeric); declared ``categorical`` always categorical."""
        from pyspark.sql.types import NumericType  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

        types = {field.name: field.dataType for field in self.frame.schema.fields}
        return {
            v: NUMERIC if isinstance(types[v], NumericType) and v not in self.config.categorical else CATEGORICAL
            for v in variables
        }

    def closing_counts(self, variable: str) -> dict[str, dict[Any, int]]:
        """Return ``closing -> value -> rows`` over both samples."""
        counts: dict[str, dict[Any, int]] = {}
        for row in self.both.groupBy(CLOSING, variable).count().collect():
            slot = counts.setdefault(row[CLOSING], {})
            value = normal(row[variable])
            slot[value] = slot.get(value, 0) + int(row["count"])
        return counts

    def class_counts(self, variable: str) -> dict[Any, tuple[int, int]]:
        """Return ``value -> (events, non-events)`` over development rows with a binary target."""
        f = _functions()
        events = f.sum(f.when(self.y == 1.0, 1).otherwise(0)).alias("e")
        non_events = f.sum(f.when(self.y == 0.0, 1).otherwise(0)).alias("n")
        counts: dict[Any, tuple[int, int]] = {}
        for row in self.labelled.groupBy(variable).agg(events, non_events).collect():
            value = normal(row[variable])
            e, n = counts.get(value, (0, 0))
            counts[value] = (e + int(row["e"]), n + int(row["n"]))
        return counts

    def target_moments(self, variable: str) -> dict[Any, tuple[float, float, float]]:
        """Return ``category -> (rows, sum y, sum y^2)`` over development rows with a continuous target."""
        f = _functions()
        aggregates = (f.count(f.lit(1)).alias("c"), f.sum(self.y).alias("s"), f.sum(self.y * self.y).alias("q"))
        moments: dict[Any, tuple[float, float, float]] = {}
        for row in self.labelled.groupBy(variable).agg(*aggregates).collect():
            value = normal(row[variable])
            key = None if value is None else category_text(value)
            n, s, q = moments.get(key, (0.0, 0.0, 0.0))
            moments[key] = (n + float(row["c"]), s + float(row["s"]), q + float(row["q"]))
        return moments

    def rank_moments(self, variable: str) -> Moments:
        """Return the moments of ``(rank x, rank y)``: mid ranks from collected value counts, joined back."""
        f = _functions()
        base = self.labelled.select(f.col(variable).cast("double").alias("x"), self.y.alias("y"))
        base = base.filter(_present(f.col("x")))
        spark = self.frame.sparkSession
        ranks = {}
        for name in ("x", "y"):
            counts = {float(r[name]): int(r["count"]) for r in base.groupBy(name).count().collect()}
            table = list(mid_rank_map(counts).items())
            ranks[name] = spark.createDataFrame(table, f"{name} DOUBLE, r{name} DOUBLE")
        ranked = base.join(f.broadcast(ranks["x"]), "x").join(f.broadcast(ranks["y"]), "y")
        rx, ry = f.col("rx"), f.col("ry")
        row = ranked.agg(
            f.count(f.lit(1)).alias("n"),
            f.sum(rx).alias("sx"),
            f.sum(ry).alias("sy"),
            f.sum(rx * rx).alias("sxx"),
            f.sum(ry * ry).alias("syy"),
            f.sum(rx * ry).alias("sxy"),
        ).first()
        if row is None or not row["n"]:
            return Moments()
        return Moments(*(float(row[k]) for k in ("n", "sx", "sy", "sxx", "syy", "sxy")))

    def pair_moments(self, pairs: Sequence[tuple[str, str]]) -> dict[tuple[str, str], Moments]:
        """Return the Pearson moments of each numeric pair in a few aggregates over the development rows."""
        f = _functions()
        result: dict[tuple[str, str], Moments] = {}
        for start in range(0, len(pairs), PAIRS_PER_AGGREGATE):
            chunk = pairs[start : start + PAIRS_PER_AGGREGATE]
            expressions = []
            for i, (a, b) in enumerate(chunk):
                x, y = f.col(a).cast("double"), f.col(b).cast("double")
                both = _present(x) & _present(y)

                def summed(value: Column, name: str, both: Column = both, i: int = i) -> Column:
                    return f.sum(f.when(both, value).otherwise(f.lit(0.0))).alias(f"{name}{i}")

                expressions += [summed(f.lit(1.0), "n"), summed(x, "sx"), summed(y, "sy"),
                                summed(x * x, "sxx"), summed(y * y, "syy"), summed(x * y, "sxy")]  # fmt: skip
            row = self.dev.agg(*expressions).first()
            for i, pair in enumerate(chunk):
                values = (
                    float((row[f"{k}{i}"] if row else 0.0) or 0.0) for k in ("n", "sx", "sy", "sxx", "syy", "sxy")
                )
                result[pair] = Moments(*values)
        return result

    def sample(self, variables: Sequence[str], *, out_of_time: bool = False) -> list[dict[str, Any]]:
        """Return at most ``sample_rows`` rows: all of them below the bound, else a seeded Spark sample."""
        part = self.oot if out_of_time else self.dev
        names = [*variables, self.config.target]
        limit = self.config.sample_rows
        total = self.sizes()[1 if out_of_time else 0]
        if total > limit:
            fraction = min(1.0, SAMPLE_MARGIN * limit / total)
            part = part.sample(fraction=fraction, seed=self.config.seed).limit(limit)
        return [{c: normal(row[c]) for c in names} for row in part.select(*names).collect()]


__all__ = ["SparkEngine"]
