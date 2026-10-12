"""The leak gate must block what it promises to block, in every mode."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "leak_check.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("leak_check", SCRIPT)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


leak_check = load_script()


def run(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway git repository with a fake organization denylist."""
    root = tmp_path / "repo"
    root.mkdir()
    run(root, "init", "-q", "-b", "main")
    run(root, "config", "user.name", "Tester")
    run(root, "config", "user.email", "tester@example.org")
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("# fake organization\nacme-corp\n\\bwidget_[0-9]{4}\\b\n", encoding="utf-8")
    monkeypatch.setenv("BRICKSMITH_DENYLIST_FILE", str(denylist))
    monkeypatch.chdir(root)
    return root


def commit(repo: Path, name: str, content: str) -> str:
    (repo / name).write_text(content, encoding="utf-8")
    run(repo, "add", name)
    run(repo, "commit", "-q", "-m", f"add {name}")
    return run(repo, "rev-parse", "HEAD").strip()


def test_clean_staged_content_passes(repo: Path) -> None:
    (repo / "ok.py").write_text("print('hello')\n", encoding="utf-8")
    run(repo, "add", "ok.py")
    assert leak_check.main(["--staged"]) == 0


def test_denylisted_term_in_staged_file_is_blocked(repo: Path) -> None:
    (repo / "bad.md").write_text("Built at ACME-Corp.\n", encoding="utf-8")
    run(repo, "add", "bad.md")
    assert leak_check.main(["--staged"]) == 1


def test_author_email_is_checked_at_commit_time(repo: Path) -> None:
    run(repo, "config", "user.email", "someone@acme-corp.example")
    (repo / "ok.py").write_text("x = 1\n", encoding="utf-8")
    run(repo, "add", "ok.py")
    assert leak_check.main(["--staged"]) == 1


def test_generic_patterns_apply_without_denylist(repo: Path) -> None:
    # Built at runtime so this file never carries a literal match for the gate.
    digits = "123.456.789"
    cpf = f"{digits}-09"
    commit(repo, "data.csv", f"id,doc\n1,{cpf}\n")
    assert leak_check.main(["--all", "--generic-only"]) == 1


def test_missing_denylist_fails_closed(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRICKSMITH_DENYLIST_FILE", str(repo / "absent.txt"))
    assert leak_check.main(["--all"]) == 2


def test_push_scans_added_lines_and_messages_of_new_commits(repo: Path) -> None:
    base = commit(repo, "a.txt", "fine\n")
    head = commit(repo, "b.txt", "uses widget_0042 here\n")
    assert leak_check.main(["--push", head, base]) == 1
    assert leak_check.main(["--push", base, leak_check.ZERO_SHA]) == 0


def test_deleted_branch_push_scans_nothing(repo: Path) -> None:
    head = commit(repo, "a.txt", "fine\n")
    assert leak_check.main(["--push", leak_check.ZERO_SHA, head]) == 0


def test_script_does_not_match_its_own_generic_patterns() -> None:
    findings = leak_check.scan_text("leak_check.py", SCRIPT.read_text(encoding="utf-8"), leak_check.GENERIC_PATTERNS)
    assert findings == []
