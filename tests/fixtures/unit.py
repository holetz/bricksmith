"""Shared fixtures for the ``unit`` proof layer (reach: ``session``)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


# QUENCHING PROOF FIXTURE LIBRARY: BEGIN
# The declared reach is the ceiling for this module: session.


@pytest.fixture(scope="module")
def local_spark() -> Iterator[Any]:
    """Yield a one-core local Spark session; skip where pyspark is missing or cannot start one."""
    sql = pytest.importorskip("pyspark.sql")
    try:
        session = (
            sql.SparkSession.builder.master("local[1]")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "1")
            .getOrCreate()
        )
    except Exception as exc:  # noqa: BLE001 - any refusal to start a local session skips
        pytest.skip(f"no local Spark session: {exc}")
    yield session
    session.stop()


# QUENCHING PROOF FIXTURE LIBRARY: END
