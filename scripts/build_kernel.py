"""Build the JVM feature kernel and place its JAR where the wheel packages it.

Usage: ``python scripts/build_kernel.py [--skip-tests]``. Runs ``sbt test package`` in ``jvm/`` (``sbt`` from
``PATH``, or the path in ``$SBT``) and copies the JAR to ``src/bricksmith/features/_jars/``. That directory is
ignored by git; hatch ships the JAR as a build artifact, so a wheel built afterwards carries it. A JDK 17 or later
and sbt are needed only here, never to use bricksmith.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / "jvm"
BUILT = PROJECT / "target" / "bricksmith-kernel.jar"
PACKAGED = ROOT / "src" / "bricksmith" / "features" / "_jars" / "bricksmith-kernel.jar"


def main(argv: list[str] | None = None) -> int:
    """Build, test and copy the kernel JAR."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-tests", action="store_true", help="package without running the Scala suite")
    args = parser.parse_args(argv)
    sbt = os.environ.get("SBT") or shutil.which("sbt")
    if sbt is None:
        sys.exit("sbt is not on PATH (set $SBT to its launcher)")
    tasks = ["package"] if args.skip_tests else ["test", "package"]
    result = subprocess.run([sbt, "-batch", *tasks], cwd=PROJECT, check=False)
    if result.returncode != 0:
        return result.returncode
    if not BUILT.is_file():
        sys.exit(f"sbt finished but {BUILT.relative_to(ROOT)} is missing")
    PACKAGED.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BUILT, PACKAGED)
    print(f"kernel JAR: {PACKAGED.relative_to(ROOT)} ({PACKAGED.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
