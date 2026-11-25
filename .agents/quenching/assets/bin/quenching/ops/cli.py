"""The `cq ops` argument parser and its three read-only subcommands."""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from quenching.common.front import build_parser as build_front_parser, resolve_root
from quenching.common.output import emit, refuse
from quenching.common.version import VERSION
from quenching.ops.config import load_ops_config
from quenching.ops.doctor import doctor as run_doctor, inspect_ops
from quenching.ops.inventory import build_inventory
from quenching.ops.registry import (
    RegistryError, inventory_digest, read_registry, render_registry_document, write_registry,
)


def build_parser() -> argparse.ArgumentParser:
    parser = build_front_parser(
        "cq ops", "read and verify a repository's declared operations surface",
        (("inventory", "walk the declared operations tree and print its read model"),
         ("doctor", "run the static operations checks"),
         ("status", "summarize packages, lifecycle, router, and findings")),
    )
    parser.add_argument("--version", action="store_true", help="print the ops pillar version")
    sub = next(action for action in parser._actions
               if isinstance(action, argparse._SubParsersAction))
    registry = sub.add_parser("registry", help="write or check the generated operations registry")
    mode = registry.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="update the generated registry")
    mode.add_argument("--check", action="store_true", help="check without writing (the default)")
    registry.add_argument("--json", action="store_true", help="print machine-readable output")
    return parser


def _root(value: str | None) -> str:
    return resolve_root(value)


def _human_inventory(payload: dict) -> str:
    router = payload["router"]
    return (f"ops inventory — {payload['root']}\n"
            f"  entry points: {len(payload['entryPoints'])}\n"
            f"  router: {router['path']} ({router['kind'] or 'unsupported'})")


def _human_doctor(payload: dict) -> str:
    lines = [f"ops doctor — {payload['root']} ({len(payload['findings'])} finding(s))"]
    for finding in payload["findings"]:
        lines.append(f"  [{finding['severity']:<5}] {finding.get('path', '-')} — "
                     f"{finding['message']} ({finding['code']})")
    if not payload["findings"]:
        lines.append("  OK — no findings.")
    return "\n".join(lines)


def _status_payload(payload: dict) -> dict:
    entries = payload["entryPoints"]
    packages = Counter(
        entry["path"].split("/", 1)[0] if "/" in entry["path"] else "."
        for entry in entries
    )
    lifecycle = Counter(entry["lifecycle"] or "undeclared" for entry in entries)
    finding_tally = Counter(finding["code"] for finding in payload["findings"])
    router = payload["router"]
    return {
        "root": payload["root"],
        "scanned": payload["scanned"],
        "packages": dict(sorted(packages.items())),
        "router": {
            "path": router["path"],
            "kind": router["kind"],
            "exists": router["exists"],
            "health": "ok" if router["exists"] and router["kind"] else "missing",
        },
        "lifecycle": dict(sorted(lifecycle.items())),
        "findings": dict(sorted(finding_tally.items())),
        "errors": payload["errors"],
        "warnings": payload["warnings"],
        "ok": payload["ok"],
    }


def _human_status(payload: dict) -> str:
    router = payload["router"]
    return (f"ops status — {payload['root']}\n"
            f"  packages: {', '.join(f'{k}={v}' for k, v in payload['packages'].items()) or '(none)'}\n"
            f"  router: {router['path']} ({router['health']})\n"
            f"  lifecycle: {', '.join(f'{k}={v}' for k, v in payload['lifecycle'].items()) or '(none)'}\n"
            f"  findings: {', '.join(f'{k}={v}' for k, v in payload['findings'].items()) or '(none)'}")


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print(f"cq ops {VERSION}")
        return 0
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = bool(getattr(args, "json", False))
    if not args.cmd:
        return refuse({"code": "op-no-command", "message": "choose `inventory`, `doctor`, "
                       "`status`, or `registry`"}, as_json)
    root = _root(args.root)
    if args.cmd == "registry":
        config, config_err = load_ops_config(root)
        if config_err:
            return refuse(config_err, as_json)
        assert config is not None
        inventory, inventory_err = build_inventory(root)
        if inventory_err:
            return refuse(inventory_err, as_json)
        assert inventory is not None
        path = Path(config["registry"])
        try:
            current = read_registry(path)
            expected = render_registry_document(current, inventory)
            changed = current != expected
            if args.write:
                changed = write_registry(path, inventory)
        except RegistryError as exc:
            return refuse({"code": "op-registry-malformed", "message": str(exc),
                           "path": str(path)}, as_json)
        if args.write:
            payload = {"ok": True, "mode": "write", "path": str(path), "changed": changed,
                       "digest": inventory_digest(inventory)}
            emit(as_json, payload, f"ops registry — {'written' if changed else 'unchanged'}")
            return 0
        payload = {"root": root, "path": str(path), "changed": changed,
                   "digest": inventory_digest(inventory)}
        if as_json:
            emit(as_json, {"ok": not changed, **payload}, "")
        else:
            emit(False, payload, f"ops registry — {'stale' if changed else 'fresh'}")
        return 1 if changed else 0
    if args.cmd == "inventory":
        payload, err = build_inventory(root)
        if err:
            return refuse(err, as_json)
        assert payload is not None
        emit(as_json, payload.as_dict(), _human_inventory(payload.as_dict()))
        return 0
    if args.cmd == "doctor":
        payload, err, code = run_doctor(root)
        if err:
            return refuse(err, as_json)
        assert payload is not None
        emit(as_json, payload, _human_doctor(payload))
        return code
    payload, err = inspect_ops(root)
    if err:
        return refuse(err, as_json)
    assert payload is not None
    summary = _status_payload(payload)
    emit(as_json, summary, _human_status(summary))
    return 0 if summary["ok"] else 1
