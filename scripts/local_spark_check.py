"""Run the local-Spark parity tests in an isolated classic-pyspark environment and fail if any is skipped.

Usage: ``python scripts/local_spark_check.py [pytest args...]``. The project environment carries Databricks Connect,
whose ``pyspark`` refuses local sessions, so ``uv run pytest`` skips the tests that use the ``local_spark`` fixture.
This runs them with ``uv run --isolated --with pyspark==4.0.1`` and sets ``BRICKSMITH_REQUIRE_LOCAL_SPARK=true``;
it also fails when the run reports any skip or selects no test, so the gate cannot pass vacuously.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

from bootstrap import ROOT

PYSPARK = "pyspark==4.0.1"
TESTS = ("tests/unit/monitor/test_spark_parity.py", "tests/unit/monitor/model/test_engine_parity.py")


def main(argv: list[str]) -> int:
    """Run the parity tests and return a non-zero code on any failure, skip or empty run."""
    command = ["uv", "run", "--isolated", "--with", PYSPARK, "pytest", "-q", "-rs", *(argv or TESTS)]
    env = {**os.environ, "BRICKSMITH_REQUIRE_LOCAL_SPARK": "true"}
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        return result.returncode
    if re.search(r"\b\d+ skipped\b", result.stdout) or not re.search(r"\b\d+ passed\b", result.stdout):
        sys.stderr.write("local Spark parity tests must all run: a skip or an empty selection fails the gate\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
