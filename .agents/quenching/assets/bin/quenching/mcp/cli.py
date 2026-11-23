"""`cq mcp` — the whole `quenching` MCP server over stdio.

    cq [--root PATH] mcp

The server is the references resources plus the catalog of CLI verbs (with the specs tools when
the `specs` pillar is present). The workspace is the cwd it is launched in, or `--root`; it is
never a tool argument. `cq specs mcp` stays the specs-only mount of the same core.
"""
from __future__ import annotations

import os

from quenching.common.output import CQArgumentParser
from quenching.mcp import catalog, resources, server


def build_parser() -> CQArgumentParser:
    parser = CQArgumentParser(
        prog="cq mcp",
        description="serve the quenching MCP server over stdio: the catalogued verbs as tools "
                    "and every skill's references as quenching://references/ resources")
    parser.add_argument("--root", help="the workspace the tools run in (default: current directory)")
    return parser


def surface(present=None) -> server.Composite:
    return server.Composite("quenching", [resources.ReferencesSurface(), catalog.surface(present)])


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return server.run_stdio(os.path.abspath(args.root or os.getcwd()), surface())
