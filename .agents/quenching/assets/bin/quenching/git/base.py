"""`cq git base` — the base-branch chain, and the second fact every PR/merge caller needs
before deciding whether `pr:`/`merge:` are read on demand or still a required stamp.

Reuses `infer_base_branch` from the common configuration module rather than a second copy of the
chain: `origin/HEAD -> init.defaultBranch -> main` does not change with which pillar asks,
only the git facts fed into it do.

MEASURED for GitHub: a PR whose base is not the repository's own default branch never
populates `closingIssuesReferences`, even with `Closes #<n>` in the body — see
`/docs/external/tools/github-cli-measured-behaviour.md`. The Azure DevOps call below
follows the same shape on an unmeasured hypothesis (`## Design` §Quando o vínculo é lido de
volta, e quando não é of the `pilar-git-e-specs-agnosticas-ao-git` spec) until a live run
proves or breaks it — task 1.2 there is why it stays a hypothesis rather than a second
measurement."""
from __future__ import annotations

import json
import os
import subprocess
import time

from quenching.common.git import COMMAND_TIMEOUT_S, _git, origin_head_branch, short_branch
from quenching.common.output import emit
from quenching.common.config import find_repo_root, infer_base_branch, load_config
from quenching.specs.backends.azure import _az_run
from quenching.specs.backends.base import BackendRefusal


def _origin_head_branch(cwd: str) -> str | None:
    return origin_head_branch(cwd)


def _init_default_branch(cwd: str) -> str | None:
    return _git(cwd, "config", "init.defaultBranch").strip() or None


def _is_host_default(cwd: str, backend: str, base: str) -> bool | None:
    """Whether `base` is the host's own default branch.

    A host CLI that is missing, unauthenticated, or answers with no remote to name reads the
    same way `_git` reads git's own absence — as "unknown", not as an error.
    """
    if backend == "github":
        argv = ["gh", "repo", "view", "--json", "defaultBranchRef",
                "-q", ".defaultBranchRef.name"]
    elif backend == "azure-boards":
        try:
            code, stdout, _stderr = _az_run(
                cwd, "repos", "show", "--query", "defaultBranch", "-o", "tsv")
        except BackendRefusal:
            return None
        if code != 0:
            return None
        default = short_branch(stdout.strip())
        return default == base if default else None
    else:
        return None
    try:
        out = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=COMMAND_TIMEOUT_S, cwd=cwd)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    default = short_branch(out.stdout.strip())
    return default == base if default else None


CACHE_FILE = os.path.join(".quenching", "git-base-cache.json")
CACHE_TTL_S = 24 * 3600


def _cache_path(cwd: str) -> str:
    return os.path.join(find_repo_root(cwd) or cwd, CACHE_FILE)


def _cached_default(cwd: str, key: str) -> bool | None:
    try:
        with open(_cache_path(cwd), encoding="utf-8") as fh:
            entry = json.load(fh).get(key)
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(entry, dict) or not isinstance(entry.get("isDefault"), bool):
        return None
    if time.time() - float(entry.get("at", 0)) > CACHE_TTL_S:
        return None
    return entry["isDefault"]


def _store_default(cwd: str, key: str, value: bool) -> None:
    path = _cache_path(cwd)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[key] = {"isDefault": value, "at": time.time()}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError:
        pass


def _host_default_cached(cwd: str, backend: str, base: str) -> bool | None:
    """`_is_host_default`, remembered per remote URL and base: only a definite answer is kept."""
    url = _git(cwd, "config", "--get", "remote.origin.url").strip()
    if not url:
        return _is_host_default(cwd, backend, base)
    key = f"{url}|{base}"
    hit = _cached_default(cwd, key)
    if hit is not None:
        return hit
    value = _is_host_default(cwd, backend, base)
    if value is not None:
        _store_default(cwd, key, value)
    return value


def resolve_base(cwd: str, host_default: bool = True) -> tuple[str, bool | None]:
    """The base branch, and whether it is the host's own default — the one pair `git stale`
    (task 3.3) also needs, factored out so a second caller is an import rather than a second
    chain. A caller that discards the second value passes `host_default=False` and pays for no
    host call; the answer is `None` then."""
    cfg = load_config(cwd)
    base = infer_base_branch(cfg, _origin_head_branch(cwd), _init_default_branch(cwd))
    if not host_default:
        return base, None
    return base, _host_default_cached(cwd, cfg.get("provider") or cfg.get("backend"), base)


def cmd_base(args) -> int:
    base, is_default = resolve_base(os.getcwd())
    default_label = {True: "yes", False: "no", None: "unknown"}[is_default]
    human = f"base: {base}\ndefault branch: {default_label}"
    emit(args.json, {"ok": True, "base": base, "isDefault": is_default}, human)
    return 0
