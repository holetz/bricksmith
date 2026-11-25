"""Collect security evidence without changing the target repository."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from quenching.security.model import QuestionResult, SecurityReport


_WORKFLOW_SUFFIXES = frozenset({".yml", ".yaml"})
_ADVISORY_FILES = (
    ".github/dependabot.yml",
    ".github/dependabot.yaml",
    ".renovate.json",
    "renovate.json",
    "renovate.json5",
)
_ACCESS_FILES = (
    ".github/CODEOWNERS",
    "CODEOWNERS",
    "docs/CODEOWNERS",
    ".github/OWNERS",
    "OWNERS",
)


def _read(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except FileNotFoundError:
        return None, "missing"
    except (OSError, UnicodeDecodeError):
        return None, "unreadable"


def _workflow_files(root: Path) -> list[Path]:
    directory = root / ".github" / "workflows"
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.rglob("*")
                  if path.is_file() and path.suffix.lower() in _WORKFLOW_SUFFIXES)


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


_PERMISSIONS = re.compile(r"^(\s*)permissions\s*:\s*([^#]*?)\s*(?:#.*)?$")
_SCOPE = re.compile(r"^\s*([A-Za-z-]+)\s*:\s*([A-Za-z-]+)")


def _grants(lines: list[str]) -> tuple[list[str], list[str]]:
    """Every `write-all` and every scope granted `write`, as `workflow` or `job:<line>` plus the
    scope. Line-based, like the counts: an inline value, a flow mapping or the block below."""
    write_all: list[str] = []
    write_scopes: list[str] = []
    for number, line in enumerate(lines, start=1):
        match = _PERMISSIONS.match(line)
        if not match:
            continue
        indent, inline = len(match.group(1)), match.group(2)
        where = "workflow" if indent == 0 else f"job:{number}"
        if inline == "write-all":
            write_all.append(where)
            continue
        if inline.startswith("{"):
            pairs = [item.split(":", 1) for item in inline.strip("{}").split(",") if ":" in item]
        elif inline:
            continue
        else:
            pairs = []
            for below in lines[number:]:
                if not below.strip() or below.lstrip().startswith("#"):
                    continue
                if len(below) - len(below.lstrip()) <= indent:
                    break
                scope = _SCOPE.match(below)
                if scope:
                    pairs.append([scope.group(1), scope.group(2)])
        write_scopes += [f"{where}:{key.strip()}" for key, value in pairs
                         if value.strip() == "write"]
    return write_all, write_scopes


def workflow_permissions(root: Path) -> QuestionResult:
    """Report whether recognized workflows declare workflow or job permissions, and where a
    declaration grants `write-all`, the one grant unbounded by its own definition."""
    files = _workflow_files(root)
    if not files:
        return QuestionResult(
            "workflow-permissions",
            "Are workflow and job permissions explicit and bounded?",
            "missing",
            ({"path": ".github/workflows", "reason": "no workflow files measured"},),
        )

    evidence: list[dict[str, object]] = []
    for path in files:
        text, error = _read(path)
        relative = _relative(root, path)
        if error:
            evidence.append({"path": relative, "reason": error})
            continue
        assert text is not None
        lines = text.splitlines()
        workflow_declarations = sum(bool(re.match(r"^permissions\s*:", line)) for line in lines)
        job_declarations = sum(bool(re.match(r"^\s{2,}permissions\s*:", line)) for line in lines)
        write_all, write_scopes = _grants(lines)
        evidence.append({
            "path": relative,
            "workflowDeclarations": workflow_declarations,
            "jobDeclarations": job_declarations,
            "writeAll": write_all,
            "writeScopes": write_scopes,
        })

    if any("reason" in item for item in evidence):
        state = "refused"
    elif any(item["writeAll"] for item in evidence):
        state = "unbounded"
    elif any(item["workflowDeclarations"] or item["jobDeclarations"] for item in evidence):
        state = "present"
    else:
        state = "missing"
    return QuestionResult(
        "workflow-permissions",
        "Are workflow and job permissions explicit and bounded?",
        state,
        tuple(evidence),
    )


_SECRET_CLASSES = ("environment-files", "private-keys", "credential-files")
_SECRET_SAMPLES = {
    "environment-files": (".env",),
    "private-keys": ("server.pem", "server.key", "id_rsa"),
    "credential-files": ("credentials.json", "secrets.json", "token.json"),
}


def _secret_class(pattern: str) -> str | None:
    value = pattern.lower().lstrip("/")
    while value.startswith("**/"):
        value = value[3:].lstrip("/")
    value = value.rstrip("/")
    if value.startswith(".env") or value == "*.env":
        return "environment-files"
    if any(value.endswith(suffix) for suffix in (".pem", ".key", ".p12", ".pfx")) \
            or value.startswith("id_rsa"):
        return "private-keys"
    if any(token in value for token in ("secret", "credential", "token")):
        return "credential-files"
    return None


def _ignored_names(text: str, names: list[str]) -> set[str] | None:
    """Ask git itself which names the ignore text hides; None when git cannot answer."""
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    try:
        with tempfile.TemporaryDirectory() as raw:
            subprocess.run(["git", "init", "-q", raw], check=True, env=env,
                           capture_output=True)
            Path(raw, ".gitignore").write_text(text, encoding="utf-8")
            ignored: set[str] = set()
            for name in names:
                done = subprocess.run(
                    ["git", "-C", raw, "-c", "core.excludesFile=", "check-ignore",
                     "--no-index", "-q", "--", name],
                    env=env, capture_output=True)
                if done.returncode == 0:
                    ignored.add(name)
                elif done.returncode != 1:
                    return None
            return ignored
    except (OSError, subprocess.SubprocessError):
        return None


def secret_ignore_coverage(root: Path) -> QuestionResult:
    """Report recognized secret classes the repository ignore file really hides from git."""
    path = root / ".gitignore"
    text, error = _read(path)
    if error:
        return QuestionResult(
            "secret-ignore-coverage",
            "Do ignore rules cover the secret classes the repository exposes?",
            "missing" if error == "missing" else "refused",
            ({"path": ".gitignore", "reason": error},),
        )
    assert text is not None
    candidates: dict[str, list[str]] = {}
    for line in text.splitlines():
        pattern = line.strip()
        if not pattern or pattern.startswith("#") or pattern.startswith("!"):
            continue
        category = _secret_class(pattern)
        if category:
            candidates.setdefault(category, []).append(pattern)
    names = sorted({name for samples in _SECRET_SAMPLES.values() for name in samples}
                   | {p.lstrip("/") for ps in candidates.values() for p in ps
                      if not re.search(r"[*?\[]|/$", p)})
    ignored = _ignored_names(text, names)
    if ignored is None:
        return QuestionResult(
            "secret-ignore-coverage",
            "Do ignore rules cover the secret classes the repository exposes?",
            "refused",
            ({"path": ".gitignore", "reason": "git could not evaluate the ignore rules"},),
        )
    coverage: dict[str, list[str]] = {}
    for category, patterns in candidates.items():
        literal = set() if category == "environment-files" \
            else {p.lstrip("/") for p in patterns}
        if ignored & (set(_SECRET_SAMPLES[category]) | literal):
            coverage[category] = patterns
    uncovered = sorted(set(_SECRET_CLASSES) - set(coverage))
    state = "missing" if uncovered else "present"
    details: dict[str, object] = {}
    if coverage:
        details["patterns"] = coverage
    if uncovered:
        details["uncovered"] = uncovered
    return QuestionResult(
        "secret-ignore-coverage",
        "Do ignore rules cover the secret classes the repository exposes?",
        state,
        ({"path": ".gitignore", "classes": sorted(coverage)},),
        details,
    )


def advisory_dependency_configuration(root: Path) -> QuestionResult:
    """Report known static dependency-advisory configuration files."""
    candidates = [root / relative for relative in _ADVISORY_FILES]
    present = [_relative(root, path) for path in candidates if path.is_file()]
    pyproject = root / "pyproject.toml"
    text, error = _read(pyproject)
    if error == "unreadable":
        return QuestionResult(
            "advisory-dependency-configuration",
            "Is advisory dependency configuration present?",
            "refused",
            ({"path": "pyproject.toml", "reason": error},),
        )
    if text and any(marker in text.lower() for marker in ("pip-audit", "safety", "cargo-audit")):
        present.append("pyproject.toml")
    present = sorted(set(present))
    return QuestionResult(
        "advisory-dependency-configuration",
        "Is advisory dependency configuration present?",
        "present" if present else "missing",
        tuple({"path": path} for path in present)
        or ({"path": ".github/dependabot.yml", "reason": "no known advisory configuration"},),
    )


def access_ownership(root: Path) -> QuestionResult:
    """Report provider ownership declarations without inspecting owner values."""
    present = [relative for relative in _ACCESS_FILES
               if (root / relative).is_file()]
    return QuestionResult(
        "access-ownership",
        "Who may touch the relevant security surface?",
        "present" if present else "missing",
        tuple({"path": path} for path in present)
        or ({"path": ".github/CODEOWNERS", "reason": "no known ownership declaration"},),
    )


_AGENT_FILES = (".claude/settings.json", ".mcp.json")
_RUNNERS = {"npx", "bunx", "uvx", "pipx"}
_UNBOUNDED_SHELL = re.compile(r"^(Bash|PowerShell)(\((\*|:\*)?\))?$")
_NETWORK_HOOK = re.compile(r"\b(curl|wget|Invoke-WebRequest|iwr)\b", re.I)
_AGENT_QUESTION = "Are the agent harness grants committed to the repository bounded?"


def _package_pinned(spec: str) -> bool:
    """`name@1.2.3`, `@scope/name@1.2.3` or `name==1.2.3`; `@latest`, `@next` and bare are not."""
    if "==" in spec:
        return True
    name, _, version = spec[1:].rpartition("@") if spec.startswith("@") else spec.rpartition("@")
    return bool(name) and bool(re.match(r"v?\d", version))


def _unpinned_servers(servers: object) -> list[str]:
    found: list[str] = []
    for name, server in (servers.items() if isinstance(servers, dict) else ()):
        if not isinstance(server, dict):
            continue
        command = os.path.basename(str(server.get("command", ""))).lower().removesuffix(".cmd")
        args = [str(arg) for arg in server.get("args", []) if isinstance(arg, (str, int))] \
            if isinstance(server.get("args"), list) else []
        if command == "pnpm" and args[:1] == ["dlx"]:
            command, args = "npx", args[1:]
        if command not in _RUNNERS:
            continue
        packages = [arg for arg in args if not arg.startswith("-")]
        if command in {"uvx", "pipx"} and "--from" in args:
            index = args.index("--from") + 1
            packages = args[index:index + 1]
        if packages and not _package_pinned(packages[0]):
            found.append(f"{name}: {command} {packages[0]}")
    return found


def _settings_findings(document: dict) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    permissions = document.get("permissions")
    permissions = permissions if isinstance(permissions, dict) else {}
    if permissions.get("defaultMode") == "bypassPermissions":
        findings.append({"code": "bypass-permissions-default",
                         "value": "permissions.defaultMode"})
    allow = permissions.get("allow")
    for rule in allow if isinstance(allow, list) else ():
        if isinstance(rule, str) and _UNBOUNDED_SHELL.match(rule.strip()):
            findings.append({"code": "unbounded-shell-allow", "value": rule})
    if document.get("enableAllProjectMcpServers") is True:
        findings.append({"code": "all-project-mcp-enabled", "value": "enableAllProjectMcpServers"})
    hooks = document.get("hooks")
    for event, groups in (hooks.items() if isinstance(hooks, dict) else ()):
        for group in groups if isinstance(groups, list) else ():
            entries = group.get("hooks") if isinstance(group, dict) else None
            for entry in entries if isinstance(entries, list) else ():
                command = entry.get("command") if isinstance(entry, dict) else None
                if isinstance(command, str) and _NETWORK_HOOK.search(command):
                    findings.append({"code": "network-hook", "value": event})
    for server in _unpinned_servers(document.get("mcpServers")):
        findings.append({"code": "unpinned-mcp-server", "value": server})
    return findings


def agent_grants(root: Path) -> QuestionResult:
    """Report risky grants in the agent configuration the repository commits.

    Reads the shared `.claude/settings.json` and `.mcp.json` only — never `settings.local.json`,
    which is one person's machine — and names each risky grant without judging whether the target
    meant it: a default that bypasses permission prompts, an unscoped shell allow rule, every
    project MCP server enabled at once, an MCP server fetched through a runner without a pinned
    version, and a hook that reaches the network. Nothing is edited.
    """
    evidence: list[dict[str, object]] = []
    findings: list[dict[str, object]] = []
    measured = False
    for relative in _AGENT_FILES:
        text, error = _read(root / relative)
        if error == "missing":
            continue
        measured = True
        if error:
            evidence.append({"path": relative, "reason": error})
            continue
        try:
            document = json.loads(text or "")
        except ValueError:
            evidence.append({"path": relative, "reason": "invalid JSON"})
            continue
        if not isinstance(document, dict):
            evidence.append({"path": relative, "reason": "not a JSON object"})
            continue
        found = _settings_findings(document)
        evidence.append({"path": relative, "findings": len(found)})
        findings.extend({"path": relative, **item} for item in found)
    if not measured:
        return QuestionResult(
            "agent-grants", _AGENT_QUESTION, "not-measured",
            ({"path": ".claude/settings.json", "reason": "no committed agent configuration"},))
    if any("reason" in item for item in evidence):
        state = "refused"
    else:
        state = "missing" if findings else "present"
    return QuestionResult("agent-grants", _AGENT_QUESTION, state, tuple(evidence),
                          {"findings": findings} if findings else {})


_CI_QUESTION = "Does the merge still demand green CI, or has the project declared otherwise?"


def ci_enforcement(root: Path) -> QuestionResult:
    """Report `shared.ci.enforce`: `present` when CI is required (the default), `relaxed` when the
    project layer declares `when-present` or `off`, which lets a merge run on the local gate
    alone. A declaration in the user or local layer is named as ignored, never obeyed."""
    from quenching.common.config import (_load_local_layer, _load_user_layer, find_repo_root)
    from quenching.config import ci

    enforce, warnings = ci.read(str(root))
    ignored = [name for name, layer in (("local", _load_local_layer(find_repo_root(str(root)))),
                                         ("user", _load_user_layer()))
               if isinstance(layer["namespaces"].get("shared"), dict)
               and "ci" in layer["namespaces"]["shared"]]
    details: dict[str, object] = {"enforce": enforce}
    if warnings:
        details["warnings"] = warnings
    if ignored:
        details["ignoredLayers"] = ignored
    return QuestionResult("ci-enforcement", _CI_QUESTION,
                          "present" if enforce == "required" else "relaxed",
                          ({"path": ".agents/quenching.json", "key": "shared.ci.enforce"},),
                          details)


def build_report(root: str) -> SecurityReport:
    """Build all security answers from static files under ``root``."""
    repository = Path(root).resolve()
    questions = (
        workflow_permissions(repository),
        secret_ignore_coverage(repository),
        advisory_dependency_configuration(repository),
        access_ownership(repository),
        agent_grants(repository),
        ci_enforcement(repository),
    )
    return SecurityReport(str(repository), questions)
