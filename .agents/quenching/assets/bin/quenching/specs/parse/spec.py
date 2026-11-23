"""A spec's identity — the phases, the cosmetic handle, and the one resolution every
backend shares.

The basename grammar and the filesystem listing that used to live here are gone with the
slug: identity is the provider-native ID, and no backend has resolved a spec off a filename
since. What is left is what every backend still shares."""
from __future__ import annotations

from quenching.common.text import slugify


PHASES = ("plans", "archive")

# v2 folders. READ so an unmigrated workspace keeps working and `migrate` can fold it;
# NEVER written — `new` and `promote` only ever target a folder in PHASES. Reading them
# is not politeness: `list` globs the phase folders, so a workspace this tool refused to
# see would read as EMPTY rather than as out of date, and a skill would conclude there is
# no work when there is.
LEGACY_PHASES = ("backlog", "ready")
PHASE_ALIASES = {"backlog": "plans", "ready": "plans"}
# scan order — a spec's canonical folder before the legacy ones it may still sit in
PHASE_DIRS = ("plans", "backlog", "ready", "archive")


def canonical_phase(folder: str) -> str:
    """The schema phase a folder maps to. `plans` for both v2 definition folders, so
    stage rules, gates and `next` branch on ONE phase and never on where the file
    happens to sit mid-migration."""
    return PHASE_ALIASES.get(folder, folder)


# THE HANDLE IS COSMETIC, and the number beside it is the whole identity. `plan/974-citacao-
# pendurada` is resolved by `974` alone: the handle rides along so a human scanning
# `git branch` reads a name rather than a number, and nothing ever parses it back. It is
# derived from the title on every use rather than stored, which is what keeps it from
# becoming a second answer to "which spec is this" the way the slug was.
#
# ONE derivation, shared: `next` names branches with it and `export` names files with it, and
# a spec's branch and its dump must not disagree about what to call it.
def spec_handle(spec_id: str | int, title: str) -> str:
    return f"{spec_id}-{slugify(title) or 'spec'}"


def resolve_one(specs: list[dict], spec_id: str | int) -> tuple[dict | None, dict]:
    """Pick one spec descriptor out of a listing by its provider-native ID."""
    spec = next((s for s in specs if str(s["id"]) == str(spec_id)), None)
    if spec is not None:
        return spec, {}
    return None, {"code": "sp-unknown-id", "exit": 1, "id": spec_id,
                  "message": f"no spec with id '{spec_id}'"}
