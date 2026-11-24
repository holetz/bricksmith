"""The JVM engine's pure parts: the plan string, the SQL around the kernel call and the JAR naming."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from typing import TYPE_CHECKING

import pytest

from bricksmith.features import CategoricalCount, FeatureError, FeatureSpec, Ratio, WindowFeature, kernel
from bricksmith.features.kernel import (
    FUNCTION_NAME,
    KERNEL_CLASS,
    RETURN_TYPE,
    SESSION,
    UC_HANDLER,
    UNITY_CATALOG,
    KernelStatus,
    KernelUnavailableError,
    call_sql,
    history_sql,
    install_uc_function,
    is_connect,
    jar_digest,
    kernel_plan,
    output_columns,
    probe,
    register_session,
    target_sql,
    uc_function_sql,
    versioned_jar_name,
)
from bricksmith.io.volumes import Entry

if TYPE_CHECKING:
    from pathlib import Path

SPEC = FeatureSpec(
    keys=("entity",),
    closing="closing",
    features=(
        WindowFeature(("x", "y"), (3, 6), ("mean", "count")),
        WindowFeature(("y",), (12,), ("p95",)),
    ),
    ratios=(Ratio("r", "x_mean_3p", "y"),),
    categorical=(CategoricalCount("status", ("a",), (3,)),),
)


def test_plan_lists_the_window_outputs_in_spec_order_by_column_index() -> None:
    assert kernel_plan(SPEC) == ("0:mean:3|0:count:3|0:mean:6|0:count:6|1:mean:3|1:count:3|1:mean:6|1:count:6|1:p95:12")


def test_history_collects_period_day_and_doubles_sorted_by_period() -> None:
    sql = history_sql(SPEC)
    assert sql.startswith("array_sort(collect_list(named_struct('p', CAST(")
    assert "unix_date(CAST(`closing` AS DATE))" in sql
    assert "array(CAST(`x` AS DOUBLE), CAST(`y` AS DOUBLE))" in sql


def test_target_is_null_or_the_period_of_the_cutoff() -> None:
    assert target_sql(SPEC, None) == "CAST(NULL AS INT)"
    assert "DATE'2026-06-30'" in target_sql(SPEC, date(2026, 6, 30))
    call = call_sql(SPEC, "cat.sch.kernel", date(2026, 6, 30))
    assert call.startswith("cat.sch.kernel(`__f_history`, '0:mean:3|")


def test_outputs_read_the_exploded_row_with_the_catalog_type() -> None:
    columns = dict(output_columns(SPEC))
    assert columns["closing"] == "date_from_unix_date(`__f_row`.d)"
    assert columns["x_mean_3p"] == "CAST(element_at(`__f_row`.v, 1) AS DOUBLE)"
    assert columns["x_count_3p"] == "CAST(element_at(`__f_row`.v, 2) AS BIGINT)"
    assert columns["y_p95_12p"] == "CAST(element_at(`__f_row`.v, 9) AS DOUBLE)"
    assert "r" not in columns
    assert "status_count_a_3p" not in columns
    assert RETURN_TYPE == "array<struct<p:int,d:int,v:array<double>>>"


def test_versioned_name_carries_version_and_content_hash(tmp_path: Path) -> None:
    jar = tmp_path / "k.jar"
    jar.write_bytes(b"kernel")
    digest = jar_digest(jar)
    assert len(digest) == 64
    assert versioned_jar_name("1.2.3", digest) == f"bricksmith-kernel-1.2.3-{digest[:12]}.jar"


def test_importing_the_kernel_module_loads_no_heavy_integration() -> None:
    code = (
        "import sys, bricksmith.features.kernel\n"
        "heavy = {'numpy', 'pyarrow', 'pandas', 'pyspark'} & {m.split('.')[0] for m in sys.modules}\n"
        "assert not heavy, heavy\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


class FakeUdf:
    def __init__(self, spark: FakeSpark) -> None:
        self.spark = spark

    def registerJavaFunction(self, name: str, java_class: str, return_type: str) -> None:  # noqa: N802 - pyspark
        if self.spark.refuse_register:
            raise RuntimeError(self.spark.refuse_register)
        self.spark.calls.append(("register", name, java_class, return_type))
        self.spark.registered.add(name)


class FakeSpark:
    def __init__(self, *, refuse_register: str = "", known: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.refuse_register = refuse_register
        self.registered: set[str] = set(known)
        self.udf = FakeUdf(self)

    def sql(self, statement: str) -> FakeResult:
        self.calls.append(("sql", statement))
        if statement.startswith("SELECT "):
            name = statement.removeprefix("SELECT ").split("(", 1)[0]
            if name not in self.registered:
                msg = f"[UNRESOLVED_ROUTINE] Cannot resolve routine {name}\nstack"
                raise RuntimeError(msg)
            return FakeResult([[[[1, 1, [2.5]]]]])
        return FakeResult([])


class FakeConnect(FakeSpark):
    session_id = "connect-session"

    def addArtifacts(self, path: str) -> None:  # noqa: N802 - pyspark
        self.calls.append(("artifact", path))


FakeConnect.__module__ = "pyspark.sql.connect.session"


class FakeResult:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def collect(self) -> list[object]:
        return self.rows


class FakeVolumes:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def exists(self, path: str) -> bool:
        return path in self.files

    def write(self, path: str, data: bytes, *, overwrite: bool) -> None:
        assert not overwrite
        self.files[path] = data

    def read(self, path: str, max_bytes: int) -> bytes:
        return self.files[path][:max_bytes]

    def list(self, directory: str) -> list[Entry]:
        return [
            Entry(p.rsplit("/", 1)[1], is_dir=False, size=len(d))
            for p, d in self.files.items()
            if p.startswith(directory)
        ]

    def delete(self, path: str) -> None:
        del self.files[path]


@pytest.fixture
def jar(tmp_path: Path) -> Path:
    path = tmp_path / "bricksmith-kernel.jar"
    path.write_bytes(b"kernel bytes")
    return path


def test_session_registration_adds_the_jar_the_way_the_session_supports(jar: Path) -> None:
    classic = FakeSpark()
    assert register_session(classic, jar) == FUNCTION_NAME
    assert classic.calls[0] == ("sql", f"ADD JAR '{jar}'")
    assert classic.calls[1] == ("register", FUNCTION_NAME, KERNEL_CLASS, RETURN_TYPE)
    connect = FakeConnect()
    register_session(connect, jar, "my_kernel")
    assert connect.calls[0] == ("artifact", str(jar))
    assert is_connect(connect)
    assert not is_connect(classic)


def test_a_missing_jar_is_a_named_unavailability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kernel, "packaged_jar", lambda: None)
    with pytest.raises(KernelUnavailableError, match="not packaged"):
        register_session(FakeSpark())


def test_unity_catalog_function_sql_and_idempotent_install(jar: Path) -> None:
    sql = uc_function_sql("main.feat.kernel", "/Volumes/main/feat/jars/k.jar")
    assert sql.startswith("CREATE OR REPLACE FUNCTION main.feat.kernel(history ARRAY<STRUCT<p: INT")
    assert "LANGUAGE SCALA" in sql
    assert """java_dependencies = '["/Volumes/main/feat/jars/k.jar"]'""" in sql
    assert f"HANDLER '{UC_HANDLER}'" in sql
    with pytest.raises(FeatureError, match="identifier"):
        uc_function_sql("main.feat.kernel; DROP", "/x")
    spark, volumes = FakeSpark(), FakeVolumes()
    first = install_uc_function(spark, volumes, "main.feat.jars", "main.feat.kernel", jar=jar, version="1.0")
    assert first == f"/Volumes/main/feat/jars/bricksmith-kernel-1.0-{jar_digest(jar)[:12]}.jar"
    assert volumes.files == {first: b"kernel bytes"}
    again = install_uc_function(spark, volumes, "main.feat.jars", "main.feat.kernel", jar=jar, version="1.0")
    assert again == first
    assert sum(1 for c in spark.calls if c[1].startswith("CREATE OR REPLACE")) == 2


def test_probe_uses_the_session_path_and_caches_per_session(jar: Path) -> None:
    spark = FakeSpark()
    status = probe(spark, jar=jar)
    assert status == KernelStatus(available=True, path=SESSION, function=FUNCTION_NAME)
    calls = len(spark.calls)
    assert probe(spark, jar=jar) is status
    assert len(spark.calls) == calls
    assert probe(spark, jar=jar, refresh=True) == status


def test_probe_tries_the_named_unity_catalog_function_first(jar: Path) -> None:
    spark = FakeSpark(known=("main.feat.kernel",))
    status = probe(spark, "main.feat.kernel", jar=jar)
    assert status.path == UNITY_CATALOG
    assert status.function == "main.feat.kernel"
    assert not any(c[0] == "register" for c in spark.calls)


def test_probe_reports_why_every_path_failed(jar: Path) -> None:
    spark = FakeSpark(refuse_register="[NOT_SUPPORTED] Java UDFs are not allowed\ndetails")
    status = probe(spark, "main.feat.missing", jar=jar)
    assert not status.available
    assert status.reason == (
        "unity-catalog: RuntimeError: [UNRESOLVED_ROUTINE] Cannot resolve routine main.feat.missing; "
        "session: RuntimeError: [NOT_SUPPORTED] Java UDFs are not allowed"
    )
