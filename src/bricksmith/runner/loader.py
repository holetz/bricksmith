"""Import a task module at execution time, keeping relative imports inside the project working."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import threading
import types
from pathlib import Path
from typing import TYPE_CHECKING

from bricksmith.errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable

_LOCK = threading.RLock()


def _package(name: str, directory: Path) -> None:
    if name not in sys.modules:
        module = types.ModuleType(name)
        module.__path__ = [str(directory)]
        sys.modules[name] = module


def load_task_function(root: Path, path: str, function: str) -> Callable[..., object]:
    """Import ``root/path`` under a project-private package name and return ``function``.

    Parent folders become namespace packages so ``from .helpers import x`` works as discovery assumes.
    """
    root = root.resolve()
    parts = Path(path).with_suffix("").parts
    base = f"_bricksmith_project_{hashlib.sha256(str(root).encode()).hexdigest()[:10]}"
    with _LOCK:
        _package(base, root)
        name = base
        directory = root
        for part in parts[:-1]:
            name, directory = f"{name}.{part}", directory / part
            _package(name, directory)
        full = f"{name}.{parts[-1]}"
        if full not in sys.modules:
            file = root / path
            spec = importlib.util.spec_from_file_location(full, file)
            if spec is None or spec.loader is None or not file.is_file():
                msg = f"cannot load the task module {path!r}"
                raise ValidationError(msg)
            module = importlib.util.module_from_spec(spec)
            sys.modules[full] = module
            try:
                spec.loader.exec_module(module)
            except BaseException:
                del sys.modules[full]
                raise
        found = getattr(sys.modules[full], function, None)
    if not callable(found):
        msg = f"{path!r} has no callable {function!r}"
        raise ValidationError(msg)
    return found
