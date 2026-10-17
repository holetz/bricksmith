"""The ``catalog`` command group: ``catalog render``; offline, never imports task modules."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from bricksmith.cli.common import global_parent
from bricksmith.errors import EXIT_OK, ConfigError, UsageError

if TYPE_CHECKING:
    import argparse

# Exemption expiry is a gate concern; pinning the date keeps catalog bytes independent of the clock.
_CATALOG_DATE = date.min


def _render(args: argparse.Namespace) -> int:
    from bricksmith.catalogdocs import build_catalog, to_json, to_markdown  # noqa: PLC0415 - keeps parser light
    from bricksmith.graph import load_graph  # noqa: PLC0415
    from bricksmith.registry import load_registry  # noqa: PLC0415

    config = Path(args.project) / "bricksmith.toml"
    if not config.is_file():
        msg = f"{config} not found: --project must be a project root holding bricksmith.toml"
        raise ConfigError(msg)
    registry = load_registry(args.registry) if args.registry else None
    report = build_catalog(load_graph(args.project, today=_CATALOG_DATE), registry)
    text = to_json(report) if args.format == "json" or args.json else to_markdown(report)
    if args.output:
        try:
            Path(args.output).write_text(text, encoding="utf-8", newline="\n")
        except OSError as error:
            msg = f"cannot write {args.output}: {error}"
            raise UsageError(msg) from None
    else:
        sys.stdout.write(text)
    return EXIT_OK


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``catalog render``."""
    group = subparsers.add_parser("catalog", help="offline data and model catalog", parents=[global_parent()])
    commands = group.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render", help="render the catalog as Markdown or JSON", parents=[global_parent()])
    render.add_argument("--project", default=".", help="project root holding bricksmith.toml (default: .)")
    render.add_argument("--registry", help="column registry TOML; absent means incomplete metadata")
    render.add_argument("--format", choices=("markdown", "json"), default="markdown")
    render.add_argument("--output", help="write to this file instead of stdout")
    render.set_defaults(handler=_render)
