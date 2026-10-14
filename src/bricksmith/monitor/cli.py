"""The ``monitor`` command group: ``fit``, ``compare`` and ``evaluate`` (text or ``--json``)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.cli.common import context_from_args, emit, global_parent
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION, UsageError
from bricksmith.monitor.adapter import MonitorReport, fit_reference, measure, monitor_frames
from bricksmith.monitor.reference import Reference, resolve_window

if TYPE_CHECKING:
    import argparse


def _scalar(text: str) -> object:
    if text == "":
        return None
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            continue
    return text


def load_rows(path: str) -> list[dict[str, Any]]:
    """Read a local ``.csv`` (empty cell is null, numbers parsed) or ``.jsonl`` file into rows."""
    file = Path(path)
    if not file.is_file():
        msg = f"data file not found: {path}"
        raise UsageError(msg)
    if file.suffix == ".jsonl":
        return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if file.suffix == ".csv":
        with file.open(encoding="utf-8", newline="") as handle:
            return [{k: _scalar(v) for k, v in row.items()} for row in csv.DictReader(handle)]
    msg = "data must be a .csv or .jsonl file (or use --table for a Spark table)"
    raise UsageError(msg)


def _data(args: argparse.Namespace) -> Any:  # noqa: ANN401 - rows or DataFrame
    if bool(args.data) == bool(args.table):
        msg = "pass exactly one of --data (csv/jsonl) or --table"
        raise UsageError(msg)
    if args.data:
        return load_rows(args.data)
    from bricksmith.clients import Clients  # noqa: PLC0415 - heavy integration, loaded where used

    return Clients(context_from_args(args)).spark().table(args.table)


def _json(path: str) -> Any:  # noqa: ANN401
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        msg = f"cannot read JSON file {path}: {error}"
        raise UsageError(msg) from None


def _fit(args: argparse.Namespace) -> int:
    config = dict(_json(args.config))
    try:
        reference = fit_reference(
            _data(args),
            config.pop("columns"),
            **{"fit_source": args.table or Path(args.data).name, **config},
        )
    except KeyError as error:
        msg = f"fit config is missing {error}"
        raise UsageError(msg) from None
    if args.out:
        Path(args.out).write_text(reference.to_json() + "\n", encoding="utf-8")
    emit(
        args,
        reference.to_dict(),
        f"reference {reference.name} v{reference.version} {reference.fingerprint[:12]}"
        + (f" -> {args.out}" if args.out else ""),
    )
    return EXIT_OK


def _window(args: argparse.Namespace, reference: Reference) -> dict[str, str]:
    if args.window_start or args.window_end:
        column = args.window_column or reference.time
        if not (args.window_start and args.window_end and column):
            msg = "a window needs --window-start, --window-end and a column (--window-column or the reference's time)"
            raise UsageError(msg)
        return {"column": column, "end": args.window_end, "start": args.window_start}
    return resolve_window(reference, args.as_of)


def _history(path: str | None) -> dict[tuple[str, str, str], list[float]]:
    history: dict[tuple[str, str, str], list[float]] = {}
    for row in _json(path) if path else []:
        history.setdefault((row["metric"], row.get("subject", ""), row.get("segment", "")), []).append(
            float(row["value"])
        )
    return history


def _report(args: argparse.Namespace, *, rules: bool) -> MonitorReport:
    reference = Reference.from_json(Path(args.reference).read_text(encoding="utf-8"))
    window = _window(args, reference)
    if rules:
        return monitor_frames(
            _data(args),
            reference=reference,
            window=window,
            history=_history(args.history),
            segments=not args.no_segments,
        )
    measurements = measure(reference, _data(args), window, segments=not args.no_segments)
    return MonitorReport(reference, tuple(measurements), (), ())


def _compare(args: argparse.Namespace) -> int:
    report = _report(args, rules=False)
    emit(args, report.to_dict(), report.to_text())
    return EXIT_OK


def _evaluate(args: argparse.Namespace) -> int:
    report = _report(args, rules=True)
    emit(args, report.to_dict(), report.to_text())
    return EXIT_VALIDATION if args.fail_on_alert and report.alerts else EXIT_OK


def _source(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data", help="local .csv or .jsonl file")
    parser.add_argument("--table", help="Spark table (uses the explicit context)")


def _compare_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reference", required=True, help="reference JSON written by `monitor fit`")
    _source(parser)
    parser.add_argument("--window-start", help="inclusive ISO start (default: the reference's evaluation window)")
    parser.add_argument("--window-end", help="exclusive ISO end")
    parser.add_argument("--window-column", help="window column (default: the reference's time column)")
    parser.add_argument("--as-of", help="ISO date: end of a relative evaluation window")
    parser.add_argument("--no-segments", action="store_true", help="skip per-segment measurements")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``monitor fit|compare|evaluate``."""
    group = subparsers.add_parser(
        "monitor", help="fit references, compare windows and evaluate alert rules", parents=[global_parent()]
    )
    commands = group.add_subparsers(dest="command", required=True)
    fit = commands.add_parser("fit", help="fit a versioned reference from data", parents=[global_parent()])
    fit.add_argument(
        "--config", required=True, help="JSON: name, version, columns, score, label, segment, time, alert_policy..."
    )
    fit.add_argument("--out", help="write the reference JSON here")
    _source(fit)
    fit.set_defaults(handler=_fit)
    compare = commands.add_parser("compare", help="measure a window against a reference", parents=[global_parent()])
    _compare_options(compare)
    compare.set_defaults(handler=_compare)
    evaluate = commands.add_parser(
        "evaluate", help="measure and evaluate the reference's alert rules", parents=[global_parent()]
    )
    _compare_options(evaluate)
    evaluate.add_argument("--history", help="JSON list of prior {metric, subject, segment, value} measurements")
    evaluate.add_argument("--fail-on-alert", action="store_true", help="exit 4 when any alert fires")
    evaluate.set_defaults(handler=_evaluate)


__all__ = ["load_rows", "register"]
