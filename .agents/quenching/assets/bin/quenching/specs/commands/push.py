"""`push` — send the clone's pending store writes to the remote: one push, one commit.

The git store keeps every write in ONE local pending commit and reaches the remote only at a
sync point. This verb is the explicit one; `sync_store` is the same sync run at the end of
`cq git push`, `cq git pr create` and `cq git pr merge`, where a store failure is reported
beside the code result instead of undoing it. A card that could not follow a push that landed
is a warning beside the result (`cardFailures`), never a failed sync."""
from __future__ import annotations

from quenching.common.config import repository_document
from quenching.common.output import OK
from quenching.specs.backends.base import BackendRefusal
from quenching.specs.backends.git import open_git_backend
from quenching.specs.commands.output import Emitter


def _git_store(root: str):
    """The git store when the repository declares one, else None (a tracker has nothing
    pending). Read from the repository's own declaration, so deciding costs no fetch."""
    document, _, refusal = repository_document(root)
    if refusal:
        return None, refusal
    backend = (document or {}).get("backend")
    if not (isinstance(backend, str) and backend.strip() == "git"):
        return None, {}
    return open_git_backend(root)


def sync_store(root: str) -> dict:
    """The code-side sync: send what is pending, never raise. `{ok, ...}` for the payload."""
    try:
        backend, err = _git_store(root)
        if err:
            return {"ok": False, "message": err.get("message", "")}
        if backend is None:
            return {"ok": True, "skipped": "no git store"}
        pending = backend.pending_state()
        if pending is None:
            return {"ok": True, "skipped": "nothing pending"}
        result = backend.push(collapse=False)
    except BackendRefusal as exc:
        return {"ok": False, "code": exc.err.get("code"), "message": exc.err.get("message", "")}
    return {"ok": True, "sent": pending["specs"], "artifacts": pending.get("artifacts") or [],
            **result}


def card_failures(backend) -> list[dict]:
    """The cards the backend's last sync could not update (`sp-card-failed` entries); [] for a
    backend with no sync or when every card followed."""
    return list(((getattr(backend, "last_sync", None) or {}).get("cardFailures")) or [])


def card_warnings(failures: list[dict]) -> str:
    """The human lines for card failures: one `warning:` line per spec."""
    return "".join(f"\n  warning: {failure.get('message', '')}" for failure in failures)


def cmd_push(args, root: str, out: Emitter) -> int:
    """Send every pending write of the clone in one push that leaves the store branch a single
    parentless commit; `--drop <id>` discards that spec's local writes first."""
    backend, err = _git_store(root)
    if err:
        return out.emit_err(args.json, err)
    if backend is None:
        return out.emit_err(args.json, {
            "code": "sp-push-no-store", "exit": 2,
            "message": "`cq specs push` sends the git store's pending writes; this repository "
                       "does not declare `backend: git`, so nothing is pending"})
    drop = tuple(args.drop or ())
    pending = backend.pending_state()
    result = backend.push(drop=drop)
    sent = [s for s in (pending["specs"] if pending else []) if s not in drop]
    artifacts = list((pending or {}).get("artifacts") or [])
    obj = {"ok": True, "sent": sent, "artifacts": artifacts, "dropped": list(drop), **result}
    if result.get("commit"):
        counted = f"{len(sent)} spec(s)" + (f" and {len(artifacts)} artifact(s)"
                                           if artifacts else "")
        human = (f"pushed {counted} in {result['commit'][:12]} "
                 f"(history {result['history']})")
    elif result.get("collapseRefused"):
        human = ("nothing pending; the remote refuses to rewrite the store branch, so it keeps "
                 "its history until a writer allowed to force-push syncs")
    else:
        human = "nothing pending; the store branch is already one commit"
    out.emit(args.json, obj, human + card_warnings(result.get("cardFailures") or []))
    return OK
