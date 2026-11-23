"""The `config` pillar's own entry point — views over the configuration layers, and the seed.

`export` prints the repository layer through the active provider, `init` seeds the file and `check` lists what it lacks.
`schema` prints the key registry; `set` and `unset` are the only writers of one key in one layer. `show --effective` prints every key with
its effective value and the layer that supplied it (`default`, `user`, `repository`, `branch`)."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys

from quenching.common.front import resolve_root
from quenching.common.config import (CONFIG_FILE, LOCAL_CONFIG_FILE, config_path,
                                      DEFAULT_PROVIDER, PROVIDER_ENV, active_provider,
                                      find_repo_root, local_config_path, repository_document)
from quenching.common.git import _git
from quenching.common.io import write_text
from quenching.common.output import FINDINGS, OK, REFUSAL, NoAbbrevParser, emit, refuse
from quenching.common.version import VERSION
from quenching.config.effective import effective
from quenching.config.host import build_plan, load_settings
from quenching.config.schema import REGISTRY, schema as key_schema, unknown_leaves, validate
from quenching.config.seed import (missing_keys, read_repository, seed_document, set_leaf,
                                   unset_leaf)


def _render(payload: dict) -> str:
    lines = ["layers (low to high precedence):"]
    for layer in payload["layers"]:
        state = ("unparseable: " + layer["unparseable"] if layer["unparseable"]
                 else "present" if layer["present"] else "absent")
        if layer.get("applies") is False:
            state = "not applied (backend is not git)"
        lines.append(f"  {layer['name']:<11} {layer['path'] or '-'}  [{state}]")
    lines.append("")
    width = max((len(row["key"]) for row in payload["values"]), default=0)
    for row in payload["values"]:
        lines.append(f"{row['key']:<{width}} = {json.dumps(row['value'], ensure_ascii=False)}"
                     f"  [{row['source']}]")
    if payload.get("warnings"):
        lines.extend(["", "warnings:", *(f"  {warning}" for warning in payload["warnings"])])
    return "\n".join(lines)


def cmd_show(args) -> int:
    if not args.effective:
        print("cq config show: --effective is required", file=sys.stderr)
        return 3
    payload = {"ok": True, **effective(resolve_root(args.root))}
    emit(args.json, payload, _render(payload))
    return 0


def cmd_host(args) -> int:
    settings, warnings = load_settings(resolve_root(args.root))
    payload = {"ok": True, **settings, "warnings": warnings}
    if args.plan:
        command = [c for c in args.command if c != "--"] if args.command else []
        try:
            steps, notes = build_plan(settings, os.path.basename(resolve_root(args.root)), command)
        except ValueError as exc:
            print(f"cq config host: {exc}", file=sys.stderr)
            return REFUSAL
        payload.update(steps=steps, notes=notes)
        text = "\n".join(f"{s['step']:<5} {shlex.join(s['argv'])}" for s in steps)
        text += "".join(f"\nnote: {n}" for n in notes)
    else:
        text = "\n".join(f"{k} = {json.dumps(v, ensure_ascii=False)}"
                         for k, v in settings.items())
    text += "".join(f"\nwarning: {w}" for w in warnings)
    emit(args.json, payload, text)
    return 0


def _file_only(verb: str, args) -> int | None:
    """Refuse a file write or audit while another provider answers: the service would not get it."""
    provider, refusal = active_provider()
    if provider is None:
        return refuse(refusal, args.json)
    if provider.name == DEFAULT_PROVIDER:
        return None
    return refuse({
        "code": "cq-config-provider-not-file", "provider": provider.name,
        "message": f"cq config {verb} works on {CONFIG_FILE}, and {PROVIDER_ENV}={provider.name} is active",
        "remedy": f"unset {PROVIDER_ENV} to use the file, or change the configuration through {provider.name}",
    }, args.json)


def cmd_export(args) -> int:
    document, info, refusal = repository_document(resolve_root(args.root))
    if refusal:
        return refuse(refusal, args.json)
    if document is None:
        state = "is unparseable: " + info["unparseable"] if info["unparseable"] else "holds no document"
        return refuse({
            "code": "cq-config-unparseable" if info["unparseable"] else "cq-config-absent",
            "provider": info["name"], "config": info["path"],
            "message": f"provider {info['name']} {state}",
            "remedy": "fix or create the repository configuration before exporting it",
        }, args.json)
    emit(args.json, {"ok": True, "provider": info["name"], "path": info["path"],
                     "document": document},
         json.dumps(document, indent=2, ensure_ascii=False))
    return OK


def cmd_init(args) -> int:
    if (refused := _file_only("init", args)) is not None:
        return refused
    root = resolve_root(args.root)
    path = config_path(find_repo_root(root))
    if os.path.isfile(path):
        return refuse({
            "code": "cq-config-present", "config": path,
            "message": f"{CONFIG_FILE} already exists; init will not overwrite it",
            "remedy": "run `cq config check` to list the seeded keys it lacks, and add them by hand",
        }, args.json)
    document = seed_document(root)
    if args.write:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_text(path, json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    action = "written" if args.write else "preview"
    human = (f"cq config init — {path}\n"
             + json.dumps(document, indent=2, ensure_ascii=False)
             + f"\n{action}: {'yes' if args.write else 'no — rerun with `--write` to create it'}")
    emit(args.json, {"ok": True, "action": f"init-{action}", "path": path,
                     "document": document, "written": args.write}, human)
    return OK


def cmd_check(args) -> int:
    if (refused := _file_only("check", args)) is not None:
        return refused
    root = resolve_root(args.root)
    path = config_path(find_repo_root(root))
    if not os.path.isfile(path):
        return refuse({
            "code": "cq-config-absent", "config": path,
            "message": f"{CONFIG_FILE} does not exist",
            "remedy": "run `cq config init --write` to seed it",
        }, args.json)
    document = read_repository(root)
    if document is None:
        return refuse({
            "code": "cq-config-unparseable", "config": path,
            "message": f"{CONFIG_FILE} is not a JSON object",
            "remedy": "fix the file by hand; check reads only a valid object",
        }, args.json)
    missing = missing_keys(document, root)
    unknown = unknown_leaves(document)
    human = ("seeded keys missing from the file:\n" + "\n".join(f"  {k}" for k in missing)
             if missing else "every seeded key is declared")
    if unknown:
        human += ("\nkeys nothing reads (a typo yields the default):\n"
                  + "\n".join(f"  {k}" for k in unknown))
    found = bool(missing or unknown)
    emit(args.json, {"ok": not found, "path": path, "missing": missing, "unknown": unknown}, human)
    return FINDINGS if found else OK


def _exclude_local(repo: str) -> str | None:
    """Add the local layer to `.git/info/exclude` once; the path written, or None outside git."""
    common = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    if not common:
        return None
    path = os.path.join(common, "info", "exclude")
    line = "/" + LOCAL_CONFIG_FILE.replace(os.sep, "/")
    try:
        with open(path, encoding="utf-8") as fh:
            current = fh.read()
    except FileNotFoundError:
        current = ""
    if line in current.splitlines():
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(("\n" if current and not current.endswith("\n") else "")
                 + f"# quenching: this checkout's local configuration layer\n{line}\n")
    return path


def _layer_document(path: str) -> tuple[dict | None, str | None]:
    if not os.path.isfile(path):
        return {}, None
    try:
        with open(path, encoding="utf-8") as fh:
            document = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)
    return (document, None) if isinstance(document, dict) else (None, "not a JSON object")


def _parse_value(raw: str):
    """A JSON value when it parses (`6`, `true`, `["a"]`), else the text itself."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def cmd_set(args) -> int:
    """Write one key into one layer, validated against the registry; nothing else moves."""
    unset = args.cmd == "unset"
    spec = REGISTRY.get(args.key)
    if spec is None:
        return refuse({"code": "cq-config-key-unknown", "key": args.key,
                       "message": f"{args.key} is not a configuration key",
                       "remedy": "`cq config schema` lists every key"}, args.json)
    if args.layer == "project" and (refused := _file_only(args.cmd, args)) is not None:
        return refused
    if args.layer == "local" and spec["scope"] == "project":
        return refuse({"code": "cq-config-scope-project", "key": args.key,
                       "message": f"{args.key} is a project key: the team's contract, which the "
                                  "local layer cannot set",
                       "remedy": "write it with --layer project"}, args.json)
    value = None
    if not unset:
        value, why = validate(args.key, _parse_value(args.value))
        if why:
            return refuse({"code": "cq-config-value-invalid", "key": args.key,
                           "message": why}, args.json)
    repo = find_repo_root(resolve_root(args.root))
    path = local_config_path(repo) if args.layer == "local" else config_path(repo)
    document, broken = _layer_document(path)
    if document is None:
        return refuse({"code": "cq-config-unparseable", "config": path,
                       "message": f"{path} is not a JSON object: {broken}",
                       "remedy": "fix the file by hand; set never rewrites a file it cannot read"},
                      args.json)
    if unset:
        changed, previous = unset_leaf(document, args.key)
    else:
        previous = set_leaf(document, args.key, value)
        changed = previous != value
    excluded = None
    if changed:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_text(path, json.dumps(document, indent=2, ensure_ascii=False) + "\n")
        if args.layer == "local":
            excluded = _exclude_local(repo)
    payload = {"ok": True, "key": args.key, "layer": args.layer, "path": path,
               "action": "unset" if unset else "set", "changed": changed,
               "previous": previous, "value": value, "excluded": excluded}
    verb = "removed from" if unset else "set in"
    human = (f"{args.key} {verb} the {args.layer} layer ({path})" if changed
             else f"{args.key}: nothing to change in the {args.layer} layer")
    if excluded:
        human += f"\n{LOCAL_CONFIG_FILE} added to {excluded}"
    emit(args.json, payload, human)
    return OK


def cmd_schema(args) -> int:
    payload = {"ok": True, **key_schema()}
    if args.values:
        payload["values"] = {row["key"]: {"value": row["value"], "source": row["source"]}
                             for row in effective(resolve_root(args.root))["values"]}
        repo = find_repo_root(resolve_root(args.root))
        payload["layerPaths"] = {"project": config_path(repo), "local": local_config_path(repo)}
    lines = []
    for cat in payload["categories"]:
        lines.append(cat["title"]["en"])
        for row in (k for k in payload["keys"] if k["category"] == cat["id"]):
            lock = "" if row["editable"] else "  (read-only)"
            lines.append(f"  {row['key']:<44} {row['type']:<8} {row['scope']:<8}"
                         f" default={json.dumps(row['default'], ensure_ascii=False)}{lock}")
    emit(args.json, payload, "\n".join(lines))
    return OK


def build_parser() -> argparse.ArgumentParser:
    p = NoAbbrevParser(prog="cq config", description="the configuration layers: read them, and write one key at a time")
    p.add_argument("--version", action="store_true", help="print the version and exit")
    p.add_argument("--root", help="repository root (default: current directory)")
    sub = p.add_subparsers(dest="cmd")
    sp = sub.add_parser("show", help="print the effective configuration with each value's layer")
    sp.add_argument("--effective", action="store_true",
                    help="every key with its effective value and source layer")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    hp = sub.add_parser("host", help="print the work host (`shared.workHost`) and its dispatch plan")
    hp.add_argument("--plan", action="store_true",
                    help="print the dispatch steps for the command after `--`; runs nothing")
    hp.add_argument("--json", action="store_true", help="machine-readable output")
    hp.add_argument("command", nargs="*", help="the command to dispatch, after `--`")
    xp = sub.add_parser("export", help="print the repository layer as the envelope a service would hold")
    xp.add_argument("--json", action="store_true", help="machine-readable output")
    ip = sub.add_parser("init", help="seed .agents/quenching.json with every adjustable default")
    ip.add_argument("--write", action="store_true", help="create the file (default: preview)")
    ip.add_argument("--json", action="store_true", help="machine-readable output")
    sc = sub.add_parser("schema", help="every key with its type, default, category, scope and "
                                       "copy (English and pt-BR)")
    sc.add_argument("--values", action="store_true",
                    help="add each key's effective value and the layer that supplied it")
    sc.add_argument("--json", action="store_true", help="machine-readable output")
    for verb, text in (("set", "write one key into one layer, validated; nothing else moves"),
                       ("unset", "remove one key from one layer, so the next layer down governs")):
        vp = sub.add_parser(verb, help=text)
        vp.add_argument("key", help="the dotted key, as `cq config schema` names it")
        if verb == "set":
            vp.add_argument("value", help="a JSON value (6, true, [\"a\"]) or plain text")
        vp.add_argument("--layer", choices=("project", "local"), required=True,
                        help="project: .agents/quenching.json (the team's contract); local: "
                             ".agents/quenching.local.json (this checkout, kept out of git)")
        vp.add_argument("--json", action="store_true", help="machine-readable output")
    kp = sub.add_parser("check", help="list the seeded keys the file lacks")
    kp.add_argument("--json", action="store_true", help="machine-readable output")
    return p


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print(f"cq config {VERSION}")
        return 0
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_usage(sys.stderr)
        return REFUSAL
    return {"host": cmd_host, "export": cmd_export, "init": cmd_init, "check": cmd_check,
            "schema": cmd_schema, "set": cmd_set, "unset": cmd_set}.get(args.cmd, cmd_show)(args)
