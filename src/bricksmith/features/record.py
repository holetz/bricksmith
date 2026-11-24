"""The engine choice record: which engine computed the window outputs, and why when it was not the one asked for.

``compute_features`` calls :func:`record` once per call. The choice is always logged on the ``bricksmith.features``
logger: a fallback as a warning with its reason, any other choice as info. Inside :func:`collecting` the choice is
also kept, and the runner opens one collector per task so each task result carries ``feature_engines``.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

LOGGER = logging.getLogger("bricksmith.features")


@dataclass(frozen=True)
class EngineChoice:
    """One engine decision.

    ``requested`` is the ``engine`` argument; ``engine`` the engine that computed the window outputs (``mixed`` when
    ``engines=`` split them); ``path`` the JVM registration path; ``reason`` why ``auto`` fell back, empty otherwise.
    """

    requested: str
    engine: str
    path: str = ""
    reason: str = ""
    outputs: int = 0

    @property
    def fallback(self) -> bool:
        """Whether the engine differs from the one the request resolved to first."""
        return bool(self.reason)

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form stored in the task result."""
        return asdict(self)


_COLLECTED: ContextVar[list[EngineChoice] | None] = ContextVar("bricksmith_feature_engines", default=None)


@contextmanager
def collecting() -> Iterator[list[EngineChoice]]:
    """Collect every choice recorded inside the block (nested blocks collect into their own list)."""
    choices: list[EngineChoice] = []
    token = _COLLECTED.set(choices)
    try:
        yield choices
    finally:
        _COLLECTED.reset(token)


def record(choice: EngineChoice) -> None:
    """Log ``choice`` and keep it in the open collector, if any."""
    if choice.fallback:
        LOGGER.warning(
            "feature engine %s ran instead of the requested %s: %s", choice.engine, choice.requested, choice.reason
        )
    else:
        LOGGER.info("feature engine %s ran (requested %s%s)", choice.engine, choice.requested, _path(choice))
    choices = _COLLECTED.get()
    if choices is not None:
        choices.append(choice)


def _path(choice: EngineChoice) -> str:
    return f", {choice.path}" if choice.path else ""
