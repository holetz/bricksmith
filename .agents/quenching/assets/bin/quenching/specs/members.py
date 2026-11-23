"""Read an epic's members wherever they live — this store, or another repository's git store.

A cross-repository key (`owner/repo#id`, see `parse/epics.py`) names a sibling of this
repository's `origin`: the same URL with its trailing `<owner>/<repo>` swapped. That store is
read through the read-only `GitBackend` mirror under the cache, so nothing is ever written into
either repository's `.git`. A store that cannot be read makes its keys `unreadable`, never
`missing`: a network failure is not a defect of the epic."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess

from quenching.common.config import find_repo_root
from quenching.specs.backends.base import BackendRefusal
from quenching.specs.backends.git import DEFAULT_REMOTE, GIT_TIMEOUT_S, NO_FETCH_ENV, GitBackend
from quenching.specs.parse.epics import local_key, split_ref

# `<owner>/<repo>[.git][/]` at the end of a URL, after `/` or the scp-like `:`.
_TAIL_RE = re.compile(r"(?P<sep>[/:])(?P<owner>[^/:]+)/(?P<repo>[^/]+?)(?P<git>\.git)?/?$")

_FOREIGN: dict[str, GitBackend] = {}


def origin_url(root: str | None) -> str | None:
    repo = find_repo_root(root or os.getcwd())
    run = subprocess.run(["git", "-C", repo, "remote", "get-url", DEFAULT_REMOTE],
                         capture_output=True, text=True, timeout=GIT_TIMEOUT_S, check=False) \
        if os.path.isdir(repo) else None
    return (run.stdout.strip() or None) if run is not None and run.returncode == 0 else None


def own_repo(url: str | None) -> str | None:
    """`owner/repo`, lower-cased, of a URL with that path shape; None otherwise (Azure `/_git/`)."""
    if not url or "/_git/" in url:
        return None
    m = _TAIL_RE.search(url)
    return f"{m.group('owner').lower()}/{m.group('repo').lower()}" if m else None


def sibling_url(url: str | None, repo: str) -> str | None:
    """`url` with its trailing `<owner>/<repo>` replaced by `repo`, keeping `.git` as it was."""
    if own_repo(url) is None:
        return None
    m = _TAIL_RE.search(url)
    return f"{url[:m.start()]}{m.group('sep')}{repo}{m.group('git') or ''}"


_STORE_BACKENDS = ("git", "memory")


def _foreign(root: str, url: str) -> GitBackend:
    if url not in _FOREIGN:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
        digest = hashlib.sha256(url.encode()).hexdigest()[:32]
        mirror = os.path.join(base, "quenching", "mirrors", f"{digest}.git")
        _FOREIGN[url] = GitBackend(root, mirror_dir=mirror, remote_url=url)
    return _FOREIGN[url]


def read_members(backend, keys, root: str | None = None) -> tuple[dict, set[str]]:
    """`(members, unreadable)` for a mixed list of local and cross-repository keys: `members`
    maps every key to its info or None (absent from a store that was read), and `unreadable`
    names the keys whose store could not be read. One `read_specs` per store."""
    keys = [str(k) for k in keys]
    url = origin_url(root) if any(split_ref(k)[0] for k in keys) else None
    self_repo = own_repo(url)
    local: dict[str, str] = {}
    foreign: dict[str, dict[str, str]] = {}
    for key in keys:
        bare = local_key(key, self_repo)
        repo, spec_id = split_ref(bare)
        if repo is None:
            local[key] = bare
        else:
            foreign.setdefault(repo, {})[key] = spec_id
    members: dict = {}
    unreadable: set[str] = set()
    if local:
        got = backend.read_specs(sorted(set(local.values())))
        members.update({key: got.get(bare) for key, bare in local.items()})
    no_fetch = os.environ.get(NO_FETCH_ENV, "") not in ("", "0")
    for repo, wanted in foreign.items():
        target = sibling_url(url, repo)
        # A tracker backend resolves nothing: its foreign members are unreadable by contract.
        if getattr(backend, "name", "git") not in _STORE_BACKENDS:
            target = None
        store = _foreign(find_repo_root(root or os.getcwd()), target) if target else None
        got = None
        if store is not None and not (no_fetch and not store.has_local_ref()):
            try:
                got = store.read_specs(sorted(set(wanted.values())))
            except BackendRefusal:
                # A failed fetch still counts as fresh to this instance, which would then read
                # an empty mirror as "missing": the next read starts from a new one.
                _FOREIGN.pop(target, None)
        if got is None:
            unreadable.update(wanted)
        members.update({key: (got or {}).get(spec_id) for key, spec_id in wanted.items()})
    return members, unreadable
