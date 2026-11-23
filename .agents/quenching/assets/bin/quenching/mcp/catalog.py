"""The `quenching-mcp` catalog: a closed list of CLI verbs, each one MCP tool.

Every row is `name -> (pillar, verb path, class)`. Nothing else is hand-written: the tool's
`inputSchema` is derived from the pillar's own `build_parser()`, walked along the verb path, so a
new flag on a catalogued verb shows up in the tool with no edit here, and a new verb shows up only
when it gets its row.

* Closed: the argv is built only from the schema's properties (`additionalProperties: false`), so
  no argument reaches a verb or a flag the row does not name. `--root` and `--json` are the
  server's, never the model's.
* Classes: `read` (`readOnlyHint`), `write` (changes the workspace or the specs store) and
  `external` (leaves the machine: `openWorldHint`, `destructiveHint` where the row says so). The
  class is what a sub-agent grant is given by.
* Absent pillar: a row whose pillar is not present in this process is not published at all.
* Dispatch: in-process, through the `cq` router's own `main`, with the workspace as cwd; a verb
  that declares `--json` answers with its payload, any other with `{ok, exit, text}`.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
from importlib.machinery import SourceFileLoader
from typing import NamedTuple

from quenching.mcp import server
from quenching.mcp.server import ToolArgs

ROUTER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "cq")
SERVER_OWNED = {"root", "json", "help", "version"}
# A pillar whose `--root` names something narrower than the workspace (`components`: the surface
# root holding commands/) gets no `--root`; the workspace reaches it as the cwd, from which its own
# default discovery (nearest `.claude/` upward) runs.
ROOT_BY_CWD = {"components"}
CLASSES = ("read", "write", "external")


class Row(NamedTuple):
    pillar: str
    path: tuple[str, ...]
    cls: str
    destructive: bool = False
    hidden: frozenset = frozenset()   # parser dests the tool never publishes


def _rows(pillar: str, cls: str, *paths: str) -> dict[str, Row]:
    return {"_".join((pillar, *p.split())).replace("-", "_"): Row(pillar, tuple(p.split()), cls)
            for p in paths}


CATALOG: dict[str, Row] = {
    **_rows("components", "read", "read", "lint", "doctor", "session list", "session digest",
            "session cost", "session cache", "session context", "session live"),
    **_rows("components", "write", "registry reindex"),
    **_rows("knowledge", "read", "validate", "status"),
    **_rows("knowledge", "write", "site-source", "project", "nav", "listing"),
    **_rows("git", "read", "base", "state", "conventions", "history log", "history show",
            "history diff", "audit", "stale", "preflight", "pr probe", "pr view"),
    **_rows("git", "write", "commit", "worktree link", "worktree add", "specs"),
    **_rows("git", "external", "push", "pr create", "pr record"),
    # `--spec` would stamp the `merge` record with no `specs_record` call behind it: the
    # record is written by `cq specs record` once the merge landed, not by the merge verb.
    "git_pr_merge": Row("git", ("pr", "merge"), "external", True, frozenset({"spec"})),
    "git_worktree_retire": Row("git", ("worktree", "retire"), "write", True),
    "git_prune": Row("git", ("prune",), "external", True),
    **_rows("config", "read", "show", "check", "host"),
    **_rows("config", "write", "init"),
    **_rows("ops", "read", "doctor", "status"),
    **_rows("ops", "write", "registry"),
    **_rows("proof", "read", "doctor", "status"),
    **_rows("proof", "write", "ratchet"),
    **_rows("delivery", "read", "doctor", "status"),
    **_rows("toolchain", "read", "doctor", "status"),
    **_rows("design", "read", "doctor", "status"),
    **_rows("design", "write", "build"),
    **_rows("security", "read", "audit", "status"),
    **_rows("opensource", "read", "doctor", "status", "readiness"),
    **_rows("opensource", "write", "apply"),
    **_rows("codex", "read", "eligible", "probe"),
}


_router = None


def router():
    """The `cq` router, loaded from its file: the one table of pillars and of what is present."""
    global _router
    if _router is None:
        loader = SourceFileLoader("quenching_cq_router", ROUTER_PATH)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        _router = importlib.util.module_from_spec(spec)
        loader.exec_module(_router)
    return _router


class Verb(NamedTuple):
    parser: argparse.ArgumentParser
    fixed: dict[str, str]        # positional dest -> the value the verb path fills in
    help: str
    hidden: frozenset = frozenset()


def _subparsers(parser: argparse.ArgumentParser):
    return next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)


def _pillar_parser(pillar: str) -> argparse.ArgumentParser:
    built = importlib.import_module(router().PILLARS[pillar].module).build_parser()
    return built[0] if isinstance(built, tuple) else built


def resolve(row: Row) -> Verb:
    """Walk the pillar's parser along the verb path. A path element no subparser names fills the
    first positional whose `choices` carry it (`git worktree add`, `security audit`)."""
    parser, fixed, help_text = _pillar_parser(row.pillar), {}, ""
    for word in row.path:
        subs = _subparsers(parser)
        if subs is not None and word in subs.choices:
            help_text = next((c.help for c in subs._choices_actions if c.dest == word), "") or ""
            parser = subs.choices[word]
            continue
        slot = next((a for a in parser._actions if not a.option_strings and a.dest not in fixed
                     and a.choices and word in a.choices), None)
        if slot is None:
            raise LookupError(f"{row.pillar} has no verb {' '.join(row.path)!r}")
        fixed[slot.dest] = word
    return Verb(parser, fixed, help_text or parser.description or "", row.hidden)


def _declares(parser: argparse.ArgumentParser, flag: str) -> bool:
    return any(flag in a.option_strings for a in parser._actions)


def _arguments(verb: Verb) -> list[argparse.Action]:
    return [a for a in verb.parser._actions
            if not isinstance(a, (argparse._HelpAction, argparse._VersionAction,
                                  argparse._SubParsersAction))
            and a.dest not in SERVER_OWNED and a.dest not in verb.fixed
            and a.dest not in verb.hidden]


def _scalar(action: argparse.Action) -> dict:
    if action.type is int:
        return {"type": "integer"}
    if action.type is float:
        return {"type": "number"}
    return {"type": "string"}


def _many(action: argparse.Action) -> bool:
    return isinstance(action, argparse._AppendAction) or action.nargs in ("+", "*")


def _property(action: argparse.Action) -> dict:
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        prop = {"type": "boolean"}
    else:
        item = _scalar(action)
        if action.choices:
            item = {**item, "enum": list(action.choices)}
        prop = {"type": "array", "items": item} if _many(action) else item
    if action.help and action.help is not argparse.SUPPRESS:
        prop["description"] = action.help % {"default": action.default} \
            if "%(default)" in action.help else action.help
    return prop


def _required(action: argparse.Action) -> bool:
    if action.option_strings:
        return bool(action.required)
    return action.nargs not in ("?", "*")


def schema(verb: Verb) -> dict:
    actions = _arguments(verb)
    out = {"type": "object", "properties": {a.dest: _property(a) for a in actions},
           "additionalProperties": False}
    required = [a.dest for a in actions if _required(a)]
    if required:
        out["required"] = required
    return out


ANNOTATIONS = {
    "read": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
             "openWorldHint": False},
    "write": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
              "openWorldHint": False},
    "external": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
                 "openWorldHint": True},
}


def annotations(row: Row) -> dict:
    return {**ANNOTATIONS[row.cls], **({"destructiveHint": True} if row.destructive else {})}


def available(present=None) -> dict[str, Row]:
    """The rows whose pillar is present in this process — an absent pillar publishes nothing."""
    present = present or router().present
    shown = {pillar: present(pillar) for pillar in {row.pillar for row in CATALOG.values()}}
    return {name: row for name, row in CATALOG.items() if shown[row.pillar]}


def describe(name: str, row: Row) -> dict:
    verb = resolve(row)
    head = f"`cq {row.pillar} {' '.join(row.path)}`"
    return {"name": name,
            "description": f"{head} — {verb.help} [class: {row.cls}]" if verb.help
            else f"{head} [class: {row.cls}]",
            "inputSchema": schema(verb), "annotations": annotations(row)}


def _check(action: argparse.Action, value):
    prop = _property(action)
    kind = prop["type"]
    values = value if kind == "array" else [value]
    item = prop.get("items", prop)
    if kind == "array" and (not isinstance(value, list) or not value):
        raise ToolArgs(f"`{action.dest}` must be a non-empty array")
    for one in values:
        if item["type"] == "boolean":
            ok = isinstance(one, bool)
        elif item["type"] == "integer":
            ok = isinstance(one, int) and not isinstance(one, bool)
        elif item["type"] == "number":
            ok = isinstance(one, (int, float)) and not isinstance(one, bool)
        else:
            # An option value may span lines (a PR body); a positional never does. NUL never.
            ok = isinstance(one, str) and "\0" not in one \
                and (bool(action.option_strings) or "\n" not in one)
        if not ok:
            raise ToolArgs(f"`{action.dest}` must be {item['type']}")
        if "enum" in item and one not in item["enum"]:
            raise ToolArgs(f"`{action.dest}` must be one of {item['enum']}")
        if not action.option_strings and isinstance(one, str) and one.startswith("-"):
            raise ToolArgs(f"`{action.dest}` cannot start with `-`")


def argv(row: Row, args: dict, root: str) -> tuple[list[str], bool]:
    """`(cq argv, answers in JSON)` for one call. Raises `ToolArgs` on any argument the schema
    does not admit; nothing in `args` is passed through unchecked."""
    if not isinstance(args, dict):
        raise ToolArgs("`arguments` must be an object")
    verb = resolve(row)
    actions = {a.dest: a for a in _arguments(verb)}
    unknown = sorted(set(args) - set(actions))
    if unknown:
        raise ToolArgs(f"unknown argument(s): {', '.join(unknown)}")
    by_cwd = row.pillar in ROOT_BY_CWD
    head = ["--root", root] if router().PILLARS[row.pillar].takes_root and not by_cwd else []
    out = [*head, row.pillar, *row.path]
    options, positionals, skipped = [], [], None
    for dest, action in actions.items():
        if dest not in args or args[dest] is None:
            if not action.option_strings:
                if _required(action):
                    raise ToolArgs(f"`{dest}` is required")
                skipped = skipped or dest
            elif _required(action):
                raise ToolArgs(f"`{dest}` is required")
            continue
        value = args[dest]
        _check(action, value)
        if not action.option_strings:
            if skipped:
                raise ToolArgs(f"`{dest}` needs `{skipped}` first")
            positionals += value if isinstance(value, list) else [value]
            continue
        flag = next(f for f in action.option_strings if f.startswith("--"))
        if isinstance(value, bool):
            if value != isinstance(action, argparse._StoreFalseAction):
                options.append(flag)
        else:
            options += [f"{flag}={one}" for one in (value if isinstance(value, list) else [value])]
    if not head and not by_cwd and _declares(verb.parser, "--root"):
        options.append(f"--root={root}")
    as_json = _declares(verb.parser, "--json")
    if as_json:
        options.append("--json")
    return out + options + positionals, as_json


def call(name: str, args: dict, root: str) -> tuple[dict, bool]:
    """`(payload, is_error)` for one catalogued tool. Raises KeyError for an unknown name."""
    row = CATALOG[name]
    try:
        cmd, as_json = argv(row, args, root)
    except ToolArgs as exc:
        return {"ok": False, "code": "mcp-bad-arguments", "message": str(exc)}, True
    try:
        code, out, err = server.dispatch(router().main, cmd, cwd=root)
    except Exception as exc:  # a verb's crash is this call's error, never the server's exit
        return {"ok": False, "code": "mcp-core-failure",
                "message": f"{type(exc).__name__}: {exc}"}, True
    hint = "narrow the call: a smaller path, fewer sections, or a filter the verb offers"
    if not as_json:
        return server.guard({"ok": code <= 1, "exit": code, "text": (out + err).strip()},
                            hint), code > 1
    try:
        payload = json.loads(out)
    except ValueError:
        return {"ok": False, "code": "mcp-core-failure", "exit": code,
                "message": err or out.strip() or "the verb answered with no JSON"}, True
    if not isinstance(payload, dict):
        payload = {"ok": code <= 1, "result": payload}
    return server.guard(payload, hint), code > 1 or payload.get("ok") is False


class CatalogSurface:
    """The catalog as a surface of the common core. Tools only; resources live elsewhere."""

    name = "quenching"

    def __init__(self, present=None):
        self.present = present

    def tools(self) -> dict[str, Row]:
        return available(self.present)

    def has_tool(self, name: str) -> bool:
        return name in self.tools()

    def list_tools(self) -> list[dict]:
        return [describe(name, row) for name, row in self.tools().items()]

    def call_tool(self, name, args, root, session):
        return call(name, args, root)

    def list_resources(self, root):
        return []

    def resource_templates(self):
        return []

    def read_resource(self, uri, root):
        raise server.ResourceError(server.NOT_FOUND, f"unknown resource: {uri}")


def surface(present=None) -> server.Composite:
    """The whole `quenching` server: the catalog, plus the specs tools when `specs` is present."""
    present = present or router().present
    parts = [CatalogSurface(present)]
    if present("specs"):
        from quenching.specs import mcp as specs_mcp
        parts.append(specs_mcp.SURFACE)
    return server.Composite("quenching", parts)
