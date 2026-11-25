"""Discover whether a repository exposes a delivery workflow surface."""
from __future__ import annotations

import os
from pathlib import Path

from quenching.delivery.config import configured_provider, load_delivery_config
from quenching.delivery.model import Applicability


_WORKFLOW_TREES = (
    ("github", ".github/workflows", "github-actions"),
    ("gitlab", ".gitlab-ci.yml", "gitlab-ci"),
    ("azure", "azure-pipelines.yml", "azure-pipelines"),
    ("circleci", ".circleci/config.yml", "circleci"),
    ("buildkite", ".buildkite/pipeline.yml", "buildkite"),
    ("bitbucket", "bitbucket-pipelines.yml", "bitbucket-pipelines"),
)
_WORKFLOW_SUFFIXES = frozenset({".yaml", ".yml"})


def workflow_artifacts(root: Path) -> list[tuple[str, str, str]]:
    """Return recognized workflow files as ``(relative path, provider, kind)`` rows."""
    rows: list[tuple[str, str, str]] = []
    for provider, relative, kind in _WORKFLOW_TREES:
        candidate = root / relative
        if candidate.is_dir():
            paths = sorted(path for path in candidate.rglob("*")
                           if path.is_file() and path.suffix.lower() in _WORKFLOW_SUFFIXES)
        elif candidate.is_file():
            paths = [candidate]
        else:
            paths = []
        rows.extend((path.relative_to(root).as_posix(), provider, kind) for path in paths)
    return rows


def probe_delivery(root: str) -> Applicability:
    """Return applicability without inferring a provider or inventing a workflow tree."""
    repo_root = Path(os.path.abspath(root))
    config, error = load_delivery_config(str(repo_root))
    if error:
        return Applicability(str(repo_root), "refused", None, config_error=error)

    rows = workflow_artifacts(repo_root)
    configured = configured_provider(config)
    has_declaration = bool(config)
    providers = sorted({provider for _, provider, _ in rows})
    artifacts = [path for path, _, _ in rows]
    if has_declaration:
        config_file = Path(config["path"])
        artifacts.append(f"{config_file.parent.name}/{config_file.name}#delivery")
    if not rows and not has_declaration:
        return Applicability(str(repo_root), "not-applicable", None)
    provider = configured or (providers[0] if len(providers) == 1 else
                              "multiple" if providers else None)
    signal = ("configuration" if has_declaration and not rows
              else "workflow-tree" if rows else "configuration")
    return Applicability(str(repo_root), "applicable", signal, provider, tuple(sorted(artifacts)))
