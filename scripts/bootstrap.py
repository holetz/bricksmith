"""Shared bootstrap for the operation scripts: the one place that resolves the repository root.

Scripts run as ``python3 scripts/<name>.py``, so this directory is first on ``sys.path`` and they
import ``ROOT`` from here instead of deriving it themselves. Nothing here runs on its own.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
