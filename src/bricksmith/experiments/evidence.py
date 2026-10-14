"""The evidence record: the frozen, serializable, write-once description of a revision."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.experiments.errors import EvidenceError, EvidenceExistsError, ExperimentError, StaleEvidenceError
from bricksmith.experiments.fingerprint import digest
from bricksmith.experiments.revision import Revision

if TYPE_CHECKING:
    from bricksmith.experiments.isolation import IsolationPlan

EVIDENCE_VERSION = 1


@dataclass(frozen=True)
class Evidence:
    """Immutable evidence: ``body`` is canonical JSON, so a value cannot change after creation."""

    body: str

    @classmethod
    def freeze(cls, plan: IsolationPlan, *, created: str) -> Evidence:
        """Freeze a ready isolation plan; ``created`` is the caller's timestamp (the plan itself has none)."""
        if not plan.ok:
            codes = ", ".join(sorted({i.code for i in plan.errors}))
            msg = f"the isolation plan has errors ({codes}); nothing can be frozen"
            raise ExperimentError(msg)
        unresolved = [i.physical for i in plan.inputs if i.mode == "frozen" and i.version is None]
        if unresolved:
            msg = f"unresolved pins: {', '.join(unresolved)}"
            raise ExperimentError(msg)
        data = {**plan.content(), "created": created, "evidence_version": EVIDENCE_VERSION}
        return cls(json.dumps(data, sort_keys=True, separators=(",", ":")))

    @property
    def data(self) -> dict[str, Any]:
        """Return a fresh copy of the frozen content."""
        return json.loads(self.body)  # type: ignore[no-any-return]

    @property
    def fingerprint(self) -> str:
        """Return the SHA-256 of the body."""
        return digest(self.data)

    @property
    def revision(self) -> Revision:
        """Return the frozen revision."""
        return Revision.from_dict(self.data["revision"])

    @property
    def name(self) -> str:
        """Return the revision name."""
        return str(self.data["revision"]["name"])

    @property
    def targets(self) -> dict[str, str]:
        """Return ``{baseline physical: isolated physical}``."""
        return dict(self.data["targets"])

    @property
    def pins(self) -> dict[str, int]:
        """Return ``{baseline physical: version}`` for frozen inputs."""
        return {i["physical"]: i["version"] for i in self.data["inputs"] if i["mode"] == "frozen"}

    @property
    def unpinned(self) -> list[str]:
        """Return the inputs recorded as read live."""
        return [i["physical"] for i in self.data["inputs"] if i["mode"] == "live"]

    def to_dict(self) -> dict[str, object]:
        """Return the file form: content and fingerprint."""
        return {"body": self.data, "fingerprint": self.fingerprint}

    def to_json(self) -> str:
        """Return stable JSON."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Evidence:
        """Rebuild evidence and verify its fingerprint; a modified record is rejected."""
        try:
            body = data["body"]
            recorded = data["fingerprint"]
        except (KeyError, TypeError) as error:
            msg = "the evidence file lacks body or fingerprint"
            raise EvidenceError(msg) from error
        found = cls(json.dumps(body, sort_keys=True, separators=(",", ":")))
        if found.fingerprint != recorded:
            msg = "the evidence does not match its fingerprint; it was modified after being written"
            raise EvidenceError(msg)
        return found

    @classmethod
    def from_json(cls, text: str) -> Evidence:
        """Parse and verify evidence JSON."""
        try:
            return cls.from_dict(json.loads(text))
        except json.JSONDecodeError as error:
            msg = f"the evidence file is not valid JSON: {error}"
            raise EvidenceError(msg) from error

    def write(self, path: Path | str) -> None:
        """Write the record once; an existing file is never replaced."""
        target = Path(path)
        try:
            with target.open("x", encoding="utf-8") as handle:
                handle.write(self.to_json())
        except FileExistsError as error:
            msg = f"evidence already exists at {target}; records are immutable"
            raise EvidenceExistsError(msg) from error

    @classmethod
    def read(cls, path: Path | str) -> Evidence:
        """Read and verify an evidence file."""
        try:
            return cls.from_json(Path(path).read_text(encoding="utf-8"))
        except OSError as error:
            msg = f"cannot read evidence {path}: {error}"
            raise EvidenceError(msg) from error

    def verify_current(self, plan: IsolationPlan) -> None:
        """Raise :class:`StaleEvidenceError` unless re-planning today reproduces what was frozen."""
        frozen = self.data
        now = plan.content()
        changed = sorted(k for k in now if now[k] != frozen.get(k))
        if changed:
            msg = f"the revision no longer matches its evidence (changed: {', '.join(changed)})"
            if "tasks" in changed:
                moved = sorted(
                    t
                    for t in {*now["tasks"], *frozen["tasks"]}  # type: ignore[attr-defined]
                    if now["tasks"].get(t) != frozen["tasks"].get(t)  # type: ignore[attr-defined]
                )
                msg += f"; task sources or declarations changed: {', '.join(moved)}"
            raise StaleEvidenceError(msg)
