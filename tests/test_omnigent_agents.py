"""Dry run of the omnigent agent bundles: synthetic tool calls, no model, standard library only."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from bricksmith.cli.main import build_parser

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "plugins" / "bricksmith-omnigent" / "agents"

VERBS: dict[str, list[str]] = {
    "bricksmith-bundle": [
        "bundle resources",
        "bundle validate",
        "tasks list",
        "tasks graph",
        "tasks validate",
        "context show",
    ],
    "bricksmith-mlops": [
        "dataset validate",
        "dataset describe",
        "model card",
        "tasks list",
        "tasks graph",
        "tasks validate",
        "context show",
    ],
    "bricksmith-analysis": [
        "ops report",
        "ops diagnose",
        "ops readiness",
        "ops explain",
        "monitor compare",
        "monitor evaluate",
        "tasks list",
        "context show",
    ],
}
FORBIDDEN_FLAGS = {"--apply", "--out", "--output", "--freeze", "--write-baseline", "--authorize", "--report-out"}
DENIED_TOOLS = [
    "Write",
    "sys_os_write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "sys_os_edit",
    "WebFetch",
    "WebSearch",
    "sys_session_create",
    "Task",
    "Agent",
    "Skill",
]
SHELL_TOOLS = ["sys_os_shell", "Bash", "Shell", "bash"]
IDS = sorted(VERBS)


def _scalar(text: str, key: str) -> str:
    match = re.search(rf"^\s*{key}: (\".*\")\s*$", text, re.MULTILINE)
    assert match, key
    return str(json.loads(match.group(1)))


def _config(agent: str) -> str:
    return (AGENTS / agent / "config.yaml").read_text(encoding="utf-8")


def _decide(agent: str, name: str, arguments: dict[str, object] | None) -> str:
    """Evaluate the fixed policy shape: a denied name list, then one anchored shell regex."""
    expression = _scalar(_config(agent), "expression")
    lists = re.findall(r"event\.data\.name in (\[[^\]]*\])", expression)
    denied, shell = (json.loads(item) for item in lists)
    regex = re.search(r'matches\(r"(.*)"\)\)\)', expression)
    assert regex
    if name in denied:
        return "DENY"
    if name in shell:
        command = (arguments or {}).get("command")
        if not isinstance(command, str) or not re.fullmatch(regex.group(1), command):
            return "DENY"
    return "ALLOW"


def _parser_flags(path: str) -> set[str]:
    node: argparse.ArgumentParser = build_parser()
    for part in path.split():
        sub = next(a for a in node._actions if isinstance(a, argparse._SubParsersAction))  # noqa: SLF001
        node = sub.choices[part]
    return {option for action in node._actions for option in action.option_strings}  # noqa: SLF001


@pytest.mark.parametrize("agent", IDS)
def test_bundle_structure(agent: str) -> None:
    text = _config(agent)
    assert text.startswith("spec_version: 1\n")
    assert _scalar(text, "name") == agent
    assert "instructions: instructions.md" in text
    assert "harness: opencode" in text
    assert "type: caller_process" in text
    assert "omnigent.policies.builtins.cel.cel_policy" in text
    assert "spawn" not in text
    assert (AGENTS / agent / "instructions.md").read_text(encoding="utf-8").strip()


@pytest.mark.parametrize("agent", IDS)
@pytest.mark.parametrize("tool", DENIED_TOOLS)
def test_denied_tools(agent: str, tool: str) -> None:
    assert _decide(agent, tool, {"file_path": "x", "content": "y"}) == "DENY"


@pytest.mark.parametrize("agent", IDS)
def test_read_tools_are_allowed(agent: str) -> None:
    for tool in ("Read", "Grep", "Glob"):
        assert _decide(agent, tool, {"file_path": "bricksmith.toml"}) == "ALLOW"


@pytest.mark.parametrize("agent", IDS)
@pytest.mark.parametrize("shell", SHELL_TOOLS)
def test_granted_verbs_are_allowed(agent: str, shell: str) -> None:
    for verb in VERBS[agent]:
        assert _decide(agent, shell, {"command": f"bricksmith {verb} --json --env example"}) == "ALLOW"
        assert _decide(agent, shell, {"command": f"bricksmith {verb}"}) == "ALLOW"
    first, second = VERBS[agent][0], VERBS[agent][-1]
    chained = f"bricksmith {first} --json && bricksmith {second} ; bricksmith {first}"
    assert _decide(agent, shell, {"command": chained}) == "ALLOW"


@pytest.mark.parametrize("agent", IDS)
def test_other_shell_calls_are_denied(agent: str) -> None:
    verb = VERBS[agent][0]
    ungranted = ["bricksmith model promote x", "bricksmith run --apply", "bricksmith env check", "ls", "cat .env"]
    if agent == "bricksmith-analysis":
        ungranted += ["bricksmith quality compare a b", "bricksmith quality check"]
    injected = [
        f"bricksmith {verb} | tee out",
        f"bricksmith {verb} > out",
        f"bricksmith {verb} < in",
        f"bricksmith {verb} $(id)",
        f"bricksmith {verb} `id`",
        f"bricksmith {verb} $HOME",
        f"bricksmith {verb} & ls",
        f"bricksmith {verb}\nls",
        f"bricksmith {verb}\n",
        f"bricksmith {verb} \\ls",
        f"bricksmith {verb} && ls",
        f"ls && bricksmith {verb}",
        f"bricksmithx {verb}",
        f"bricksmith {verb}x",
        "",
    ]
    for command in [*ungranted, *injected]:
        assert _decide(agent, "Bash", {"command": command}) == "DENY", command
    assert _decide(agent, "Bash", {}) == "DENY"
    assert _decide(agent, "Bash", None) == "DENY"
    assert _decide(agent, "Bash", {"command": 7}) == "DENY"


@pytest.mark.parametrize("agent", IDS)
def test_verbs_are_read_only_in_the_parser(agent: str) -> None:
    config = _config(agent)
    for verb in VERBS[agent]:
        assert verb in config
        flags = _parser_flags(verb)
        assert flags, verb
        assert not flags & FORBIDDEN_FLAGS, (verb, flags & FORBIDDEN_FLAGS)
    listed = set(re.search(r"bricksmith \(\?:([^)]*)\)", config).group(1).split("|"))  # type: ignore[union-attr]
    assert listed == set(VERBS[agent])


@pytest.mark.parametrize("agent", IDS)
def test_instructions_content_rules(agent: str) -> None:
    text = (AGENTS / agent / "instructions.md").read_text(encoding="utf-8")
    assert text.startswith(f"---\nname: {agent}\nallowed-tools: ")
    for verb in VERBS[agent]:
        assert f"`bricksmith {verb}`" in text
    for code in ("0", "1", "2", "3", "4"):
        assert f"{code} " in text
    for phrase in ("stays denied", "DEFAULT", "--json", "overlay"):
        assert phrase in text
    assert "`.env`" in text


@pytest.mark.parametrize("agent", IDS)
def test_frontmatter_grant_equals_the_config_grant(agent: str) -> None:
    text = (AGENTS / agent / "instructions.md").read_text(encoding="utf-8")
    match = re.match(r"---\nname: [^\n]+\nallowed-tools: ([^\n]+)\n---\n", text)
    assert match
    items = [item.strip() for item in match.group(1).split(", ")]
    assert "*" not in items
    shell = [item for item in items if item.startswith("Bash(")]
    assert shell == [f"Bash(bricksmith {verb}:*)" for verb in VERBS[agent]]
    assert [item for item in items if not item.startswith("Bash(")] == ["Read", "Grep", "Glob"]
    config = _config(agent)
    listed = set(re.search(r"bricksmith \(\?:([^)]*)\)", config).group(1).split("|"))  # type: ignore[union-attr]
    assert {item[len("Bash(bricksmith ") : -len(":*)")] for item in shell} == listed


def test_bundle_files_pass_the_generic_leak_scan() -> None:
    spec = importlib.util.spec_from_file_location("leak_check", ROOT / "scripts" / "leak_check.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    findings = []
    for path in sorted((ROOT / "plugins" / "bricksmith-omnigent").rglob("*")):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            findings += module.scan_text(str(path), text, dict(module.GENERIC_PATTERNS))
    assert not findings
