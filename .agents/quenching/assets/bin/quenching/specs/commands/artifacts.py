"""`cq specs artifact`: the text a spec carries beside its document.

A handoff is the first user — a session's last act writes one and the next session reads it —
but the verb is not about handoffs: it is the store's one way to keep a text that belongs to a
spec and is not the spec. It lives on the `quenching` branch, under `specs/artifacts/<id>/`,
because that is the tree every caller already syncs and because a side file beside a worktree is
invisible to the next machine.

TEXT ONLY, ONE MEGABYTE, ONE PATH SEGMENT. The store decodes UTF-8 and the leak guard scans
text, so `write` refuses bytes that are not UTF-8 before anything reaches the store; the backend
refuses a name that could leave its subtree and a body over the cap. No argument carries the
body — it comes from stdin or `--from <path>` — so a handoff never lands in a shell history or a
process listing.

`--spec` is optional on purpose: an artifact written outside a spec's life (the handoff of a
conversation that has none) is a real case, and it lives under `specs/artifacts/_handoffs/`
rather than attached to an id nobody chose.
"""
from __future__ import annotations

import sys

from quenching.common.io import write_text
from quenching.specs.backends import open_backend
from quenching.specs.commands.output import Emitter, display_locator

STDIN = "-"


def _opened(root: str, out: Emitter, as_json: bool):
    """The repository's store, or `(None, exit code)` after a refusal. Every verb here writes
    or reads an artifact, so a backend without the capability refuses inside the call itself."""
    backend, err = open_backend(root)
    if err:
        return None, out.emit_err(as_json, err)
    if backend is None:
        return None, out.emit_err(as_json, {
            "code": "sp-artifact-no-store", "exit": 2,
            "message": "this repository declares no specs store; an artifact lives on the "
                       "`quenching` branch, and there is none to write to"})
    return backend, 0


def _spec(args) -> str | int | None:
    """The `--spec` value, or None for a standalone artifact. The backend judges the id."""
    raw = (getattr(args, "spec", None) or "").strip()
    return int(raw) if raw.isdigit() else (raw or None)


def _body(args, out: Emitter) -> tuple[str | None, int]:
    """The text to store, read from `--from <path>` or from stdin, decoded strictly.

    A body that is not UTF-8 is refused HERE, before the backend hashes anything: `_texts`
    would fail later with a traceback, and a traceback is never the answer to a bad input."""
    source = getattr(args, "from_path", None)
    if source and source != STDIN:
        try:
            with open(source, "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            return None, out.emit_err(args.json, {
                "code": "sp-artifact-unreadable", "exit": 2,
                "message": f"cannot read '{source}': {exc.strerror or exc}; nothing was written"})
    else:
        raw = getattr(sys.stdin, "buffer", None)
        raw = raw.read() if raw is not None else sys.stdin.read().encode("utf-8")
    try:
        return raw.decode("utf-8"), 0
    except UnicodeDecodeError as exc:
        return None, out.emit_err(args.json, {
            "code": "sp-artifact-binary", "exit": 2,
            "message": f"the artifact is not UTF-8 text (byte {exc.start} is {raw[exc.start]:#04x}); "
                       "the store carries text artifacts only, and nothing was written"})


def _listed(root: str, backend, spec) -> dict:
    """One row per artifact, with the locator printed the way a human follows it."""
    rows = [{"spec": row["spec"], "name": row["name"],
             "path": display_locator(row["path"], root)}
            for row in backend.list_artifacts(spec)]
    return {"count": len(rows), "artifacts": rows}


def cmd_artifact(args, root: str, out: Emitter) -> int:
    """`list`, `read`, `write` and `rm`, dispatched on the sub-verb the parser stored."""
    verb = getattr(args, "artifact_cmd", None)
    if not verb:
        return out.emit_err(args.json, {
            "code": "sp-artifact-verb", "exit": 2,
            "message": "choose: list, read, write or rm"})
    backend, code = _opened(root, out, args.json)
    if backend is None:
        return code
    spec = _spec(args)
    if verb == "list":
        listed = _listed(root, backend, spec)
        human = (f"no standalone artifact" if spec is None else f"no artifact for spec {spec}") \
            if not listed["count"] else "\n".join(row["path"] for row in listed["artifacts"])
        out.emit(args.json, {"ok": True, "spec": spec, **listed}, human)
        return 0
    if verb == "read":
        text = backend.read_artifact(spec, args.name)
        # The locator is the backend's, never a path composed here: the store owns where an
        # artifact lives, and the CLI only prints what it says.
        path = next((display_locator(row["path"], root)
                     for row in backend.list_artifacts(spec) if row["name"] == args.name),
                    args.name)
        if args.out:
            write_text(args.out, text)
            out.emit(args.json, {"ok": True, "spec": spec, "name": args.name, "path": path,
                                 "out": args.out, "bytes": len(text.encode("utf-8"))},
                     f"read {path} → {args.out} ({len(text.encode('utf-8'))} bytes)")
            return 0
        if args.json:
            out.emit(True, {"ok": True, "spec": spec, "name": args.name, "path": path,
                            "bytes": len(text.encode("utf-8")), "text": text}, "")
        else:
            sys.stdout.write(text)
        return 0
    if verb == "write":
        text, code = _body(args, out)
        if text is None:
            return code
        locator = backend.write_artifact(spec, args.name, text)
        out.emit(args.json, {"ok": True, "spec": spec, "name": args.name,
                             "path": display_locator(locator, root),
                             "bytes": len(text.encode("utf-8"))},
                 f"wrote {display_locator(locator, root)} ({len(text.encode('utf-8'))} bytes) "
                 "— unsent until `cq specs push`")
        return 0
    locator = backend.remove_artifact(spec, args.name)
    out.emit(args.json, {"ok": True, "spec": spec, "name": args.name,
                         "path": display_locator(locator, root)},
             f"removed {display_locator(locator, root)} — unsent until `cq specs push`")
    return 0
