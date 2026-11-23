"""The `memory` backend — specs in a dict, the other side of the equivalence check.

Moved verbatim out of the pre-refactor specs script."""
from __future__ import annotations

from quenching.common.leak import guard_store_write
from quenching.specs.backends.base import (BackendRefusal, SpecBackend, artifact_owner,
                                           check_artifact)
from quenching.specs.parse import PHASES, derive_info, derive_labels, resolve_one


class MemoryBackend(SpecBackend):
    """Specs in a dict. No disk, no network, no repository to stage.

    This exists to be the OTHER side of the selftest's equality: the canonical case list runs
    against the provider backends and against this, and the two must agree. A backend that
    shares nothing with the filesystem but the interface is the only honest way to prove the
    interface is what the CLI depends on — if a command reaches around it to a path, this backend is where
    that shows up, immediately and without a fixture.

    It is deliberately NOT a cache and never reachable from the config: nothing a human can
    declare selects it, because a store that forgets on exit must never be somewhere real
    work can land."""

    name = "memory"

    def __init__(self) -> None:
        # Native ID -> (phase, document). The fake allocates integer IDs so it exercises the
        # same interface as the provider backends without inventing a filename.
        self.docs: dict[int, tuple[str, str]] = {}
        # (owner, name) -> text. `owner` is the spec id as a string, or `_handoffs`.
        self.artifacts: dict[tuple[str, str], str] = {}
        self._next_id = 1

    def _descriptor(self, spec_id: int) -> dict:
        phase, _ = self.docs[spec_id]
        return {"id": spec_id, "phase": phase, "folder": phase, "legacy": False,
                "path": f"memory://{phase}/{spec_id}"}

    def list_specs(self, phase: str | None = None, lean: bool = False) -> list[dict]:
        rows = []
        for spec_id in self.docs:
            if phase is not None and self.docs[spec_id][0] != phase:
                continue
            descriptor = self._descriptor(spec_id)
            if lean:
                info = derive_info(descriptor, self.docs[spec_id][1])
                descriptor.update({
                    "title": info["frontmatter"].get("title", ""),
                    "state": "closed" if descriptor["phase"] == "archive" else "open",
                    "records": derive_labels(info),
                    "epic": info["frontmatter"].get("epic"),
                    "workItemType": info["frontmatter"].get("workItemType"),
                })
            rows.append(descriptor)
        return sorted(rows, key=lambda r: (PHASES.index(r["phase"]), r["id"]))

    def read_spec(self, spec_id: str | int) -> tuple[dict | None, dict]:
        spec, err = resolve_one(self.list_specs(), spec_id)
        if err:
            return None, err
        return derive_info(spec, self.docs[spec["id"]][1]), {}

    def write_spec(self, info: dict, text: str) -> None:
        phase, held = self.docs[info["id"]]
        self.docs[info["id"]] = (phase, guard_store_write(text, None, held, BackendRefusal))

    def create_spec(self, phase: str, text: str) -> str:
        text = guard_store_write(text, None, None, BackendRefusal)
        spec_id = self._next_id
        self._next_id += 1
        self.docs[spec_id] = (phase, text)
        return f"memory://{phase}/{spec_id}"

    def move_spec(self, info: dict, dest_phase: str) -> str:
        _, text = self.docs[info["id"]]
        self.docs[info["id"]] = (dest_phase, text)
        return f"memory://{dest_phase}/{info['id']}"

    # -- the artifact capability ------------------------------------------------ #
    def _owner(self, spec_id) -> str:
        """The owner key, refusing an id this store does not hold: an attached artifact with
        no spec to attach to is a typo, never a standalone one."""
        owner = artifact_owner(spec_id)
        if owner != "_handoffs" and not any(str(i) == owner for i in self.docs):
            raise BackendRefusal({"code": "sp-unknown-id", "exit": 2,
                                  "message": f"no spec with id '{spec_id}' to attach the "
                                             "artifact to"})
        return owner

    def list_artifacts(self, spec_id: str | int | None = None) -> list[dict]:
        owner = self._owner(spec_id)
        rows = [{"spec": None if owner == "_handoffs" else int(owner), "name": name,
                 "path": f"memory://artifacts/{owner}/{name}"}
                for (held, name) in self.artifacts if held == owner]
        return sorted(rows, key=lambda row: row["name"])

    def read_artifact(self, spec_id: str | int | None, name: str) -> str:
        owner = self._owner(spec_id)
        if (owner, name) not in self.artifacts:
            raise BackendRefusal({"code": "sp-artifact-unknown", "exit": 2,
                                  "message": f"no artifact '{name}' for {owner} in the "
                                             "memory store"})
        return self.artifacts[(owner, name)]

    def write_artifact(self, spec_id: str | int | None, name: str, text: str) -> str:
        owner = self._owner(spec_id)
        check_artifact(name, text)
        self.artifacts[(owner, name)] = guard_store_write(
            text, None, self.artifacts.get((owner, name)), BackendRefusal)
        return f"memory://artifacts/{owner}/{name}"

    def remove_artifact(self, spec_id: str | int | None, name: str) -> str:
        owner = self._owner(spec_id)
        if self.artifacts.pop((owner, name), None) is None:
            raise BackendRefusal({"code": "sp-artifact-unknown", "exit": 2,
                                  "message": f"no artifact '{name}' for {owner} in the "
                                             "memory store"})
        return f"memory://artifacts/{owner}/{name}"
