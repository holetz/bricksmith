"""The one parser for git remote URLs: host, cache key and GitHub `owner/name`."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

_GITHUB_REMOTE_RE = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


def remote_host(remote: str) -> str:
    """Return the lower-cased host of an SSH/scp/HTTPS remote, without user or port."""
    host = remote.strip().split("#", 1)[0]
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.rsplit("@", 1)[-1]
    return host.split("/", 1)[0].split(":", 1)[0].lower()


def normalize_remote(remote: str) -> str:
    """Normalize SSH/scp and HTTPS remotes to one case-insensitive cache key."""
    value = remote.strip()
    if not value:
        return ""
    if "://" in value:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        path = parsed.path
    else:
        left, separator, path = value.partition(":")
        host = left.rsplit("@", 1)[-1] if separator else ""
    if not host or not path:
        return ""
    path = path.split("?", 1)[0].split("#", 1)[0].strip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    return f"https://{host.lower()}/{path.lower()}"


def github_slug(remote: str) -> str:
    """Return `owner/name` for a github.com remote, or an empty string."""
    match = _GITHUB_REMOTE_RE.search(remote) if remote else None
    return f"{match.group(1)}/{match.group(2)}" if match else ""
