"""Time the feature engines per aggregate family and print a markdown report.

Usage: ``python scripts/features_benchmark.py [--sizes 200x24x4,2000x24x4] [--engines reference,jvm,spark,arrow]
[--families sequence,trend,all] [--single-closing] [--budget SECONDS] [--output report.md]``. The Spark engines run on
the context selected the usual way (``BRICKSMITH_*`` variables in the process environment, or in an optional ``.env``
file; see the test setup guide); ``--engines reference`` needs no workspace. Progress goes to stderr and the report to
stdout. ``--budget`` stops cleanly between measurements and labels the report as partial; ``--single-closing`` times
the last closing of each size instead of every closing.
"""

from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path

from bricksmith.features.benchmark import BENCH_ENGINES, REFERENCE, Budget, Size, render_report, run_benchmark
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
    parser.add_argument("--single-closing", action="store_true", help="time the last closing of each size only")
    parser.add_argument("--budget", type=float, help="stop cleanly between measurements after this many seconds")
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
    budget = Budget(args.budget) if args.budget else None
    timings = run_benchmark(
        spark,
        sizes,
        engines,
        _split(args.families),
        single_closing=args.single_closing,
        budget=budget,
        progress=lambda line: print(line, file=sys.stderr, flush=True),
    )
    notes = [f"python {platform.python_version()}", f"engines: {', '.join(engines)}"]
    if args.single_closing:
        notes.append("mode: single closing (the last closing of each size)")
    if budget and (partial := budget.note()):
        notes += [partial, f"not run: {', '.join(budget.skipped)}"]
    report = render_report(timings, notes)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
