"""The spec-store config file must select the git backend."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_quenching_config_selects_git_backend() -> None:
    config = json.loads((ROOT / ".agents/quenching.json").read_text(encoding="utf-8"))

    assert isinstance(config, dict)
    assert config["backend"] == "git"
