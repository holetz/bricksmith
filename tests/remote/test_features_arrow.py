"""Feature engineering on the selected workspace: the Arrow engine and mixed engines equal the numpy reference."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from bricksmith.features import compute_features
from bricksmith.features.arrow import arrow_path

from .test_features import DDL, SPEC, assert_equal_to_reference, synthetic

if TYPE_CHECKING:
    from bricksmith.clients import Clients


def test_arrow_engine_equals_the_reference(remote_clients: Clients) -> None:
    spark = remote_clients.spark()
    rows = synthetic()
    frame = spark.createDataFrame(rows, DDL)
    sys.stderr.write(f"ARROW PATH: {arrow_path(frame)}\n")
    assert_equal_to_reference(compute_features(frame, SPEC, engine="arrow"), rows)


def test_auto_with_mixed_engines_equals_the_reference(remote_clients: Clients) -> None:
    spark = remote_clients.spark()
    rows = synthetic()
    frame = spark.createDataFrame(rows, DDL)
    mixed = compute_features(frame, SPEC, engines={"sequence": "arrow", "trend": "arrow"})
    assert_equal_to_reference(mixed, rows)
