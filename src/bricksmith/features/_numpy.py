"""numpy, imported on first use: every features module imports without the ``features`` extra.

Engines write ``from bricksmith.features._numpy import np`` and use ``np`` as usual; the first attribute
access imports numpy, or raises :class:`~bricksmith.errors.MissingExtraError` naming the extra.
"""

from __future__ import annotations

from types import ModuleType
from typing import TYPE_CHECKING, Any

from bricksmith.clients import import_extra


class _LazyNumpy(ModuleType):
    """A stand-in module that imports numpy when an attribute is first read."""

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - any numpy attribute
        return getattr(import_extra("numpy", "features"), name)


if TYPE_CHECKING:
    import numpy as np
else:
    np = _LazyNumpy("numpy")

__all__ = ["np"]
