"""Spec references — the key one document uses to name another.

A local spec is a bare id (`1202`); a spec in another repository's store is
`owner/repo#id`, with owner and repo lower-cased so the key never collides with a local
id. One module owns the parsing so the task grammar, the composition derivation and the
backend plumbing cannot disagree about what a reference is."""
from __future__ import annotations

import re

_REF_RE = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#(\w[\w.-]*)$")


def member_key(raw: str) -> str:
    """The member key an item's `spec:` value names: a bare id for this store, else
    `owner/repo#id` with owner and repo lower-cased."""
    raw = str(raw).strip().lstrip("#")
    m = _REF_RE.match(raw)
    return f"{m.group(1).lower()}/{m.group(2).lower()}#{m.group(3)}" if m else raw


def split_ref(key: str) -> tuple[str | None, str]:
    """`("owner/repo", id)` for a cross-repository key, `(None, key)` for a local one."""
    m = _REF_RE.match(str(key))
    return (f"{m.group(1).lower()}/{m.group(2).lower()}", m.group(3)) if m else (None, str(key))


def local_key(key: str, self_repo: str | None) -> str:
    """The bare id for a reference naming this repository; `key` unchanged otherwise."""
    repo, spec_id = split_ref(key)
    return spec_id if repo is not None and repo == self_repo else key


def is_remote(key: str) -> bool:
    return split_ref(key)[0] is not None
