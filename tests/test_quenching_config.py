"""The two spec-store config files must stay identical and select the git backend."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(relative: str) -> object:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_quenching_configs_are_equal_and_select_git_backend() -> None:
    agents = _load(".agents/quenching.json")
    claude = _load(".claude/quenching.json")

    assert claude == agents
    assert isinstance(agents, dict)
    assert agents["backend"] == "git"
