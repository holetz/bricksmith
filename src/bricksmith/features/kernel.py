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

Two registration paths reach the kernel. A **session-scoped** function adds the packaged JAR to the session
(``addArtifacts`` on Spark Connect, ``ADD JAR`` on a classic session) and registers it with
``registerJavaFunction``. A **Unity Catalog** Scala function (:func:`install_uc_function`) points at the JAR uploaded
to a volume under its versioned name. :func:`probe` tries the configured paths once per session, in
:data:`PROBE_ORDER`, and caches what it found, the reasons for every failure included.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING, Any

from bricksmith.features.catalog import INTEGER
from bricksmith.features.errors import FeatureError
from bricksmith.features.spark import PERIOD_SQL, quote, refuse_future

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

    from pyspark.sql import DataFrame  # pyright: ignore[reportMissingImports]

    from bricksmith.features.spec import FeatureSpec
    from bricksmith.io.volumes import VolumeClient

#: The JVM engine's name in ``engine=`` and in ``DEFAULT_ENGINES``.
JVM = "jvm"
#: The Scala class implementing the kernel (``UDF3``) and its Unity Catalog handler.
KERNEL_CLASS = "bricksmith.features.WindowKernel"
UC_HANDLER = f"{KERNEL_CLASS}.handle"
#: The packaged JAR, under ``bricksmith/features/_jars``.
JAR_NAME = "bricksmith-kernel.jar"
#: The default name of the session-scoped function.
FUNCTION_NAME = "bricksmith_window_kernel"
#: The kernel's SQL types.
ROW_TYPE = "struct<p:int,d:int,v:array<double>>"
RETURN_TYPE = f"array<{ROW_TYPE}>"

#: Registration paths, in the order :func:`probe` tries them (the Unity Catalog path only when a function is named).
#: Measured (docs/project/evidence/features-free.md): serverless runs only the Unity Catalog function and refuses the
#: session registration, while a classic session registers it; a named function is the explicit choice, so it goes
#: first, and the session path costs no failed sandbox start where a function exists.
UNITY_CATALOG = "unity-catalog"
SESSION = "session"
PROBE_ORDER = (UNITY_CATALOG, SESSION)
#: The Databricks environment version the Unity Catalog function runs in.
ENVIRONMENT_VERSION = "4"

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


class KernelUnavailableError(FeatureError):
    """The JVM kernel cannot be registered or run on this compute (``engine="jvm"`` asked for it explicitly)."""

    code = "kernel_unavailable"


@dataclass(frozen=True)
class KernelStatus:
    """What :func:`probe` found: the function to call and its path, or why every path failed."""

    available: bool
    path: str = ""
    function: str = ""
    reason: str = ""


_FUNCTION = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}")
_PROBED: dict[object, KernelStatus] = {}


def _function_name(name: str) -> str:
    if not _FUNCTION.fullmatch(name):
        msg = f"a kernel function name is an identifier or catalog.schema.name, got {name!r}"
        raise FeatureError(msg)
    return name


def _f() -> Any:  # noqa: ANN401 - the pyspark functions module
    from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration  # pyright: ignore[reportMissingImports]

    return f


def is_connect(spark: Any) -> bool:  # noqa: ANN401 - a pyspark session
    """Return whether ``spark`` is a Spark Connect session (Databricks Connect included)."""
    return type(spark).__module__.startswith("pyspark.sql.connect")


def _session_key(spark: Any) -> object:  # noqa: ANN401 - a pyspark session
    return getattr(spark, "session_id", None) or id(spark)


def _require_jar(jar: Path | None) -> Path:
    found = jar or packaged_jar()
    if found is None:
        msg = "the kernel JAR is not packaged in this installation (build it with scripts/build_kernel.py)"
        raise KernelUnavailableError(msg)
    return found


def register_session(spark: Any, jar: Path | None = None, name: str = FUNCTION_NAME) -> str:  # noqa: ANN401
    """Add the JAR to ``spark`` and register the kernel as the session function ``name``; return ``name``."""
    path = _require_jar(jar)
    _function_name(name)
    if is_connect(spark):
        spark.addArtifacts(str(path))
    else:
        spark.sql(f"ADD JAR '{path}'")
    spark.udf.registerJavaFunction(name, KERNEL_CLASS, RETURN_TYPE)
    return name


def uc_function_sql(function: str, jar_path: str) -> str:
    """Return the ``CREATE OR REPLACE FUNCTION`` statement of the Unity Catalog kernel over ``jar_path``."""
    sql_type = "ARRAY<STRUCT<p: INT, d: INT, v: ARRAY<DOUBLE>>>"
    return (
        f"CREATE OR REPLACE FUNCTION {_function_name(function)}(history {sql_type}, plan STRING, target INT) "
        f"RETURNS {sql_type} LANGUAGE SCALA DETERMINISTIC "
        f"ENVIRONMENT (java_dependencies = '[\"{jar_path}\"]', environment_version = '{ENVIRONMENT_VERSION}') "
        f"HANDLER '{UC_HANDLER}'"
    )


def install_uc_function(  # noqa: PLR0913 - one keyword per installation setting
    spark: Any,  # noqa: ANN401 - a pyspark session
    volumes: VolumeClient,
    volume: str,
    function: str,
    *,
    jar: Path | None = None,
    version: str | None = None,
) -> str:
    """Upload the JAR to ``volume`` under its versioned name (once) and create or replace ``function``.

    Return the volume path of the JAR. Running it again with the same JAR uploads nothing.
    """
    from bricksmith.io.volumes import VolumeRef  # noqa: PLC0415 - only the installer needs volumes

    path = _require_jar(jar)
    if version is None:
        from importlib.metadata import version as installed  # noqa: PLC0415

        version = installed("bricksmith")
    target = f"{VolumeRef.parse(volume).root}/{versioned_jar_name(version, jar_digest(path))}"
    if not volumes.exists(target):
        volumes.write(target, path.read_bytes(), overwrite=False)
    spark.sql(uc_function_sql(function, target))
    return target


def _check(spark: Any, function: str) -> None:  # noqa: ANN401 - a pyspark session
    """Run the kernel once on a literal history; raise unless it returns the expected sum."""
    history = "array(named_struct('p', 1, 'd', 1, 'v', array(CAST(2.5 AS DOUBLE))))"
    rows = spark.sql(f"SELECT {function}({history}, '0:sum:1', CAST(NULL AS INT)) AS r").collect()
    value = rows[0][0][0][2][0]
    if value != 2.5:  # noqa: PLR2004 - the literal summed above
        msg = f"the kernel answered {value!r} to its probe instead of 2.5"
        raise KernelUnavailableError(msg)


def _first_line(error: BaseException) -> str:
    text = str(error).strip().splitlines()
    return f"{type(error).__name__}: {text[0][:300] if text else ''}"


def probe(
    spark: Any,  # noqa: ANN401 - a pyspark session
    function: str | None = None,
    *,
    jar: Path | None = None,
    refresh: bool = False,
) -> KernelStatus:
    """Find a working kernel on ``spark`` once per session and cache the result.

    ``function`` names an installed Unity Catalog function; without it only the session path is tried. A path
    that fails adds its error to the reason, so an unavailable status says why every path failed.
    """
    key = (_session_key(spark), function)
    if not refresh and key in _PROBED:
        return _PROBED[key]
    reasons: list[str] = []
    status = None
    for path in PROBE_ORDER:
        if path == UNITY_CATALOG and not function:
            continue
        try:
            name = function if path == UNITY_CATALOG else register_session(spark, jar)
            assert name is not None  # noqa: S101 - narrowed by the loop guard
            _check(spark, name)
        except Exception as error:  # noqa: BLE001 - every failure is a reason to try the next path
            reasons.append(f"{path}: {_first_line(error)}")
            continue
        status = KernelStatus(available=True, path=path, function=name)
        break
    status = status or KernelStatus(available=False, reason="; ".join(reasons))
    _PROBED[key] = status
    return status


def compute_jvm(
    df: DataFrame,
    spec: FeatureSpec,
    as_of: date | str | None = None,
    function: str = FUNCTION_NAME,
) -> DataFrame:
    """Return one row per entity and closing of ``df`` (or only ``as_of``) with the window outputs of ``spec``.

    ``function`` is a registered kernel (see :func:`probe`). Category counts, ratios and differences are not
    computed here; ``compute_features`` adds them.
    """
    f = _f()
    cutoff = refuse_future(df, spec, as_of)
    keys = [f.col(quote(k)) for k in spec.keys]
    history = df.groupBy(*keys).agg(f.expr(history_sql(spec)).alias(HISTORY))
    called = history.select(*keys, f.expr(call_sql(spec, _function_name(function), cutoff)).alias(ROW))
    rows = called.select(*keys, f.explode(f.col(ROW)).alias(ROW))
    result = rows.select(*keys, *(f.expr(sql).alias(name) for name, sql in output_columns(spec)))
    if cutoff is not None:
        result = result.where(f.col(quote(spec.closing)) == f.lit(cutoff))
    return result
