"""The ``governance`` CLI group offline, against the packaged-style example and a live-state file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from bricksmith.cli.main import main
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION

if TYPE_CHECKING:
    import pytest

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "governance" / "governance.toml"


def base(tmp_path: Path) -> list[str]:
    return ["--json", "--env", "free", "--env-file", str(tmp_path / "none.env")]


def test_plan_and_audit_with_live_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "none.env").write_text("", encoding="utf-8")
    live = tmp_path / "live.json"
    live.write_text(json.dumps({"c.s.orders": {"exists": True}, "c.s.scratch": {}}), encoding="utf-8")
    args = ["--config", str(EXAMPLE), "--var", "catalog=c", "--var", "schema=s", "--live", str(live)]
    assert main([*base(tmp_path), "governance", "plan", *args]) == EXIT_OK
    plan = json.loads(capsys.readouterr().out)
    assert not plan["noop"]
    assert all("scratch" not in o["resource"] for o in plan["operations"])
    assert main([*base(tmp_path), "governance", "audit", *args]) == EXIT_VALIDATION
    out = json.loads(capsys.readouterr().out)
    assert out["report"]["states"]["desired"] > 0
    assert out["report"]["resources"][0]["enforcement"]["effective_masking"] is False
