"""`find` — the reverse direction `list`/`status` never answered: given a branch name, a commit
sha, or a PR number/URL, which spec owns it.

Exact match only, against what the front already records — `branch.work`, `pr`/`merge.pr`, and
each task's `subject:` anchor (resolved from a commit sha via `git show`, never stored). No new
field, no index: the scan is the same `list_specs` + `read_spec` walk `cmd_list` already pays for.
`find_match` is the pure half — one backend in, the matching descriptor or `None` out — so it is
testable against `MemoryBackend` without a git repository or `open_backend`'s config resolution.
"""
from __future__ import annotations

import os
import re
from typing import Any

from quenching.common.git import _git
from quenching.specs.backends import open_backend
from quenching.specs.backends.base import SpecBackend
from quenching.specs.commands.output import Emitter, display_locator

PR_NUMBER_RE = re.compile(r"(\d+)\s*$")


def _pr_number(value: object) -> str | None:
    """A PR flag's value, or a recorded `pr`/`merge.pr` field, may be a bare number or a full
    URL — normalise both to the trailing digits, the one form every backend agrees on."""
    m = PR_NUMBER_RE.search(str(value or "").strip())
    return m.group(1) if m else None


def _commit_subject(sha: str) -> str | None:
    """The subject line of the commit `sha` names, read straight from git. `None` when the
    sha does not resolve — never invented, never cached.

    Runs from `os.getcwd()`, never the specs `root`: under `github`/`azure-boards` that root
    names a workspace that may not exist as a real directory at all, and git facts come from
    the checkout the process is actually standing in — the same resolution `cq specs release`
    already uses for the same reason."""
    return _git(os.getcwd(), "show", "-s", "--format=%s", sha.strip()).strip() or None


def find_match(backend: SpecBackend, *, branch: str | None = None,
               commit_subject: str | None = None, pr: str | None = None) -> dict[str, Any] | None:
    """The first spec whose records match one of the three criteria, or `None`.

    `pr` is already normalised to bare digits by the caller; `commit_subject` is already
    resolved from a sha to the commit's own subject line — this function does neither, so it
    stays pure over whatever a backend hands back."""
    for descriptor in backend.list_specs():
        info, err = backend.read_spec(descriptor["id"])
        if err or info is None:
            continue
        fm = info["frontmatter"]
        rec_branch = fm.get("branch") or {}
        rec_pr = fm.get("pr") or {}
        rec_merge = fm.get("merge") or {}

        # `work == base` is a spec built in place, not a branch that identifies it — every
        # such spec records the same base, so matching it would hand back an arbitrary one of
        # them as if it owned the ref (`git.md` §Where a branch comes from).
        if (branch and rec_branch.get("work") == branch
                and rec_branch.get("work") != rec_branch.get("base")):
            return {"info": info, "matchedBy": "branch"}
        if pr and pr in (_pr_number(rec_pr.get("number")), _pr_number(rec_pr.get("url")),
                        _pr_number(rec_merge.get("pr"))):
            return {"info": info, "matchedBy": "pr"}
        if commit_subject and any(t.get("subject") == commit_subject for t in info["tasks"]):
            return {"info": info, "matchedBy": "commit"}
    return None


def cmd_find(args, root: str, out: Emitter) -> int:
    if not (args.branch or args.commit or args.pr):
        out.emit(args.json, {"ok": False, "code": "sp-find-no-criterion",
                             "message": "pass --branch, --commit or --pr"},
                 "error: pass --branch, --commit or --pr")
        return 2

    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)

    commit_subject = None
    if args.commit:
        commit_subject = _commit_subject(args.commit)
        if commit_subject is None:
            out.emit(args.json,
                     {"ok": False, "code": "sp-find-no-such-commit", "commit": args.commit,
                      "message": f"'{args.commit}' does not resolve to a commit here"},
                     f"error: '{args.commit}' does not resolve to a commit here")
            return 1

    hit = find_match(backend, branch=args.branch, commit_subject=commit_subject,
                     pr=_pr_number(args.pr) if args.pr else None)

    if hit:
        info = hit["info"]
        out.emit(args.json,
                 {"ok": True, "id": info["id"], "matchedBy": hit["matchedBy"],
                  "path": display_locator(info["path"], root)},
                 f"{info['id']} — matched by {hit['matchedBy']}")
        return 0

    criterion = args.branch or args.commit or args.pr
    out.emit(args.json, {"ok": False, "code": "sp-find-no-match", "criterion": criterion,
                         "message": f"no spec matches {criterion!r}"},
             f"no spec matches {criterion!r}")
    return 1
