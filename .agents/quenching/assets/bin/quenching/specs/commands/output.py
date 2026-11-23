"""The specs pillar's output layer and its single resolution entry point."""
from __future__ import annotations

from quenching.specs.config import load_config
from quenching.common.output import emit as _emit
from quenching.specs.backends.base import SpecBackend


def front_fields(root: str) -> dict:
    """`{"backend": …, "root": …}` — the two fields a workspace-wide payload opens with.

    ONE HELPER FOR TEN EMIT SITES, because the field was wrong at all ten in the same way and
    a fix applied ten times is a fix nine of which can rot. External backends have no local
    document root, so `root` is always `null` and the backend name says which remote owns the
    canonical document.

    THE KEY IS NOT RENAMED. `root` stays `root`: the swept consumers read it as a locator to
    show, never to compose a path from, so correcting the value is additive while renaming the
    key would break every installed target for nothing.

    `null` rather than the empty string, and rather than the declared path: an empty string
    makes every consumer invent its own vacuity test, and the declared path is the lie itself.
    `None` is the one value nobody can mistake for a directory."""
    # The specs loader, not the common one: only it reads the `git` store from the config.
    # `backend` is the store opened (`git` for the branch store); `provider` is only the host
    # the remote points at, so it is the fallback, never the answer.
    cfg = load_config(root)
    return {"backend": cfg.get("backend") or cfg.get("provider"), "root": None}


def display_locator(locator: str, root: str) -> str:
    """A backend's locator as a report should print it.

    `path` is the backend locator a human can follow. External backends return an issue or work
    item URL, so it is printed exactly as the backend gave it; no local path is composed here."""
    return locator


class Emitter:
    """The output adapter shared by the specs commands."""

    def announced(self, obj: dict) -> dict:
        return obj

    def receipt_line(self) -> str:
        return ""

    def emit(self, as_json: bool, obj: dict, human: str) -> None:
        _emit(as_json, obj, human)

    def emit_err(self, as_json: bool, err: dict) -> int:
        self.emit(as_json, {"ok": False, **{k: v for k, v in err.items() if k != "exit"}},
                  f"error: {err['message']}")
        return err.get("exit", 1)


def read_one(backend: SpecBackend, spec_id: str | int, out: Emitter) -> tuple[dict | None, dict]:
    """Resolve a human's ID through the one shared entry point."""
    return backend.read_spec(spec_id)
