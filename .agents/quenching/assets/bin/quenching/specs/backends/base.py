"""The spec backend interface, and the refusal an external backend carries to the CLI.

Moved verbatim out of the pre-refactor specs script. `BackendRefusal` travels with the interface rather than with
`github`, where it used to sit: both external backends raise it, and neither may import the
other."""
from __future__ import annotations

import os
import re


class SpecBackend:
    """Where a repo's specs actually live. One implementation per target; every command
    above talks to this and never to a path.

    THE INTERFACE IS THE DOCUMENT, NOT THE VERBS. `status`, `show`, `section`, `task`,
    `discover`, `promote` and `validate` are shared code layered on the five primitives
    below — they are not methods each backend reimplements. That is what makes "every
    backend behaves identically" provable by construction rather than by hoping three
    parsers agree: the canonical markdown document is the contract, the derivation of
    stages, gates, records and tasks happens once, and a backend's only job is to produce
    that document and to store it again.

    A backend is therefore free to serialise natively — `## Tasks` as sub-issues, sections
    as fields — provided it reassembles the canonical document on read. The hybrid
    serialisation lives inside each external implementation, exactly where it belongs, and
    it can never drift the JSON the CLI prints.

    Granular reading is unaffected by this split, because the cost it addresses is the
    agent's context and not I/O: `section` hands back the headings asked for whether or not
    the backend had to fetch the whole document to find them."""

    name = "abstract"

    # TRANSPORT-LEVEL READ-ONLY GUARD. Checked by the lowest-level `gh`/`az` runners for every
    # non-read call, so it holds for private helpers and future write paths alike, which a
    # list of method names cannot promise. `QUENCHING_SPECS_READ_ONLY=1` flips it for any
    # embedding that must only read; the portal honours it beside its own `--read-only`.
    read_only = False

    def list_specs(self, phase: str | None = None, lean: bool = False) -> list[dict]:
        """Every spec descriptor, or a lean native index row, oldest first within each phase."""
        raise NotImplementedError

    def read_spec(self, spec_id: str | int) -> tuple[dict | None, dict]:
        """`(info, err)` — the canonical document plus everything derived from it, or a
        ready-to-emit refusal. Never raises for an unknown ID."""
        raise NotImplementedError

    def read_specs(self, spec_ids: list) -> dict[str, dict | None]:
        """`id (str) -> info`, or `None` for an id no spec carries. The default is one
        `read_spec` each; a store that can answer N reads from one snapshot overrides it."""
        out: dict[str, dict | None] = {}
        for spec_id in spec_ids:
            info, _ = self.read_spec(spec_id)
            out[str(spec_id)] = info
        return out

    def write_spec(self, info: dict, text: str) -> None:
        """Replace one spec's whole document with `text`."""
        raise NotImplementedError

    def create_spec(self, phase: str, text: str) -> str:
        """Store a new spec and return the locator a report can show a human."""
        raise NotImplementedError

    def move_spec(self, info: dict, dest_phase: str) -> str:
        """The one lifecycle hop — `plans/` to `archive/` — returning the new locator."""
        raise NotImplementedError

    # ARTIFACTS ARE A CAPABILITY, NOT A SIXTH PRIMITIVE. They are not part of the canonical
    # document, so they do not join the five above. A store that cannot host them refuses
    # here instead of pretending, and the CLI turns that refusal into exit 2; the git store
    # is the only backend that implements the four below.
    def list_artifacts(self, spec_id: str | int | None = None) -> list[dict]:
        """`[{spec, name, path, bytes}]` for one spec's artifacts, or the standalone ones
        when `spec_id` is None."""
        raise BackendRefusal(artifact_unsupported(self.name))

    def read_artifact(self, spec_id: str | int | None, name: str) -> str:
        raise BackendRefusal(artifact_unsupported(self.name))

    def write_artifact(self, spec_id: str | int | None, name: str, text: str) -> str:
        """Store one text artifact and return its locator."""
        raise BackendRefusal(artifact_unsupported(self.name))

    def remove_artifact(self, spec_id: str | int | None, name: str) -> str:
        raise BackendRefusal(artifact_unsupported(self.name))

    # There is no `reconcile_labels` hook here, and the absence is deliberate. One existed
    # briefly — a no-op on the base class, meant for an external backend to override — and
    # nothing ever called it or overrode it, because the reconciliation belongs INSIDE
    # `write_spec`: it has to ride the store call's own request to satisfy the "costs zero
    # calls beyond the write already being made" condition
    # (/docs/standards/architecture/spec-backend.md §Rendering derived state). A hook that
    # every implementer must fold into `write_spec` anyway is a sixth primitive that does
    # nothing, and this interface stays five.


class BackendRefusal(Exception):
    """An external backend's call that failed, carried to the CLI boundary as a
    ready-to-emit refusal. Shared by `github` and `azure-boards`.

    THREE OF THE FIVE PRIMITIVES RETURN A LOCATOR AND NOT `(value, err)` — `write_spec`,
    `create_spec` and `move_spec` were shaped around a backend that cannot fail halfway.
    A network target can, and the contract is "never a traceback", so the failure travels
    as an exception carrying the refusal already built and is converted exactly once, in
    `main`. Widening the interface instead would make every backend and every command pay,
    in every signature, for a failure mode only the external backends have."""

    def __init__(self, err: dict) -> None:
        super().__init__(err.get("message", "the backend failed"))
        self.err = err


READ_ONLY_ENV = "QUENCHING_SPECS_READ_ONLY"
RETRY_AFTER_CAP_S = 30.0


def transport_read_only() -> bool:
    """Whether the transport must refuse every call that is not a read."""
    return bool(SpecBackend.read_only) or os.environ.get(READ_ONLY_ENV, "") not in ("", "0")


def read_only_refusal(tool: str, argv: tuple[str, ...]) -> BackendRefusal:
    """The refusal a non-read transport call gets while the guard is on. Nothing was sent."""
    return BackendRefusal({
        "code": "sp-read-only", "exit": 2,
        "message": f"the specs backend is read-only; `{tool} {' '.join(argv[:4])}` is not a "
                   f"read and was refused before any process started — nothing was written",
    })


ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
MAX_ARTIFACT_BYTES = 1024 * 1024
STANDALONE_OWNER = "_handoffs"


def artifact_owner(spec_id: str | int | None) -> str:
    """`<id>` for an artifact of a spec, `_handoffs` for one attached to none — never
    colliding, because an id is digits."""
    return STANDALONE_OWNER if spec_id is None else str(spec_id)


def artifact_unsupported(backend: str) -> dict:
    return {"code": "sp-artifact-unsupported", "exit": 2,
            "message": f"the '{backend}' backend holds no spec artifacts; only the git store "
                       "does, and nothing was written"}


def check_artifact(name: str, text: str) -> None:
    """The two rules every artifact obeys, refused before any write."""
    if not ARTIFACT_NAME.match(name or ""):
        raise BackendRefusal({
            "code": "sp-artifact-name", "exit": 2,
            "message": f"artifact name '{name}' is one path segment: a letter or digit first, "
                       "then up to 120 of letters, digits, '.', '_' and '-' — no '/', no '..'",
        })
    if len(text.encode("utf-8")) > MAX_ARTIFACT_BYTES:
        raise BackendRefusal({
            "code": "sp-artifact-too-large", "exit": 2,
            "message": f"the artifact is larger than {MAX_ARTIFACT_BYTES} bytes; a text "
                       "artifact is a handoff, a log or a patch, not a data dump",
        })


_RETRY_AFTER_RE = re.compile(r"retry-after\s*[:=]\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


def parse_retry_after(*texts: str) -> float | None:
    """Seconds named by a `Retry-After` header quoted in a transport's output, capped so a
    hostile or absurd value cannot stall a command, or None when none is present."""
    for text in texts:
        match = _RETRY_AFTER_RE.search(text or "")
        if match:
            return min(float(match.group(1)), RETRY_AFTER_CAP_S)
    return None
