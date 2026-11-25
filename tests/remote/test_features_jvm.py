"""The JVM feature kernel on the selected workspace: both registration paths, parity, or a recorded fallback.

The test installs the kernel as a Unity Catalog Scala function over a test-owned volume and also tries the session
registration. When either path works, the kernel must equal the numpy reference and ``auto`` must record ``jvm``.
When neither works, ``auto`` must still equal the reference on native Spark and record the fallback with the
reason, and the scenario is reported as Unsupported with the exact errors (the state named in the test setup
guide). Every path's outcome is written to stderr for the evidence page.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from bricksmith.features import compute_features
from bricksmith.features.engines import AUTO, JVM, NATIVE
from bricksmith.features.kernel import install_uc_function, probe
from bricksmith.features.record import collecting
from bricksmith.io import files_api_client

from .test_features import DDL, SPEC, assert_equal_to_reference, synthetic

if TYPE_CHECKING:
    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext


def _first_line(error: BaseException) -> str:
    lines = str(error).strip().splitlines()
    return f"{type(error).__name__}: {lines[0][:300] if lines else ''}"


def test_the_kernel_runs_or_auto_records_the_native_fallback(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str, jar_volume: str
) -> None:
    spark = remote_clients.spark()
    function = f"{remote_context.catalog}.{test_schema}.bricksmith_window_kernel"
    installed = ""
    try:
        installed = install_uc_function(spark, files_api_client(remote_clients), jar_volume, function)
    except Exception as error:  # noqa: BLE001 - an installation refusal is the outcome being recorded
        sys.stderr.write(f"KERNEL INSTALL: {_first_line(error)}\n")
    else:
        sys.stderr.write(f"KERNEL INSTALL: ok ({installed.rsplit('/', 1)[1]})\n")
    status = probe(spark, function if installed else None, refresh=True)
    sys.stderr.write(f"KERNEL PROBE: available={status.available} path={status.path} reason={status.reason}\n")
    rows = synthetic()
    frame = spark.createDataFrame(rows, DDL)
    kernel_function = function if installed else None
    with collecting() as choices:
        result = compute_features(frame, SPEC, kernel_function=kernel_function)
    assert_equal_to_reference(result, rows)
    [choice] = choices
    if status.available:
        assert (choice.requested, choice.engine, choice.path) == (AUTO, JVM, status.path)
        assert_equal_to_reference(compute_features(frame, SPEC, engine=JVM, kernel_function=kernel_function), rows)
        return
    assert (choice.requested, choice.engine, choice.reason) == (AUTO, NATIVE, status.reason)
    assert status.reason
    pytest.skip(f"Unsupported: the JVM kernel cannot run here; auto fell back to native ({status.reason})")
