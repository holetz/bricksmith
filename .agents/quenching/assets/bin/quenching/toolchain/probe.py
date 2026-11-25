"""Discover whether a repository has a recognized toolchain surface."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


MANIFESTS = (
    "pyproject.toml",
    "setup.cfg",
    "package.json",
    "Cargo.toml",
)
LOCKS = (
    "uv.lock",
    "poetry.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
)
LANGUAGE_PINS = (".python-version", ".nvmrc")


@dataclass(frozen=True)
class Applicability:
    """The result of the probe, before any inventory interpretation."""

    root: str
    state: str
    signal: str | None
    artifacts: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "signal": self.signal,
            "artifacts": list(self.artifacts),
        }


def probe_toolchain(root: str) -> Applicability:
    """Return applicability from artifacts at the target repository root.

    The probe only establishes whether the front has something to inspect. It does not infer an
    ecosystem, require a lock, or classify a declaration as healthy.
    """
    repo_root = Path(os.path.abspath(root))
    groups = (MANIFESTS, LOCKS, LANGUAGE_PINS)
    artifacts = tuple(
        name for group in groups for name in group if (repo_root / name).is_file()
    )
    if not artifacts:
        return Applicability(str(repo_root), "not-applicable", None)
    signal = "manifest" if any(name in MANIFESTS for name in artifacts) else "toolchain-artifact"
    return Applicability(str(repo_root), "applicable", signal, artifacts)
