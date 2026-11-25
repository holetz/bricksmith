"""Shared fixtures for the ``jvm`` proof layer (reach: ``session``).

A classic local Spark session with the packaged kernel JAR; every test that uses it skips, with the reason, without
one. The project environment carries Databricks Connect's pyspark, which cannot start a local session, so these tests
run in an isolated environment with classic pyspark: ``uv run --isolated --with pyspark==4.0.1 pytest tests/jvm -q``.
They also need Java and the JAR ``scripts/build_kernel.py`` places in the package. CI sets
``BRICKSMITH_REQUIRE_KERNEL=true`` so a missing prerequisite fails there instead of skipping.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.features.kernel import packaged_jar

if TYPE_CHECKING:
    from collections.abc import Iterator


# QUENCHING PROOF FIXTURE LIBRARY: BEGIN
# The declared reach is the ceiling for this module: session.


def missing() -> str | None:
    """Return why a classic local session with the kernel cannot run here, or None when it can."""
    found = importlib.util.find_spec("pyspark")
    if found is None or found.origin is None or not (Path(found.origin).parent / "jars").is_dir():
        return "classic pyspark is not installed (run with uv run --isolated --with pyspark==4.0.1)"
    if shutil.which("java") is None:
        return "java is not on PATH"
    if packaged_jar() is None:
        return "the kernel JAR is not packaged (run python3 scripts/build_kernel.py)"
    return None


@pytest.fixture(scope="session")
def spark() -> Iterator[Any]:
    """Yield a local ANSI session; skip every dependent test with the reason when one cannot start."""
    reason = missing()
    if reason and os.environ.get("BRICKSMITH_REQUIRE_KERNEL") == "true":
        pytest.fail(reason)
    if reason:
        pytest.skip(reason)
    from pyspark.sql import SparkSession  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    session = (
        SparkSession.builder.master("local[2]")
        .appName("bricksmith-jvm-kernel")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.ansi.enabled", "true")
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


# QUENCHING PROOF FIXTURE LIBRARY: END
