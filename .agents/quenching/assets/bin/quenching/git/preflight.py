"""`cq git preflight` — five deterministic questions answered before a run expands: is the spec
store writable, is the provider's type catalog there, does a push reach the remote, is the PR
route authenticated (`gh`, or `az` on an Azure DevOps origin), and is the base branch's CI
available.

Read-only: pushes use `--dry-run` onto a ref the remote does not have (`PROBE_REF`, with
`--force`), so a branch that is merely behind or diverged never reads as a refused write; a failed
probe carries the `reason` git's stderr names — `read-only` or `no-credentials` (`fail`), or
`unreachable` or `probe-failed` (`unknown`). CI is read with `gh run list` / `gh run view` (GitHub only:
on Azure DevOps `ci` is `unknown`), and no hook is
ever disabled or skipped. Each check is `{id, status, message}` with status `ok`, `fail`,
`unknown` (could not answer, never promoted to ok) or `skipped`. `ci` also answers
`unavailable` when the latest finished run has jobs with no steps (a blocked runner or billing).

Exit 0 no check failed or is unavailable · 1 one did · 2 refusal (unknown remote)."""
from __future__ import annotations

import json
import os
import subprocess

from quenching.common.config import find_repo_root
from quenching.common.output import FINDINGS, OK, emit, refuse
from quenching.common.tools import spawn_argv
from quenching.git.base import resolve_base
from quenching.git.push import remote_probe
from quenching.specs.backends.git import STORE_BRANCH
from quenching.specs.config import load_config

TIMEOUT_S = 60
BAD = {"fail", "unavailable"}


def _tool(cwd: str, *argv: str) -> tuple[int, str, str]:
    try:
        done = subprocess.run(spawn_argv(list(argv)), cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=TIMEOUT_S, env={**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"})
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except OSError as e:
        return 127, "", str(e)
    return done.returncode, done.stdout, done.stderr


def _check(cid: str, status: str, message: str, **extra) -> dict:
    return {"id": cid, "status": status, "message": message, **extra}


PROBE_REF = "refs/heads/quenching-preflight-probe"
_READ_ONLY = ("permission", "denied", "403", "not allowed", "protected")
_NO_CREDENTIALS = ("could not read username", "authentication", "terminal prompts disabled", "401",
                   "publickey")
_UNREACHABLE = ("could not resolve", "unable to access", "connection", "timed out")


def _push_reason(code: int, err: str) -> str:
    """Credentials and refusals first: a forge's 401/403 arrives wrapped in "unable to access"."""
    text = err.lower()
    if code in (124, 127):
        return "unreachable"
    for reason, words in (("no-credentials", _NO_CREDENTIALS), ("read-only", _READ_ONLY),
                          ("unreachable", _UNREACHABLE)):
        if any(word in text for word in words):
            return reason
    return "probe-failed"


def _dry_push(cwd: str, remote: str, source: str) -> tuple[str, str, str | None]:
    """Whether `remote` accepts a write, asked of a ref it does not have: no fast-forward rule
    applies there, so only a refused or unreachable remote can fail the probe."""
    code, _out, err = _tool(cwd, "git", "push", "--dry-run", "--porcelain", "--force", "--",
                            remote, f"{source}:{PROBE_REF}")
    if code == 0:
        return "ok", "", None
    reason = _push_reason(code, err)
    return ("fail" if reason in ("read-only", "no-credentials") else "unknown"), err.strip(), reason


def _store(cwd: str, remote: str, backend: str) -> dict:
    if backend != "git":
        return _check("store", "unknown", f"backend {backend} is not probed")
    ref = f"refs/heads/{STORE_BRANCH}"
    if _tool(cwd, "git", "rev-parse", "--verify", "--quiet", ref)[0] == 0:
        status, why, reason = _dry_push(cwd, remote, ref)
        return _check("store", status, "store branch accepts a push (dry-run)"
                      if status == "ok" else f"store push ({reason}): {why or 'unreachable'}",
                      reason=reason)
    code, _out, err = _tool(cwd, "git", "ls-remote", "--exit-code", remote, ref)
    if code == 0:
        return _check("store", "ok", "store branch exists on the remote; no local copy to dry-run")
    if code == 2:
        return _check("store", "fail", f"no {STORE_BRANCH} branch locally or on {remote}")
    return _check("store", "unknown", err.strip() or "remote unreadable")


def _types(backend: str) -> dict:
    if backend == "git":
        return _check("types", "skipped", "the git store has no type catalog")
    return _check("types", "unknown", f"backend {backend} catalog is not probed")


def _hooks(cwd: str) -> list[str]:
    code, out, _err = _tool(cwd, "git", "rev-parse", "--git-path", "hooks")
    path = out.strip()
    cfg = _tool(cwd, "git", "config", "core.hooksPath")[1].strip()
    base = cfg or path
    if not os.path.isabs(base):
        base = os.path.join(cwd, base)
    return [h for h in ("pre-commit", "pre-push")
            if os.access(os.path.join(base, h), os.X_OK)] if code == 0 else []


def _push(cwd: str, remote: str) -> dict:
    code, out, _err = _tool(cwd, "git", "symbolic-ref", "--quiet", "HEAD")
    if code != 0:
        return _check("push", "unknown", "detached HEAD: no branch to dry-run")
    status, why, reason = _dry_push(cwd, remote, out.strip())
    return _check("push", status,
                  ("push reaches the remote (dry-run); a passing dry-run does not prove the "
                   "pre-push hook passes — the hook runs at the real push, and is never skipped")
                  if status == "ok" else f"push ({reason}): {why or 'unreachable'}",
                  reason=reason, hooks=_hooks(cwd), noVerify=False)


def _gh_auth(cwd: str, provider: str | None) -> dict:
    """The PR route's authentication: `gh` on GitHub, `az` on an Azure DevOps origin. The id
    stays `ghAuth` on both, because the autopilot reads it by that name."""
    if provider == "azure-boards":
        code, _out, err = _tool(cwd, "az", "repos", "pr", "list", "--status=all", "--top=1",
                                "--detect=true", "--output=json")
        if code in (124, 127):
            return _check("ghAuth", "unknown", err.strip() or "az unavailable")
        return _check("ghAuth", "ok" if code == 0 else "fail",
                      "az reaches the Azure DevOps PRs" if code == 0
                      else f"az does not reach the Azure DevOps PRs: {err.strip()}".rstrip(": "))
    code, _out, err = _tool(cwd, "gh", "auth", "status")
    if code in (124, 127):
        return _check("ghAuth", "unknown", err.strip() or "gh unavailable")
    return _check("ghAuth", "ok" if code == 0 else "fail",
                  "gh is authenticated" if code == 0 else "gh is not authenticated")


def _ci(cwd: str, base: str, provider: str | None) -> dict:
    if provider == "azure-boards":
        return _check("ci", "unknown", "the CI probe reads GitHub Actions; Azure Pipelines "
                                       "is not read")
    code, out, err = _tool(cwd, "gh", "run", "list", "--branch", base, "--limit", "3",
                           "--json", "databaseId,status,conclusion")
    if code != 0:
        return _check("ci", "unknown", err.strip() or "gh run list failed")
    try:
        runs = json.loads(out or "[]")
        done = [r for r in runs if r.get("status") == "completed"]
    except (json.JSONDecodeError, AttributeError):
        return _check("ci", "unknown", "gh run list returned an unreadable payload")
    if not done:
        return _check("ci", "unknown", f"no finished run on {base}")
    code, out, err = _tool(cwd, "gh", "run", "view", str(done[0].get("databaseId")),
                           "--json", "jobs")
    try:
        jobs = json.loads(out)["jobs"] if code == 0 else None
    except (json.JSONDecodeError, KeyError, TypeError):
        jobs = None
    if not isinstance(jobs, list):
        return _check("ci", "unknown", err.strip() or "gh run view returned no jobs")
    if not any(isinstance(j, dict) and j.get("steps") for j in jobs):
        return _check("ci", "unavailable",
                      f"the latest finished run on {base} ran no steps: CI is not executing")
    return _check("ci", "ok", f"CI on {base} ran steps in its latest finished run")


def cmd_preflight(args) -> int:
    cwd = os.getcwd()
    if remote_probe(cwd, args.remote) is not True:
        return refuse({"code": "git-preflight-remote-unknown",
                       "message": f"not a configured remote: {args.remote}"}, args.json)
    cfg = load_config(find_repo_root(cwd))
    backend = cfg.get("backend") or "git"
    base, _default = resolve_base(cwd)
    checks = [_store(cwd, args.remote, backend), _types(backend), _push(cwd, args.remote),
              _gh_auth(cwd, cfg.get("provider")), _ci(cwd, base, cfg.get("provider"))]
    bad = [c["id"] for c in checks if c["status"] in BAD]
    ci = next(c for c in checks if c["id"] == "ci")
    merges = ci["status"] != "unavailable"
    lines = [f"{c['id']}: {c['status']} — {c['message']}" for c in checks]
    lines.append("merges: " + ("allowed" if merges else "none planned (CI unavailable)"))
    emit(args.json, {"ok": not bad, "base": base, "remote": args.remote, "checks": checks,
                     "failed": bad, "mergesAllowed": merges, "plannedMerges": merges},
         "\n".join(lines))
    return FINDINGS if bad else OK
