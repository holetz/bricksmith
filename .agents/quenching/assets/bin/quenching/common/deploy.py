"""The deploy manifest a generated target carries.

A deployed target keeps the read-only plugin payload under ``.agents/quenching/`` and the
generated skill source under ``.agents/skills/``.  The manifest is the single list of what this
tool owns there; it lets ``cq deploy`` check and upgrade without touching a target's own skills,
references, agents or configuration.
"""
from __future__ import annotations

import json
import os

MANIFEST = os.path.join(".agents", ".quenching-deploy.json")
PAYLOAD = os.path.join(".agents", "quenching")
SKILLS = os.path.join(".agents", "skills")


def manifest_path(root: str) -> str:
    return os.path.join(os.path.abspath(root), MANIFEST)


def read_manifest(root: str) -> dict | None:
    """The deploy manifest at ``root``, or ``None`` when the target was never deployed."""
    try:
        with open(manifest_path(root), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def generated_skills(root: str) -> set[str]:
    """Names of the skills a previous deploy owns, from the manifest's ``skills`` list."""
    data = read_manifest(root) or {}
    values = data.get("skills")
    if not isinstance(values, list):
        return set()
    return {value for value in values if isinstance(value, str) and value}


def generated_for_skills_dir(skills_dir: str) -> set[str]:
    """The generated names when ``skills_dir`` is a target's ``.agents/skills``."""
    path = os.path.abspath(skills_dir)
    if os.path.basename(path) != "skills" or os.path.basename(os.path.dirname(path)) != ".agents":
        return set()
    return generated_skills(os.path.dirname(os.path.dirname(path)))
