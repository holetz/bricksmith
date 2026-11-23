"""A local cache of an Azure Boards spec store, kept in step by service hooks and a periodic
reconciliation, and a backend decorator that serves every read from it.

IT IS NOT A STORE. Azure Boards stays the one source of truth: the cache is per machine, in the
user cache directory (never the repository), holds no credential, and deleting it loses nothing.
`spec-backend.md` §Granular reading admits a cache inside `cq specs` on exactly those terms.

STALENESS IS DETECTED BY REVISION. Every entry carries the `rev` it was read at. A service-hook
event or a reconciliation that names a higher `rev` marks the entry stale, and the next read
refetches it. A WRITE is never answered from the cache: `SyncedBackend` forwards it to the adapter,
whose JSON-Patch `test` on `rev` already refuses a write based on a stale read, then drops the
entry and refetches it before answering.

NO LOCK. Each work item is its own file, written by atomic rename, and an entry is only replaced
by one whose `rev` is not lower; two processes racing cost at worst a redundant refetch. That also
keeps this working where `fcntl` does not exist."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time

from quenching.common.io import write_text
from quenching.specs.backends.base import SpecBackend
from quenching.specs.parse import FIELD_KEYS, PHASES, derive_info

ENV_ENABLE = "QUENCHING_SPECS_SYNC"
ENV_TTL = "QUENCHING_SYNC_RECONCILE_S"
DEFAULT_TTL_S = 300.0
# Past this many changed items one bulk listing is cheaper than one batch call per item.
BULK_THRESHOLD = 20
DESCRIPTOR_KEYS = ("id", "phase", "folder", "legacy", "path")
EVENTS = ("workitem.created", "workitem.updated", "workitem.deleted", "workitem.restored")


class EventError(ValueError):
    """A service-hook payload this module cannot read; the receiver answers 400."""


def sync_enabled() -> bool:
    return os.environ.get(ENV_ENABLE, "").strip().lower() not in ("", "0", "false", "no", "off")


def reconcile_ttl() -> float:
    try:
        return max(0.0, float(os.environ.get(ENV_TTL, DEFAULT_TTL_S)))
    except ValueError:
        return DEFAULT_TTL_S


def cache_dir(org: str, project: str) -> str:
    """Per machine and per user, outside the repository, keyed by organisation and project."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    key = hashlib.sha256(f"{org}\n{project}".encode()).hexdigest()[:16]
    return os.path.join(base, "quenching", "azure-sync", key)


class SyncCache:
    """One JSON file per work item under `items/`, plus `meta.json`. A missing, unreadable or
    corrupt file is a miss, never a failure: nothing here is authoritative."""

    def __init__(self, directory: str) -> None:
        self.directory = directory
        self.items_dir = os.path.join(directory, "items")

    def _path(self, item_id: int) -> str:
        return os.path.join(self.items_dir, f"{int(item_id)}.json")

    @staticmethod
    def _load(path: str):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _dump(self, path: str, data: dict) -> None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write_text(path, json.dumps(data, default=str))
        except OSError:
            pass

    def get(self, item_id: int) -> dict | None:
        entry = self._load(self._path(item_id))
        if not entry or not isinstance(entry.get("rev"), int) or "text" not in entry:
            return None
        return entry

    def put(self, entry: dict) -> None:
        """Store `entry` unless the cache already holds a higher `rev` for the same item."""
        held = self.get(entry["id"])
        if held and held["rev"] > entry["rev"]:
            return
        self._dump(self._path(entry["id"]), entry)

    def drop(self, item_id: int) -> None:
        try:
            os.unlink(self._path(item_id))
        except OSError:
            pass

    def mark_stale(self, item_id: int, rev: int | None) -> bool:
        """Mark an entry stale when `rev` is unknown or above the held one."""
        entry = self.get(item_id)
        if not entry or (rev is not None and rev <= entry["rev"]) or entry.get("stale"):
            return False
        entry["stale"] = True
        self._dump(self._path(item_id), entry)
        return True

    def ids(self) -> list[int]:
        try:
            names = os.listdir(self.items_dir)
        except OSError:
            return []
        return sorted(int(n[:-5]) for n in names if n.endswith(".json") and n[:-5].isdigit())

    def entries(self) -> list[dict]:
        return [e for e in (self.get(i) for i in self.ids()) if e]

    def meta(self) -> dict:
        return self._load(os.path.join(self.directory, "meta.json")) or {}

    def update_meta(self, **changes) -> dict:
        data = self.meta()
        data.update(changes)
        self._dump(os.path.join(self.directory, "meta.json"), data)
        return data

    def status(self) -> dict:
        entries = self.entries()
        meta = self.meta()
        return {"path": self.directory, "items": len(entries),
                "stale": sum(1 for e in entries if e.get("stale")),
                "reconciledAt": meta.get("reconciledAt"), "dirty": bool(meta.get("dirty")),
                "notSpecs": len(meta.get("notSpecs") or {})}


def entry_of(info: dict, rev: int, now: float) -> dict:
    """The inputs of `derive_info`, not its output: the document, the descriptor, the stored
    native fields. Rebuilding runs the one shared derivation, so a cached read cannot drift."""
    frontmatter = info.get("frontmatter") or {}
    return {"id": int(info["id"]), "rev": int(rev), "stale": False, "fetchedAt": now,
            "descriptor": {k: info[k] for k in DESCRIPTOR_KEYS if k in info},
            "text": info["text"],
            "native": {k: frontmatter[k] for k in FIELD_KEYS if k in frontmatter}}


def info_of(entry: dict) -> dict:
    info = derive_info(dict(entry["descriptor"]), entry["text"])
    info["frontmatter"].update(copy.deepcopy(entry.get("native") or {}))
    info["_azure_rev"] = entry["rev"]
    return info


def parse_event(payload) -> tuple[str, int, int | None]:
    """`(event type, work item id, rev or None)` from an Azure DevOps service-hook body."""
    if not isinstance(payload, dict):
        raise EventError("the event body must be a JSON object")
    kind = payload.get("eventType")
    if kind not in EVENTS:
        raise EventError(f"eventType {kind!r} is not one of {', '.join(EVENTS)}")
    resource = payload.get("resource")
    if not isinstance(resource, dict):
        raise EventError("the event carries no `resource` object")
    raw_id = resource.get("workItemId", resource.get("id"))
    revision = resource.get("revision") if isinstance(resource.get("revision"), dict) else {}
    raw_rev = resource.get("rev", revision.get("rev"))
    try:
        item_id = int(raw_id)
    except (TypeError, ValueError):
        raise EventError("the event names no work item id") from None
    try:
        rev = int(raw_rev) if raw_rev is not None else None
    except (TypeError, ValueError):
        raise EventError("the event `rev` is not a number") from None
    return kind, item_id, rev


def apply_event(cache: SyncCache, payload) -> dict:
    """Record one change. Nothing is fetched here: the receiver answers fast and the cost lands
    on the next read. A change that may add or remove a listed spec sets `dirty`."""
    kind, item_id, rev = parse_event(payload)
    meta = cache.meta()
    ignored = dict(meta.get("notSpecs") or {})
    if kind == "workitem.deleted":
        cache.drop(item_id)
        ignored.pop(str(item_id), None)
        cache.update_meta(dirty=True, notSpecs=ignored)
        return {"event": kind, "id": item_id, "action": "dropped"}
    if cache.get(item_id):
        action = "stale" if cache.mark_stale(item_id, rev) else "current"
        if kind != "workitem.updated":
            cache.update_meta(dirty=True)
            action = "dirty"
        return {"event": kind, "id": item_id, "action": action}
    if kind == "workitem.updated" and rev is not None and ignored.get(str(item_id), -1) >= rev:
        return {"event": kind, "id": item_id, "action": "ignored"}
    ignored.pop(str(item_id), None)
    cache.update_meta(dirty=True, notSpecs=ignored)
    return {"event": kind, "id": item_id, "action": "dirty"}


class SyncedBackend(SpecBackend):
    """The Azure backend with its reads answered from a `SyncCache`. Everything it does not
    define is the inner backend's (`__getattr__`), so callers keep one backend."""

    def __init__(self, inner: SpecBackend, cache: SyncCache, ttl: float | None = None,
                 clock=time.time) -> None:
        self._inner = inner
        self.cache = cache
        self.ttl = reconcile_ttl() if ttl is None else ttl
        self.clock = clock
        self.name = inner.name

    def __getattr__(self, attr: str):
        if attr == "_inner":
            raise AttributeError(attr)
        return getattr(self._inner, attr)

    # -- keeping the cache in step ------------------------------------------ #
    def _fetch(self, ids: list[int], revs: dict[int, int]) -> list[int]:
        """Refetch `ids` through the adapter; the ids that are not specs come back."""
        if not ids:
            return []
        invalidate = getattr(self._inner, "_invalidate", None)
        if invalidate:
            invalidate()
        if len(ids) > BULK_THRESHOLD:
            self._inner.list_specs()
        skipped = []
        for item_id in ids:
            info, _err = self._inner.read_spec(item_id)
            if not info:
                self.cache.drop(item_id)
                skipped.append(item_id)
                continue
            rev = info.get("_azure_rev", revs.get(item_id, 0))
            self.cache.put(entry_of(info, rev, self.clock()))
        return skipped

    def reconcile(self) -> dict:
        """Compare every id and `rev` with the cache; refetch what differs, drop what left."""
        revs = self._inner.revisions()
        held = {e["id"]: e for e in self.cache.entries()}
        ignored = {int(k): v for k, v in (self.cache.meta().get("notSpecs") or {}).items()}
        gone = [i for i in held if i not in revs]
        for item_id in gone:
            self.cache.drop(item_id)
        changed = [i for i, rev in sorted(revs.items())
                   if (i in held and (rev > held[i]["rev"] or held[i].get("stale")))
                   or (i not in held and ignored.get(i, -1) < rev)]
        skipped = self._fetch(changed, revs)
        ignored = {i: revs[i] for i in revs
                   if i in skipped or (i in ignored and i not in held and i not in changed)}
        self.cache.update_meta(reconciledAt=self.clock(), dirty=False,
                               notSpecs={str(k): v for k, v in ignored.items()})
        return {"checked": len(revs), "refetched": len(changed) - len(skipped),
                "dropped": len(gone), "notSpecs": len(ignored)}

    def _due(self) -> bool:
        meta = self.cache.meta()
        at = meta.get("reconciledAt")
        return bool(meta.get("dirty")) or not isinstance(at, (int, float)) \
            or self.clock() - at > self.ttl

    # -- the five primitives ------------------------------------------------- #
    def list_specs(self, phase: str | None = None, lean: bool = False) -> list[dict]:
        if lean:
            # The lean index is already body-free (one WIQL and one batch of four fields) and
            # carries the native state and tags an entry does not; it is not worth a second copy.
            return self._inner.list_specs(phase, lean=True)
        if self._due():
            self.reconcile()
        stale = [e["id"] for e in self.cache.entries() if e.get("stale")]
        if stale:
            self._fetch(stale, {})
        rows = [dict(e["descriptor"]) for e in self.cache.entries()
                if phase is None or e["descriptor"].get("phase") == phase]
        return sorted(rows, key=lambda r: (PHASES.index(r["phase"]), r["id"]))

    def read_spec(self, spec_id: str | int) -> tuple[dict | None, dict]:
        try:
            item_id = int(spec_id)
        except (TypeError, ValueError):
            return self._inner.read_spec(spec_id)
        if self._due():
            self.reconcile()
        entry = self.cache.get(item_id)
        if entry and not entry.get("stale"):
            return info_of(entry), {}
        info, err = self._inner.read_spec(item_id) if not entry else self._refetch(item_id)
        if not info:
            return None, err
        self.cache.put(entry_of(info, info.get("_azure_rev", 0), self.clock()))
        return info, {}

    def _refetch(self, item_id: int):
        invalidate = getattr(self._inner, "_invalidate", None)
        if invalidate:
            invalidate()
        return self._inner.read_spec(item_id)

    def _after_write(self, item_id: int | None, refetch: bool) -> None:
        if item_id is None:
            self.cache.update_meta(dirty=True)
            return
        self.cache.drop(item_id)
        if refetch:
            info, _err = self._refetch(item_id)
            if info:
                self.cache.put(entry_of(info, info.get("_azure_rev", 0), self.clock()))
            else:
                self.cache.update_meta(dirty=True)

    def write_spec(self, info: dict, text: str) -> None:
        item_id = int(info["id"])
        try:
            self._inner.write_spec(info, text)
        except Exception:
            self._after_write(item_id, refetch=False)
            raise
        self._after_write(item_id, refetch=True)

    def create_spec(self, phase: str, text: str) -> str:
        locator = self._inner.create_spec(phase, text)
        self._after_write(None, refetch=False)
        return locator

    def move_spec(self, info: dict, dest_phase: str) -> str:
        item_id = int(info["id"])
        try:
            locator = self._inner.move_spec(info, dest_phase)
        except Exception:
            self._after_write(item_id, refetch=False)
            raise
        self._after_write(item_id, refetch=True)
        return locator


def wrap_if_enabled(backend: SpecBackend) -> SpecBackend:
    """The Azure backend behind its cache when `QUENCHING_SPECS_SYNC` asks for it, else as is."""
    if not sync_enabled() or getattr(backend, "name", "") != "azure-boards":
        return backend
    return SyncedBackend(backend, SyncCache(cache_dir(backend.org, backend.project)))
