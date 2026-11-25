"""The `cq proof` parser: inventory, doctor, ratchet and status."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from quenching.common.front import build_parser as build_front_parser, resolve_root
from quenching.common.output import emit, refuse
from quenching.common.version import VERSION
from quenching.controls.evaluate import owner_controls, summary_line
from quenching.proof.doctor import doctor as run_doctor, inspect_proof
from quenching.proof.inventory import build_inventory
from quenching.proof.config import load_proof_config
from quenching.proof.readme import readme_digest, readme_path, render_readme_document, write_readme
from quenching.proof.ratchet import evaluate


def build_parser() -> argparse.ArgumentParser:
    parser = build_front_parser(
        "cq proof", "read and verify a repository's declared proof surface",
        (("inventory", "walk the proof tree and print its read model"),
         ("doctor", "run static proof checks without running the suite"),
         ("status", "summarize layers, fixtures, floors and findings")),
    )
    parser.add_argument("--version", action="store_true", help="print the proof pillar version")
    sub = next(action for action in parser._actions
               if isinstance(action, argparse._SubParsersAction))
    readme = sub.add_parser("readme", help="write or check the generated proof README")
    mode = readme.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="update the generated block")
    mode.add_argument("--check", action="store_true", help="check without writing (the default)")
    readme.add_argument("--json", action="store_true", help="print machine-readable output")
    ratchet = sub.add_parser("ratchet", help="check or raise coverage floors from an existing artifact")
    mode = ratchet.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="check without writing (the default)")
    mode.add_argument("--raise", dest="raise_floor", action="store_true",
                      help="raise floors to achieved coverage")
    ratchet.add_argument("--artifact", help="coverage.json or .coverage path")
    ratchet.add_argument("--json", action="store_true", help="print machine-readable output")
    return parser


def _root(value: str | None) -> str:
    return resolve_root(value)


def _human_inventory(payload: dict) -> str:
    return (f"proof inventory — {payload['root']}\n"
            f"  test modules: {len(payload['testModules'])}\n"
            f"  layers: {len(payload['layers'])}\n"
            f"  CI definitions: {len(payload['ci'])}")


def _human_doctor(payload: dict) -> str:
    lines = [f"proof doctor — {payload['root']} ({len(payload['findings'])} finding(s))"]
    for finding in payload["findings"]:
        lines.append(f"  [{finding['severity']:<5}] {finding.get('path', '-')} — "
                     f"{finding['message']} ({finding['code']})")
    if not payload["findings"]:
        lines.append("  OK — no findings; the suite was not run.")
    return "\n".join(lines)


def _human_ratchet(payload: dict) -> str:
    if "findings" not in payload:
        return f"proof ratchet — refused: {payload['message']} ({payload['code']})"
    findings = payload["findings"]
    if payload["mode"] == "check":
        verdict = "checked"
    else:
        verdict = "raised" if payload["written"] else "not raised — floor unchanged"
    lines = [f"proof ratchet — {verdict} ({len(findings)} finding(s))"]
    for finding in findings:
        lines.append(f"  [{finding['severity']:<5}] {finding.get('root', '-')} — "
                     f"{finding['message']} ({finding['code']})")
    if not findings:
        lines.append("  OK — every measured root meets its floor")
    return "\n".join(lines)


def _floor_values(path: str) -> dict[str, float]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    floors = payload.get("floors") if isinstance(payload, dict) else None
    return {str(key): float(value) for key, value in (floors or {}).items()
            if isinstance(key, str) and isinstance(value, (int, float))
            and not isinstance(value, bool)}


def _status_payload(payload: dict) -> dict:
    layers = {
        item["name"]: {
            "tests": sum(test["layer"] == item["name"] for test in payload["testModules"]),
            "budget": item["budget"],
            "required": item["required"],
        }
        for item in payload["layers"]
    }
    library = [item for item in payload["fixtures"] if item["library"]]
    floors = _floor_values(payload["gate"].get("ratchetPath", ""))
    return {
        "root": payload["root"],
        "layers": layers,
        "fixtureLibrary": {"fixtures": len(library), "healthy": bool(library)},
        "measuredRoots": [
            {"root": root, "floor": floors.get(root)}
            for root in payload["measuredRoots"]
        ],
        "ci": {"definitions": len(payload["ci"]),
               "runsGate": any(item["runsGate"] for item in payload["ci"])},
        "findings": dict(sorted(Counter(item["code"] for item in payload["findings"]).items())),
        "suiteRun": False,
        "ok": payload["ok"],
    }


def _human_status(payload: dict) -> str:
    return (f"proof status — {payload['root']}\n"
            f"  layers: {len(payload['layers'])}\n"
            f"  fixture library: {payload['fixtureLibrary']['fixtures']} fixture(s)\n"
            f"  findings: {', '.join(f'{key}={value}' for key, value in payload['findings'].items()) or '(none)'}\n"
            "  suite: not run\n"
            f"  {summary_line(payload['controls'])}")


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print(f"cq proof {VERSION}")
        return 0
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = bool(getattr(args, "json", False))
    if not args.cmd:
        return refuse({"code": "pf-no-command", "message":
                       "choose `inventory`, `doctor`, `ratchet`, or `status`"}, as_json)
    root = _root(args.root)
    if args.cmd == "inventory":
        payload, err = build_inventory(root)
        if err:
            return refuse(err, as_json)
        assert payload is not None
        data = payload.as_dict()
        emit(as_json, data, _human_inventory(data))
        return 0
    if args.cmd == "doctor":
        payload, err, code = run_doctor(root)
        if err:
            return refuse(err, as_json)
        assert payload is not None
        emit(as_json, payload, _human_doctor(payload))
        return code
    if args.cmd == "readme":
        inventory, err = build_inventory(root)
        if err:
            return refuse(err, as_json)
        assert inventory is not None
        path = readme_path(inventory)
        try:
            current = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            current = ""
        expected = render_readme_document(current, inventory)
        changed = current != expected
        if args.write:
            changed = write_readme(inventory)
            payload = {"ok": True, "mode": "write", "path": str(path), "changed": changed,
                       "digest": readme_digest(inventory)}
            emit(as_json, payload, f"proof readme — {'written' if changed else 'unchanged'}")
            return 0
        payload = {"ok": not changed, "mode": "check", "path": str(path), "changed": changed}
        emit(as_json, payload, "" if as_json else f"proof readme — {'stale' if changed else 'fresh'}")
        return 1 if changed else 0
    if args.cmd == "ratchet":
        config, err = load_proof_config(root)
        if err:
            return refuse(err, as_json)
        assert config is not None
        payload, code = evaluate(config, mode="raise" if args.raise_floor else "check",
                                 artifact=args.artifact)
        emit(as_json, payload, _human_ratchet(payload))
        return code
    payload, err = inspect_proof(root)
    if err:
        return refuse(err, as_json)
    assert payload is not None
    summary = _status_payload(payload)
    summary["controls"] = owner_controls("proof", root)
    emit(as_json, summary, _human_status(summary))
    return 0 if summary["ok"] else 1
