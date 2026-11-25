"""The ``cq delivery`` route and its read-only floor verbs."""
from __future__ import annotations

import argparse

from quenching.common.front import build_parser as build_front_parser, resolve_root
from quenching.common.output import emit, refuse
from quenching.common.version import VERSION
from quenching.controls.evaluate import owner_controls, summary_line
from quenching.delivery.doctor import doctor, inspect_delivery
from quenching.delivery.inventory import build_inventory


def build_parser() -> argparse.ArgumentParser:
    parser = build_front_parser(
        "cq delivery", "probe and report a repository's delivery workflow surface",
        (("inventory", "read the provider or provider-equivalent workflow tree"),
         ("doctor", "probe applicability and report delivery findings"),
         ("status", "read the delivery applicability state")),
    )
    parser.add_argument("--version", action="store_true", help="print the delivery version")
    return parser


def _root(value: str | None) -> str:
    return resolve_root(value)


def _human_inventory(payload: dict) -> str:
    state = payload["applicability"]["state"]
    return f"delivery inventory — {payload['repoRoot']} ({state}); workflows: {len(payload['workflows'])}"


def _human_doctor(payload: dict) -> str:
    state = payload["applicability"]["state"]
    return f"delivery doctor — {payload['root']} ({state})"


def _status_payload(payload: dict) -> dict:
    return {
        "root": payload["root"],
        "applicability": payload["applicability"],
        "inventory": payload["inventory"],
        "findings": payload["findings"],
        "ok": payload["ok"],
        "controls": owner_controls("delivery", payload["root"]),
    }


def _human_status(payload: dict) -> str:
    return (f"delivery status — {payload['root']} ({payload['applicability']['state']}); "
            f"{summary_line(payload['controls'])}")


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print(f"cq delivery {VERSION}")
        return 0
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = bool(getattr(args, "json", False))
    if not args.cmd:
        return refuse({"code": "delivery-no-command", "message":
                       "choose `inventory`, `doctor`, or `status`"}, as_json)
    root = _root(args.root)
    if args.cmd == "inventory":
        inventory, error = build_inventory(root)
        if error:
            return refuse(error, as_json)
        if inventory is None:
            payload = {
                "repoRoot": root,
                "applicability": {"state": "not-applicable", "signal": None,
                                   "provider": None, "artifacts": []},
                "workflows": [],
            }
        else:
            payload = inventory.as_dict()
        emit(as_json, payload, _human_inventory(payload))
        return 0
    if args.cmd == "doctor":
        payload, error, code = doctor(root)
        if error:
            return refuse(error, as_json)
        assert payload is not None
        emit(as_json, payload, _human_doctor(payload))
        return code
    payload, error = inspect_delivery(root)
    if error:
        return refuse(error, as_json)
    assert payload is not None
    summary = _status_payload(payload)
    emit(as_json, summary, _human_status(summary))
    return 0
