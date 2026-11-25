"""Build the proof front's static read model.

The walker reads files and parses Python ASTs only. It never imports a target module and never
starts a test runner; later checks receive this one inventory so a finding cannot come from a
second, subtly different traversal.
"""
from __future__ import annotations

import ast
import configparser
import os
import re
import shlex
from pathlib import Path
from typing import Any

from quenching.proof.config import load_proof_config
from quenching.proof.ci import discover_ci
from quenching.proof.model import (
    Conftest,
    CostContract,
    Fixture,
    GateConfig,
    Layer,
    ProofInventory,
    TestModule,
)


# pytest's default `norecursedirs` (plus tool caches): the test walk skips these at any depth.
_SKIP_DIRS = frozenset({
    ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__",
    ".tox", ".venv", "venv", "node_modules", "build", "dist", ".eggs",
})
# Dependency folders pytest does not skip: never a top-level surface, but not skipped deeper.
_SURFACE_SKIP_TOP = _SKIP_DIRS | {"vendor"}
# Environments and caches are never source, however deep: the surface walk prunes them at any depth.
_ENV_DIRS = frozenset({".venv", "venv", ".tox", "__pycache__", "node_modules"})
_TEST_RE = re.compile(r"(?:^test_.*\.py$|^.*_test\.py$)")
_PYTEST_FIXTURE = re.compile(r"(?:^|\.)fixture$")


def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _parse(path: Path) -> ast.AST:
    try:
        return ast.parse(_read(path), filename=str(path))
    except (SyntaxError, ValueError):
        return ast.Module(body=[], type_ignores=[])


def _imports(tree: ast.AST) -> tuple[str, ...]:
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.add(node.module)
    return tuple(sorted(values))


def _is_fixture_decorator(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "fixture"
    if isinstance(node, ast.Attribute):
        return node.attr == "fixture"
    if isinstance(node, ast.Call):
        return _is_fixture_decorator(node.func)
    return False


def _fixture_names(tree: ast.AST) -> tuple[str, ...]:
    values: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(_is_fixture_decorator(decorator) for decorator in node.decorator_list):
            values.append(node.name)
    return tuple(sorted(set(values)))


def _test_count(tree: ast.AST) -> int:
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            count += 1
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            count += 1
    return count


def _layer_for(relative: str, layers: dict[str, dict[str, Any]]) -> str | None:
    parts = relative.split("/")
    return parts[0] if parts and parts[0] in layers else None


def _marker_for(layer: str | None) -> str | None:
    return layer


def _layer_models(proof_root: str, declarations: dict[str, dict[str, Any]]) -> tuple[Layer, ...]:
    result: list[Layer] = []
    for name, declaration in declarations.items():
        result.append(Layer(
            name=name,
            path=name,
            reach=declaration.get("reach"),
            budget=declaration.get("budget"),
            required=bool(declaration.get("required", False)),
        ))
    return tuple(result)


def _toml(path: Path) -> dict[str, Any]:
    try:
        import tomllib
        return tomllib.loads(_read(path))
    except (ImportError, TypeError, ValueError):
        return {}


def _gate_from_toml(data: dict[str, Any]) -> dict[str, Any]:
    pytest = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    coverage_run = data.get("tool", {}).get("coverage", {}).get("run", {})
    coverage_report = data.get("tool", {}).get("coverage", {}).get("report", {})
    return {
        "addopts": pytest.get("addopts", ""),
        "markers": pytest.get("markers", []),
        "strict_markers": pytest.get("strict_markers", False),
        "coverage_sources": coverage_run.get("source", []),
        "coverage_floor": coverage_report.get("fail_under"),
    }


def _gate_from_ini(path: Path, section_name: str = "pytest") -> dict[str, Any]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, UnicodeError, configparser.Error):
        return {}
    section = section_name if parser.has_section(section_name) else None
    if not section:
        return {"unconfigured": True}
    return {
        "addopts": parser.get(section, "addopts", fallback=""),
        "markers": [line.strip() for line in parser.get(section, "markers", fallback="").splitlines()
                    if line.strip()],
        "strict_markers": "--strict-markers" in parser.get(section, "addopts", fallback=""),
    }


def _tokens(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            return tuple(shlex.split(value))
        except ValueError:
            return tuple(value.split())
    if isinstance(value, list):
        return tuple(str(item) for item in value if isinstance(item, (str, int, float)))
    return ()


def _markers(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [item for item in value if isinstance(item, str)]
    else:
        values = []
    return tuple(sorted(set(item.split(":", 1)[0].strip() for item in values if item.strip())))


def _gate(repo_root: str, ratchet_path: str) -> GateConfig:
    root = Path(repo_root)
    path: str | None = None
    raw: dict[str, Any] = {}
    pyproject = root / "pyproject.toml"
    data = _toml(pyproject) if pyproject.is_file() else {}
    from_toml = _gate_from_toml(data) if data else {}
    for name in ("pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"):
        candidate = root / name
        if not candidate.is_file():
            continue
        if name == "pyproject.toml":
            if not isinstance(data.get("tool", {}).get("pytest", {}).get("ini_options"), dict):
                continue
            found = from_toml
        else:
            found = _gate_from_ini(
                candidate, "tool:pytest" if name == "setup.cfg" else "pytest")
            if name in ("tox.ini", "setup.cfg") and found.get("unconfigured"):
                continue
        raw, path = found, _rel(str(candidate), repo_root)
        break
    for key in ("coverage_sources", "coverage_floor"):
        if key not in raw and key in from_toml:
            raw = {**raw, key: from_toml[key]}
    coverage_sources = raw.get("coverage_sources", [])
    if isinstance(coverage_sources, str):
        coverage_sources = [coverage_sources]
    return GateConfig(
        path=path,
        addopts=_tokens(raw.get("addopts", "")),
        markers=_markers(raw.get("markers", [])),
        strict_markers=bool(raw.get("strict_markers", False)) or
                       "--strict-markers" in _tokens(raw.get("addopts", "")),
        coverage_sources=tuple(str(value) for value in coverage_sources
                               if isinstance(value, str) and value.strip()),
        coverage_floor=(float(raw["coverage_floor"])
                        if isinstance(raw.get("coverage_floor"), (int, float))
                        and not isinstance(raw.get("coverage_floor"), bool) else None),
        ratchet_path=ratchet_path,
        ratchet_exists=Path(ratchet_path).is_file(),
    )


def _source_surfaces(repo_root: str, proof_root: str) -> tuple[str, ...]:
    """Find top-level directories that contain source-like files, excluding the proof root.

    Dependency and build directories are never a surface. The front stays static, so it asks no
    subprocess (git included) which files are tracked.
    """
    suffixes = {".c", ".cpp", ".go", ".java", ".js", ".py", ".rs", ".scala", ".sh",
                ".sql", ".ts", ".tsx"}
    result: list[str] = []
    root = Path(repo_root)
    proof_name = Path(proof_root).name
    for child in sorted(root.iterdir()):
        if (not child.is_dir() or child.name.startswith(".") or child.name == proof_name
                or child.name in _SURFACE_SKIP_TOP):
            continue
        found = False
        for directory, dirnames, filenames in os.walk(child):
            dirnames[:] = sorted(name for name in dirnames if name not in _ENV_DIRS)
            if any(Path(name).suffix.lower() in suffixes for name in filenames):
                found = True
                break
        if found:
            result.append(child.name)
    return tuple(result)


def build_inventory(root: str) -> tuple[ProofInventory | None, dict]:
    """Walk one configured proof root and return a single static inventory."""
    config, err = load_proof_config(root)
    if err:
        return None, err
    assert config is not None
    repo_root = config["repoRoot"]
    proof_root = config["proofRoot"]
    if not os.path.isdir(proof_root):
        return None, {
            "code": "pf-root-missing",
            "exit": 2,
            "path": config["declaredProofRoot"],
            "message": f"configured proofRoot `{config['declaredProofRoot']}` does not exist",
        }

    layers = _layer_models(proof_root, config["layers"])
    tests: list[TestModule] = []
    fixtures: list[Fixture] = []
    conftests: list[Conftest] = []
    for directory, dirnames, filenames in os.walk(proof_root):
        dirnames[:] = sorted(name for name in dirnames if name not in _SKIP_DIRS)
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            absolute = Path(directory) / filename
            relative = _rel(str(absolute), proof_root)
            tree = _parse(absolute)
            imports = _imports(tree)
            fixture_names = _fixture_names(tree)
            is_library = relative == "fixtures/conftest.py" or relative.startswith("fixtures/")
            for name in fixture_names:
                fixtures.append(Fixture(name, relative, is_library))
            if filename == "conftest.py":
                conftests.append(Conftest(relative, relative.startswith("fixtures/"),
                                           fixture_names, imports))
            if _TEST_RE.match(filename) and not relative.startswith("fixtures/"):
                layer = _layer_for(relative, config["layers"])
                tests.append(TestModule(relative, layer, _marker_for(layer), imports,
                                        _test_count(tree)))

    gate = _gate(repo_root, config["ratchetPath"])
    measured = tuple(item["relative"] for item in config["measuredRoots"])
    exclusions = tuple(item["relative"] for item in config["proofExclusions"])
    declared_cost = config.get("cost")
    cost = (CostContract(temp_root=declared_cost.get("tempRoot"),
                         workers=declared_cost.get("workers"),
                         scopes=dict(declared_cost.get("scopes") or {}))
            if declared_cost is not None else None)
    declared_config = config.get("config")
    config_path = (_rel(declared_config, repo_root) if declared_config else None)
    return ProofInventory(
        repo_root, proof_root, layers, tuple(tests), tuple(fixtures), tuple(conftests), gate,
        discover_ci(repo_root, proof_root),
        cost=cost,
        config_path=config_path,
        measured_roots=measured,
        exclusions=exclusions,
        source_surfaces=_source_surfaces(repo_root, proof_root),
    ), {}


def inventory(root: str) -> tuple[ProofInventory | None, dict]:
    """Public alias for callers that use the operation's noun."""
    return build_inventory(root)
