"""Feature engineering on the selected workspace: the Arrow engine and mixed engines equal the numpy reference.

The Arrow engine runs Python on the workers. A workspace that refuses Python UDFs (the free workspace answers
``ISOLATION_STARTUP_FAILURE.SANDBOX_STARTUP``) records these scenarios as Unsupported, the state named in the
test setup guide; the Arrow proof there is local (docs/project/evidence/features-free.md).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.features import compute_features
from bricksmith.features.arrow import arrow_path

from .test_features import DDL, SPEC, assert_equal_to_reference, synthetic

if TYPE_CHECKING:
    from bricksmith.clients import Clients

SANDBOX_REFUSED = "ISOLATION_STARTUP_FAILURE"


def require_python_udfs(spark: Any) -> None:  # noqa: ANN401 - a pyspark session
    """Skip as Unsupported when the workspace refuses to start a Python worker; any other error fails."""
    probe = spark.range(1).mapInPandas(lambda frames: frames, "id long")
    try:
        probe.collect()
    except Exception as error:
        if SANDBOX_REFUSED not in str(error):
            raise
        pytest.skip(f"Unsupported: this workspace refuses Python UDFs ({SANDBOX_REFUSED}.SANDBOX_STARTUP)")


def test_arrow_engine_equals_the_reference(remote_clients: Clients) -> None:
    spark = remote_clients.spark()
    require_python_udfs(spark)
    rows = synthetic()
    frame = spark.createDataFrame(rows, DDL)
    sys.stderr.write(f"ARROW PATH: {arrow_path(frame)}\n")
    assert_equal_to_reference(compute_features(frame, SPEC, engine="arrow"), rows)


def test_auto_with_mixed_engines_equals_the_reference(remote_clients: Clients) -> None:
    spark = remote_clients.spark()
    require_python_udfs(spark)
    rows = synthetic()
    frame = spark.createDataFrame(rows, DDL)
    mixed = compute_features(frame, SPEC, engines={"sequence": "arrow", "trend": "arrow"})
    assert_equal_to_reference(mixed, rows)
