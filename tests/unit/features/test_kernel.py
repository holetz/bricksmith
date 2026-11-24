"""The JVM engine's pure parts: the plan string, the SQL around the kernel call and the JAR naming."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from typing import TYPE_CHECKING

from bricksmith.features import CategoricalCount, FeatureSpec, Ratio, WindowFeature
from bricksmith.features.kernel import (
    RETURN_TYPE,
    call_sql,
    history_sql,
    jar_digest,
    kernel_plan,
    output_columns,
    target_sql,
    versioned_jar_name,
)

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
