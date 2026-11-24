"""A persistent `<phase>:<blob sha> -> lean row fields` cache for `list --lean`.

A blob is immutable, but the stage and the records `derive_labels` yields also depend on the
phase the document sits in (an archive move keeps the blob), so the key carries both. What
`derive_info` + `derive_labels` produce for one key never changes unless the derivation does. The cache therefore lives beside the object store (never
tracked, shared by every worktree) and is keyed by a fingerprint of the schema and of the parse
sources: editing either drops the whole file. Every failure degrades to "no cache" — the listing
is always computable without it."""
from __future__ import annotations

import hashlib
import json
import os

from quenching.specs import schema as _schema

_PARSE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "parse")
_CACHE_NAME = "quenching-specs-lean-cache.json"


def _fingerprint() -> str:
    h = hashlib.sha1()
    for path in [os.path.join(_schema.ASSET_DIR, "schema.json"),
                 *(os.path.join(_PARSE_DIR, n) for n in sorted(os.listdir(_PARSE_DIR))
                   if n.endswith(".py")),
                 _schema.__file__]:
        try:
            with open(path, "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(b"?")
    return h.hexdigest()


def lean_key(phase: str, blob: str) -> str:
    return f"{phase}:{blob}"


class LeanCache:
    def __init__(self, directory: str | None) -> None:
        self.path = os.path.join(directory, _CACHE_NAME) if directory else None
        self.rows: dict[str, dict] = {}
        self.dirty = False
        self.print = _fingerprint()
        if self.path:
            try:
                with open(self.path, encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and data.get("print") == self.print \
                        and isinstance(data.get("rows"), dict):
                    self.rows = data["rows"]
            except (OSError, ValueError):
                pass

    def get(self, key: str) -> dict | None:
        return self.rows.get(key)

    def put(self, key: str, row: dict) -> None:
        self.rows[key] = row
        self.dirty = True

    def save(self, keep: set[str]) -> None:
        """Write once, atomically, pruned to the keys the current tip still carries."""
        if not self.path or not (self.dirty or len(self.rows) != len(keep & set(self.rows))):
            return
        rows = {k: r for k, r in self.rows.items() if k in keep}
        tmp = f"{self.path}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"print": self.print, "rows": rows}, fh, separators=(",", ":"))
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
