"""Time the feature engines per aggregate family and print a markdown report.

Usage: ``python scripts/features_benchmark.py [--sizes 200x24x4,2000x24x4] [--engines reference,spark,arrow]
[--families sequence,trend,all] [--output report.md]``. The Spark engines run on the context selected the usual way
(``.env`` and ``BRICKSMITH_*`` variables, see the test setup guide); ``--engines reference`` needs no workspace.
"""

from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path

from bricksmith.features.benchmark import BENCH_ENGINES, REFERENCE, Size, render_report, run_benchmark
from bricksmith.features.catalog import FAMILIES

ROOT = Path(__file__).resolve().parent.parent


def _split(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark and print (or write) the report."""
    parser = argparse.ArgumentParser(description="Time the feature engines per aggregate family.")
    parser.add_argument("--sizes", default="200x24x4,2000x24x4")
    parser.add_argument("--engines", default=",".join(BENCH_ENGINES))
    parser.add_argument("--families", default=",".join((*FAMILIES, "all")))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    sizes = [Size.parse(s) for s in _split(args.sizes)]
    engines = _split(args.engines)
    spark = None
    if set(engines) - {REFERENCE}:
        from bricksmith.clients import Clients  # noqa: PLC0415 - only the Spark engines need a session
        from bricksmith.context import resolve_context  # noqa: PLC0415
        from bricksmith.env import load_dotenv  # noqa: PLC0415

        dotenv = ROOT / ".env"  # optional, as in the remote tests: the process environment alone may select the context
        spark = Clients(resolve_context(dotenv=load_dotenv(dotenv) if dotenv.is_file() else {})).spark()
    timings = run_benchmark(spark, sizes, engines, _split(args.families))
    notes = [f"python {platform.python_version()}", f"engines: {', '.join(engines)}"]
    report = render_report(timings, notes)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
