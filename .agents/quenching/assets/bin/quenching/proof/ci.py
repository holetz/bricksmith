"""Discover CI definitions that invoke the proof gate.

CI detection is intentionally pattern-based and read-only. It reports which provider file was
seen, the lines that look like a proof invocation, and whether an order-randomizing option is
present. It never executes a workflow or a test runner.
"""
from __future__ import annotations

import os
from pathlib import Path

from quenching.proof.model import CIInvocation


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _candidates(root: Path) -> list[tuple[Path, str]]:
    result: list[tuple[Path, str]] = []
    workflows = root / ".github" / "workflows"
    if workflows.is_dir():
        result.extend((path, "github-actions") for path in sorted(workflows.rglob("*.y*ml")))
    for path, provider in (
        (root / ".gitlab-ci.yml", "gitlab-ci"),
        (root / ".gitlab-ci.yaml", "gitlab-ci"),
        (root / "azure-pipelines.yml", "azure-pipelines"),
        (root / "azure-pipelines.yaml", "azure-pipelines"),
        (root / ".pre-commit-config.yaml", "pre-commit"),
        (root / ".pre-commit-config.yml", "pre-commit"),
    ):
        if path.is_file():
            result.append((path, provider))
    return result


def _proof_lines(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        lowered = line.lower()
        if any(token in lowered for token in (
            "cq proof", "proof doctor", "proof ratchet", "proof gate", "pytest", "coverage run",
        )):
            lines.append(line.strip())
    return lines


def _randomizes(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in (
        "pytest-randomly", "--random-order", "--randomly-seed", "random_order", "random-order",
    ))


def discover_ci(repo_root: str, proof_root: str | None = None) -> tuple[CIInvocation, ...]:
    """Return deterministic CI invocation rows, including files that do not invoke proof."""
    root = Path(repo_root)
    result: list[CIInvocation] = []
    for path, provider in _candidates(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        lines = _proof_lines(text)
        command = " | ".join(lines)
        result.append(CIInvocation(
            path=_relative(path, root),
            provider=provider,
            command=command,
            runs_gate=bool(lines),
            randomizes_order=_randomizes(text),
        ))
    return tuple(sorted(result, key=lambda item: item.path))
