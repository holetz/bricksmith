"""A reproducible benchmark of the feature engines: wall time per engine and aggregate family.

Synthetic long tables (``entities x closings x columns``, seeded, with nulls) feed each engine one family at a
time: the numpy reference over Python rows, and on Spark the JVM kernel, native and Arrow engines over a frame generated
remotely from the same size (Spark's own hash, so the two data sets are alike in shape, not identical). Spark
results are materialized with one ``max(hash(...))`` aggregate; one warm-up run per engine is excluded, and the time to
materialize the source alone is reported as the ``source`` row. :func:`render_report` writes a markdown table.

A run can report ``progress`` (one line per event), stop cleanly at a time :class:`Budget` (checked between
measurements, never inside one) and time one closing (``as_of``) instead of every closing.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from bricksmith.features.catalog import CATALOG, FAMILIES
from bricksmith.features.engines import ENGINES
from bricksmith.features.errors import FeatureError
from bricksmith.features.spec import FeatureSpec, WindowFeature

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

REFERENCE = "reference"
SPARK = "spark"
ARROW = "arrow"
JVM = "jvm"
#: ``jvm`` runs with ``engine="jvm"``, so a compute that cannot register the kernel fails instead of timing native.
BENCH_ENGINES = (REFERENCE, JVM, SPARK, ARROW)
SOURCE = "source"
ALL = "all"
WINDOWS = (3, 6, 12)


@dataclass(frozen=True)
class Size:
    """A synthetic table: ``entities`` x ``closings`` monthly rows with ``columns`` value columns."""

    entities: int
    closings: int
    columns: int
    null_rate: float = 0.1
    seed: int = 7

    @property
    def label(self) -> str:
        """Return ``<entities>x<closings>x<columns>``."""
        return f"{self.entities}x{self.closings}x{self.columns}"

    @property
    def last_closing(self) -> date:
        """Return the latest closing of the synthetic table (``as_of`` of a single-closing run)."""
        return date(2000 + (self.closings - 1) // 12, (self.closings - 1) % 12 + 1, 1)

    @property
    def rows(self) -> int:
        """Return the number of input rows."""
        return self.entities * self.closings

    @classmethod
    def parse(cls, text: str) -> Size:
        """Read ``<entities>x<closings>x<columns>``."""
        try:
            entities, closings, columns = (int(part) for part in text.lower().split("x"))
        except ValueError:
            msg = f"a size is <entities>x<closings>x<columns>, got {text!r}"
            raise FeatureError(msg) from None
        return cls(entities, closings, columns)


class Budget:
    """A time budget for a run: ``expired`` is true once ``seconds`` have passed since it was created.

    The run checks it before each measurement and records the cells it skipped, so the report can say what is
    missing. A measurement already started always finishes.
    """

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        """Start the budget now; ``clock`` is injectable for tests."""
        if seconds <= 0:
            msg = f"a time budget is a positive number of seconds, got {seconds}"
            raise FeatureError(msg)
        self.seconds = seconds
        self.skipped: list[str] = []
        self._clock = clock
        self._start = clock()

    @property
    def expired(self) -> bool:
        """Return whether the budget is spent."""
        return self._clock() - self._start >= self.seconds

    def skip(self, cell: str) -> None:
        """Record a measurement that was not run."""
        self.skipped.append(cell)

    def note(self) -> str | None:
        """Return the report note of a partial run, or ``None`` when every measurement ran."""
        if not self.skipped:
            return None
        return f"partial: the {self.seconds:g} s time budget ran out; {len(self.skipped)} measurement(s) not run"


@dataclass(frozen=True)
class Timing:
    """One measurement."""

    engine: str
    family: str
    size: str
    seconds: float
    outputs: int


def value_columns(size: Size) -> list[str]:
    """Return the synthetic value column names."""
    return [f"v{j}" for j in range(size.columns)]


def benchmark_spec(columns: Sequence[str], family: str = ALL, windows: Sequence[int] = WINDOWS) -> FeatureSpec:
    """Return a spec with every aggregate of ``family`` (or the whole catalog) over ``columns`` x ``windows``."""
    if family != ALL and family not in FAMILIES:
        msg = f"family must be one of {', '.join((ALL, *FAMILIES))}, got {family!r}"
        raise FeatureError(msg)
    names = [n for n, a in CATALOG.items() if family in (ALL, a.family)]
    if family in (ALL, "distribution"):
        names += ["p10", "p50", "p90"]
    return FeatureSpec(("entity",), "closing", (WindowFeature(tuple(columns), tuple(windows), tuple(names)),))


def synthetic_rows(size: Size) -> list[dict[str, Any]]:
    """Return the seeded synthetic long table as Python rows."""
    rnd = random.Random(size.seed)  # noqa: S311 - seeded synthetic data
    columns = value_columns(size)
    rows: list[dict[str, Any]] = []
    for entity in range(size.entities):
        for m in range(size.closings):
            row: dict[str, Any] = {"entity": entity, "closing": date(2000 + m // 12, m % 12 + 1, 1)}
            for column in columns:
                row[column] = None if rnd.random() < size.null_rate else float(rnd.randint(-50, 950)) / 10
            rows.append(row)
    return rows


def synthetic_frame(spark: Any, size: Size) -> Any:  # noqa: ANN401 - pyspark objects
    """Return a synthetic frame of ``size`` generated on Spark."""
    from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration  # pyright: ignore[reportMissingImports]

    frame = spark.range(size.entities).withColumnRenamed("id", "entity")
    frame = frame.crossJoin(spark.range(size.closings).withColumnRenamed("id", "m"))
    frame = frame.withColumn("closing", f.expr("add_months(DATE'2000-01-01', CAST(m AS INT))"))
    cut = int(size.null_rate * 1000)
    for j, column in enumerate(value_columns(size)):
        hashed = f"abs(hash(entity, m, {j}, {size.seed}) % 1000)"
        value = f"IF({hashed} < {cut}, NULL, (abs(hash(m, entity, {j}, {size.seed}) % 1000) - 50) / 10.0D)"
        frame = frame.withColumn(column, f.expr(value))
    return frame.drop("m")


def materialize(frame: Any) -> None:  # noqa: ANN401 - pyspark objects
    """Force every column of ``frame`` to be computed with one small aggregate."""
    from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration  # pyright: ignore[reportMissingImports]

    frame.agg(f.max(f.hash(*frame.columns))).collect()


def _timed(run: Callable[..., object], *args: object, **kwargs: object) -> float:
    start = time.perf_counter()
    run(*args, **kwargs)
    return time.perf_counter() - start


def _run_spark(frame: Any, spec: FeatureSpec, engine: str, as_of: date | None) -> None:  # noqa: ANN401 - pyspark
    from bricksmith.features.engines import compute_features  # noqa: PLC0415 - loads pyspark

    materialize(compute_features(frame, spec, engine=engine, as_of=as_of))


def _measure(  # noqa: PLR0913, PLR0917
    engine: str,
    spec: FeatureSpec,
    rows: list[dict[str, Any]],
    frame: Any,  # noqa: ANN401 - pyspark objects
    as_of: date | None,
    warmed: set[str],
) -> float:
    """Time one engine on one spec; a Spark engine runs once unmeasured the first time it is seen."""
    if engine == REFERENCE:
        from bricksmith.features.engine import compute_rows  # noqa: PLC0415 - loads numpy

        return _timed(compute_rows, rows, spec, as_of)
    if engine not in warmed:
        _run_spark(frame, spec, engine, as_of)
        warmed.add(engine)
    return _timed(_run_spark, frame, spec, engine, as_of)


def run_benchmark(  # noqa: PLR0913 - one option per way of running
    spark: Any,  # noqa: ANN401 - a pyspark session, or None for the reference engine alone
    sizes: Iterable[Size],
    engines: Sequence[str] = BENCH_ENGINES,
    families: Sequence[str] = (*FAMILIES, ALL),
    *,
    single_closing: bool = False,
    budget: Budget | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[Timing]:
    """Time every engine on every family and size; Spark engines need ``spark``.

    ``single_closing`` times one closing (the last of each size) instead of all of them. ``budget`` stops the run
    cleanly between measurements, keeping what ran and recording what did not; ``progress`` gets one line per
    event.
    """
    known = (REFERENCE, *ENGINES)
    unknown = set(engines) - set(known)
    if unknown or (spark is None and set(engines) - {REFERENCE}):
        msg = f"engines are {', '.join(known)}; Spark engines need a session (unknown: {sorted(unknown)})"
        raise FeatureError(msg)
    say = progress or (lambda _line: None)
    sizes = list(sizes)
    total = len(sizes) * len(families) * len(engines)
    done = 0
    timings: list[Timing] = []
    warmed: set[str] = set()
    for size in sizes:
        as_of = size.last_closing if single_closing else None
        cells = [(family, engine) for family in families for engine in engines]
        if budget and budget.expired:
            for family, engine in cells:
                budget.skip(f"{engine}/{family}/{size.label}")
            say(f"[budget] {size.label}: not started, {len(cells)} measurement(s) skipped")
            continue
        say(f"[{size.label}] preparing data ({size.rows} rows{f', as_of {as_of}' if as_of else ''})")
        rows = synthetic_rows(size) if REFERENCE in engines else []
        frame: Any = synthetic_frame(spark, size) if spark is not None else None
        if frame is not None:
            seconds = _timed(materialize, frame)
            timings.append(Timing(SOURCE, ALL, size.label, seconds, 0))
            say(f"[{size.label}] source {seconds:.2f}s")
        for family, engine in cells:
            done += 1
            if budget and budget.expired:
                budget.skip(f"{engine}/{family}/{size.label}")
                continue
            spec = benchmark_spec(value_columns(size), family)
            say(f"[{done}/{total}] {size.label} {family} {engine} ...")
            seconds = _measure(engine, spec, rows, frame, as_of, warmed)
            timings.append(Timing(engine, family, size.label, seconds, len(spec.outputs)))
            say(f"[{done}/{total}] {size.label} {family} {engine} {seconds:.2f}s")
    if budget and (note := budget.note()):
        say(f"[budget] {note}")
    return timings


def render_report(timings: Sequence[Timing], notes: Sequence[str] = ()) -> str:
    """Return a markdown table: one row per family and engine, one column per size (seconds)."""
    sizes = list(dict.fromkeys(t.size for t in timings))
    keys = list(dict.fromkeys((t.family, t.engine) for t in timings))
    seconds = {(t.family, t.engine, t.size): t.seconds for t in timings}
    outputs = {(t.family, t.engine): t.outputs for t in timings}
    lines = [*(f"- {n}" for n in notes), *([""] if notes else [])]
    lines.append("| Family | Engine | Outputs | " + " | ".join(f"{s} (s)" for s in sizes) + " |")
    lines.append("|---|---|---:|" + "---:|" * len(sizes))
    for family, engine in keys:
        cells = [f"{seconds[family, engine, s]:.2f}" if (family, engine, s) in seconds else "-" for s in sizes]
        lines.append(f"| {family} | {engine} | {outputs[family, engine]} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
