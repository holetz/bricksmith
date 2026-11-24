"""The JVM engine: one compiled Scala kernel per entity history, shipped as a JAR inside the bricksmith wheel.

The kernel has one fixed SQL signature, so a single registration serves every spec::

    kernel(history ARRAY<STRUCT<p INT, d INT, v ARRAY<DOUBLE>>>, plan STRING, target INT)
      -> ARRAY<STRUCT<p INT, d INT, v ARRAY<DOUBLE>>>

``history`` is one entity's rows sorted by period: ``p`` is the period index (the same numbers as the native
engine), ``d`` the closing as days since 1970-01-01 and ``v`` the declared numeric columns as doubles. ``plan``
lists the window outputs as ``<column index>:<aggregate>:<window>`` joined by ``|``. ``target`` is the period of
``as_of``, or null for every closing. The kernel returns one struct per requested closing, whose ``v`` holds the
outputs in plan order (null where the catalog says null); Python explodes it and aliases each value back to its
output name. The functions of this module that build SQL are pure; pyspark loads only where a frame is touched.
"""

from __future__ import annotations

import hashlib
from importlib import resources
from typing import TYPE_CHECKING

from bricksmith.features.catalog import INTEGER
from bricksmith.features.spark import PERIOD_SQL, quote

if TYPE_CHECKING:
    from pathlib import Path

    from bricksmith.features.spec import FeatureSpec

#: The JVM engine's name in ``engine=`` and in ``DEFAULT_ENGINES``.
JVM = "jvm"
#: The Scala class implementing the kernel (``UDF3``) and its Unity Catalog handler.
KERNEL_CLASS = "bricksmith.features.WindowKernel"
UC_HANDLER = f"{KERNEL_CLASS}.compute"
#: The packaged JAR, under ``bricksmith/features/_jars``.
JAR_NAME = "bricksmith-kernel.jar"
#: The default name of the session-scoped function.
FUNCTION_NAME = "bricksmith_window_kernel"
#: The kernel's SQL types.
ROW_TYPE = "struct<p:int,d:int,v:array<double>>"
RETURN_TYPE = f"array<{ROW_TYPE}>"

HISTORY = "__f_history"
ROW = "__f_row"
PERIOD = "p"
DAY = "d"
VALUES = "v"


def kernel_plan(spec: FeatureSpec) -> str:
    """Return the plan string of the window outputs of ``spec``, in spec order."""
    index = {column: i for i, column in enumerate(spec.value_columns)}
    return "|".join(f"{index[o.column]}:{o.aggregate}:{o.window}" for o in spec.window_outputs())


def history_struct_sql(spec: FeatureSpec) -> str:
    """Return the per-row struct the history array collects: period, day and the numeric columns as doubles."""
    closing = f"CAST({quote(spec.closing)} AS DATE)"
    period = PERIOD_SQL[spec.period].format(c=closing)
    values = ", ".join(f"CAST({quote(c)} AS DOUBLE)" for c in spec.value_columns)
    return (
        f"named_struct('{PERIOD}', CAST({period} AS INT), '{DAY}', unix_date({closing}), '{VALUES}', array({values}))"
    )


def history_sql(spec: FeatureSpec) -> str:
    """Return the aggregate expression that builds one entity's period-sorted history."""
    return f"array_sort(collect_list({history_struct_sql(spec)}))"


def target_sql(spec: FeatureSpec, cutoff: object | None) -> str:
    """Return the kernel's ``target`` argument: the period of ``cutoff`` (a date), or a null INT."""
    if cutoff is None:
        return "CAST(NULL AS INT)"
    return f"CAST({PERIOD_SQL[spec.period].format(c=f"DATE'{cutoff}'")} AS INT)"


def call_sql(spec: FeatureSpec, function: str, cutoff: object | None = None) -> str:
    """Return the top-level kernel call over the history column (higher-order functions cannot host it)."""
    plan = kernel_plan(spec).replace("'", "")
    return f"{function}({quote(HISTORY)}, '{plan}', {target_sql(spec, cutoff)})"


def output_columns(spec: FeatureSpec) -> list[tuple[str, str]]:
    """Return ``(name, sql)`` of the closing and every window output, read from the exploded kernel row."""
    columns = [(spec.closing, f"date_from_unix_date({quote(ROW)}.{DAY})")]
    for i, output in enumerate(spec.window_outputs(), start=1):
        cast = "BIGINT" if output.dtype == INTEGER else "DOUBLE"
        columns.append((output.name, f"CAST(element_at({quote(ROW)}.{VALUES}, {i}) AS {cast})"))
    return columns


def packaged_jar() -> Path | None:
    """Return the path of the JAR packaged in this installation, or ``None`` when the build carried none."""
    candidate = resources.files("bricksmith.features").joinpath("_jars", JAR_NAME)
    try:
        with resources.as_file(candidate) as path:
            return path if path.is_file() else None
    except (FileNotFoundError, NotADirectoryError):
        return None


def jar_digest(path: Path) -> str:
    """Return the SHA-256 of a JAR, the content part of its versioned name."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def versioned_jar_name(version: str, digest: str) -> str:
    """Return the name a JAR is uploaded under: ``bricksmith-kernel-<version>-<sha12>.jar``."""
    return f"bricksmith-kernel-{version}-{digest[:12]}.jar"
