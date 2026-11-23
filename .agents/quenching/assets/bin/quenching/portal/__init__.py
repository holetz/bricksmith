"""The `portal` pillar — the project registry, written only here, and the central server over it."""
from __future__ import annotations

import argparse

from quenching.common.output import NoAbbrevParser, emit, refuse
from quenching.common.version import VERSION
from quenching.portal import registry
from quenching.portal.registry import PortalRefusal


def build_parser() -> argparse.ArgumentParser:
    p = NoAbbrevParser(prog="cq portal",
                       description="register the projects the quenching portal shows, and serve them")
    p.add_argument("--version", action="store_true", help="print the version and exit")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("add", help="register the repository holding a folder")
    sp.add_argument("folder", nargs="?", help="a folder inside the repository")
    sp.add_argument("--name", help="display name (default: the repository folder's name)")
    sp.add_argument("--from-orca", nargs="?", const="", metavar="DIR",
                    help="one-time import of orca's project paths (default dir: ~/.config/orca)")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("remove", help="unregister a project by folder or name")
    sp.add_argument("target")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("list", help="print the registered projects")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("serve", help="one loopback portal over every registered project")
    sp.add_argument("--port", type=int, default=0, help="port (default: a free one)")
    sp.add_argument("--host", default="127.0.0.1", help="loopback address only")
    sp.add_argument("--read-only", action="store_true", dest="read_only",
                    help="disable every write route on the server")
    sp.add_argument("--open", action="store_true", help="open the URL in the browser")
    sp.add_argument("--sessions", action="store_true",
                    help="serve GET /api/sessions per project: the live Claude Code sessions of "
                         "its worktrees, fixed fields only (off by default)")
    sp.add_argument("--chat", action="store_true",
                    help="serve POST /api/chat: questions answered by a read-only `claude -p` "
                         "(sonnet, Read/Grep/Glob only) in a worktree, polled (off by default)")
    sp.add_argument("--chat-budget-usd", type=float, default=None, dest="chat_budget_usd",
                    help="the dollar ceiling of one chat question (default: 0.50)")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    return p


def _payload(**extra) -> dict:
    return {"ok": True, "registry": str(registry.registry_path()), **extra}


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print(f"cq portal {VERSION}")
        return 0
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.error("choose add, remove, list or serve")
    if args.cmd == "serve":
        from quenching.portal.serve import cmd_serve
        return cmd_serve(args)
    as_json = bool(getattr(args, "json", False))
    try:
        if args.cmd == "add":
            if args.from_orca is not None:
                if args.folder:
                    parser.error("--from-orca takes no folder")
                result = registry.import_orca(args.from_orca or None)
                emit(as_json, _payload(**result),
                     f"orca import: {len(result['added'])} added, "
                     f"{len(result['unchanged'])} unchanged, {len(result['skipped'])} skipped")
            else:
                if not args.folder:
                    parser.error("add needs a folder or --from-orca")
                result = registry.add(args.folder, args.name)
                project = result["project"]
                emit(as_json, _payload(**result),
                     f"{'added' if result['added'] else 'already registered'}: "
                     f"{project['name']} — {project['path']}")
        elif args.cmd == "remove":
            result = registry.remove(args.target)
            emit(as_json, _payload(**result), f"removed: {result['removed']['name']}")
        else:
            projects = registry.list_projects()
            human = "\n".join(f"{p['name']}\t{p['path']}" for p in projects) or "no projects registered"
            emit(as_json, _payload(projects=projects), human)
    except PortalRefusal as exc:
        return refuse({"code": exc.code, "exit": 2, "message": exc.message}, as_json)
    return 0
