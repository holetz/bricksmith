"""Static checks over the operations inventory.

The functions in this module consume an :class:`Inventory` and read only the source files named
by its rows.  They never discover another file, import a target module, or execute a subprocess.
The disabled-gate check is deliberately kept for task 3.2, where its tokenizer half is added.
"""
from __future__ import annotations

import ast
import io
import os
import re
import tokenize
from pathlib import Path
from typing import Callable

from quenching.common.front import register_checks
from quenching.ops import registry
from quenching.ops.model import EntryPoint, Finding, Inventory

# Kept as a compatibility export for callers that used the pre-generator helper.
inventory_digest = registry.inventory_digest


_ARCHIVE_PART = "_archive"
_ENTRY_SUFFIXES = (".py", ".sh", ".bash", ".cmd", ".bat", ".ps1")
_ROOT_NAMES = re.compile(r"(?:^|_)(?:base|project|repo|repository|workspace|ops)?root(?:$|_)", re.I)
_ROOT_CALLS = frozenset({"abspath", "cwd", "dirname", "getcwd", "realpath", "resolve"})
_WRITE_METHODS = frozenset({
    "copy", "create", "delete", "dump", "insert", "merge", "publish", "remove", "run",
    "to_csv", "to_json", "to_pickle", "unlink", "update", "write", "write_bytes",
    "write_text",
})
_ARMING_FLAGS = frozenset({
    "--apply", "--armed", "--confirm", "--execute", "--force", "--write", "--yes",
})
def _finding(code: str, message: str, path: str | None = None,
             entry_point: str | None = None) -> Finding:
    return Finding(code, "error", message, path=path, entry_point=entry_point)


def _source(inventory: Inventory, entry: EntryPoint) -> str | None:
    if entry.language != "python":
        return None
    try:
        return (Path(inventory.root) / entry.path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _tree(inventory: Inventory, entry: EntryPoint) -> ast.AST | None:
    source = _source(inventory, entry)
    if source is None:
        return None
    try:
        return ast.parse(source, filename=entry.path)
    except SyntaxError:
        return None


def _registry_path(inventory: Inventory) -> Path:
    from quenching.ops.config import load_ops_config

    config, _ = load_ops_config(inventory.root)
    return Path(config["registry"]) if config else Path(inventory.root) / "registry.md"


def _registry_text(inventory: Inventory) -> str | None:
    try:
        return _registry_path(inventory).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _registry_rows(inventory: Inventory, text: str) -> set[str]:
    """Extract entry-point paths already named by a registry without trusting its prose."""
    rows: set[str] = set()
    known = {entry.path for entry in inventory.entry_points}
    for line in text.splitlines():
        for entry in known:
            if re.search(rf"(?<![A-Za-z0-9_./-]){re.escape(entry)}(?![A-Za-z0-9_./-])", line):
                rows.add(entry)
        for match in re.finditer(r"`([^`]+)`", line):
            candidate = match.group(1).replace("\\", "/").removeprefix("./")
            if candidate.endswith(_ENTRY_SUFFIXES):
                rows.add(candidate)
    return rows


def check_undocumented(inventory: Inventory) -> list[Finding]:
    """Active entries must be named in the generated registry."""
    text = _registry_text(inventory)
    rows = _registry_rows(inventory, text) if text is not None else set()
    findings: list[Finding] = []
    for entry in inventory.entry_points:
        if entry.lifecycle == "archived":
            continue
        if text is None or entry.path not in rows:
            findings.append(_finding(
                "op-undocumented",
                f"active entry point `{entry.path}` is not named by registry.md",
                path=entry.path, entry_point=entry.path,
            ))
    return findings


def check_registry_stale(inventory: Inventory) -> list[Finding]:
    """An existing registry must match the generator's current output exactly."""
    registry_path = _registry_path(inventory)
    if not registry_path.is_file():
        return []
    path = os.path.relpath(registry_path, inventory.root).replace(os.sep, "/")
    try:
        text = registry.read_registry(registry_path)
        expected = registry.render_registry_document(text, inventory)
    except registry.RegistryError as exc:
        return [_finding("op-registry-malformed", str(exc), path=path)]
    if expected == text:
        return []
    return [_finding(
        "op-registry-stale",
        "the registry differs from the generator's current output",
        path=os.path.relpath(_registry_path(inventory), inventory.root).replace(os.sep, "/"),
    )]


def _contains_root_derivation(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in {"__file__", "__package__"}:
            return True
        if isinstance(child, ast.Attribute) and child.attr in {"parent", "parents", "resolve"}:
            return True
        if isinstance(child, ast.Attribute) and child.attr in _ROOT_CALLS:
            return True
        if isinstance(child, ast.Name) and child.id in {"cwd", "getcwd"}:
            return True
    return False


def _assigned_names(statement: ast.stmt) -> list[str]:
    targets: list[ast.AST] = []
    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            names.extend(element.id for element in target.elts if isinstance(element, ast.Name))
    return names


def check_adhoc_root(inventory: Inventory) -> list[Finding]:
    """Module-level root derivations belong in the shared bootstrap module."""
    findings: list[Finding] = []
    for entry in inventory.entry_points:
        if entry.language != "python" or Path(entry.path).stem in {"bootstrap", "config"}:
            continue
        tree = _tree(inventory, entry)
        if not isinstance(tree, ast.Module):
            continue
        for statement in tree.body:
            names = _assigned_names(statement)
            value = statement.value if isinstance(statement, (ast.Assign, ast.AnnAssign)) else None
            if value is not None and any(_ROOT_NAMES.search(name) for name in names):
                if _contains_root_derivation(value):
                    findings.append(_finding(
                        "op-adhoc-root",
                        f"entry point `{entry.path}` derives a repository root outside the shared bootstrap",
                        path=entry.path, entry_point=entry.path,
                    ))
                    break
    return findings


def _is_main_guard(statement: ast.stmt) -> bool:
    if not isinstance(statement, ast.If):
        return False
    test = statement.test
    return isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq) \
        and isinstance(test.left, ast.Name) and test.left.id == "__name__" \
        and len(test.comparators) == 1 and isinstance(test.comparators[0], ast.Constant) \
        and test.comparators[0].value == "__main__"


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _valid_exit_value(node: ast.AST | None) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return True
    if isinstance(node, ast.Call):
        return _call_name(node.func) == "main"
    return False


def check_untyped_exit(inventory: Inventory) -> list[Finding]:
    """The main guard must carry a main result or an explicit integer exit code."""
    findings: list[Finding] = []
    for entry in inventory.entry_points:
        if entry.language != "python":
            continue
        tree = _tree(inventory, entry)
        if not isinstance(tree, ast.Module):
            continue
        for statement in tree.body:
            if not _is_main_guard(statement):
                continue
            for child in statement.body:
                if isinstance(child, ast.Expr) and isinstance(child.value, ast.Call):
                    if _call_name(child.value.func) == "main":
                        findings.append(_finding(
                            "op-untyped-exit",
                            f"entry point `{entry.path}` discards main()'s exit status",
                            path=entry.path, entry_point=entry.path,
                        ))
                elif isinstance(child, ast.Call):
                    if _call_name(child.func) in {"exit", "SystemExit"} and not _valid_exit_value(
                            child.args[0] if child.args else None):
                        findings.append(_finding(
                            "op-untyped-exit",
                            f"entry point `{entry.path}` exits with a non-contractual value",
                            path=entry.path, entry_point=entry.path,
                        ))
                elif isinstance(child, ast.Raise) and isinstance(child.exc, ast.Call):
                    if _call_name(child.exc.func) == "SystemExit" and not _valid_exit_value(
                            child.exc.args[0] if child.exc.args else None):
                        findings.append(_finding(
                            "op-untyped-exit",
                            f"entry point `{entry.path}` raises SystemExit with a non-contractual value",
                            path=entry.path, entry_point=entry.path,
                        ))
            break
    return findings


def _has_session_or_client_import(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] + [alias.name for alias in node.names]
        else:
            continue
        if any(re.search(r"(?:client|session|connection|spark|boto|databricks|requests)", name, re.I)
               for name in names):
            return True
    return False


def _has_write_call(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in _WRITE_METHODS:
                return True
    return False


def _has_parser_and_arming_flag(tree: ast.AST) -> bool:
    has_parser = False
    has_flag = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        if name == "ArgumentParser":
            has_parser = True
        if name == "add_argument":
            values = [arg.value for arg in node.args
                      if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
            has_flag = has_flag or bool(_ARMING_FLAGS.intersection(values))
    return has_parser and has_flag


def check_unarmed_write(inventory: Inventory) -> list[Finding]:
    """Potential external writes need a parser flag that visibly arms them."""
    findings: list[Finding] = []
    for entry in inventory.entry_points:
        if entry.language != "python":
            continue
        tree = _tree(inventory, entry)
        if tree is None or not _has_session_or_client_import(tree) or not _has_write_call(tree):
            continue
        if not _has_parser_and_arming_flag(tree):
            findings.append(_finding(
                "op-unarmed-write",
                f"entry point `{entry.path}` can write through a session/client without an arming flag",
                path=entry.path, entry_point=entry.path,
            ))
    return findings


def check_orphan(inventory: Inventory) -> list[Finding]:
    """Every entry point needs a lifecycle, and archived rows belong under `_archive/`."""
    findings: list[Finding] = []
    for entry in inventory.entry_points:
        in_archive = _ARCHIVE_PART in Path(entry.path).parts
        if entry.lifecycle is None:
            findings.append(_finding(
                "op-orphan",
                f"entry point `{entry.path}` has no declared lifecycle",
                path=entry.path, entry_point=entry.path,
            ))
        elif entry.lifecycle == "archived" and not in_archive:
            findings.append(_finding(
                "op-orphan",
                f"archived entry point `{entry.path}` is outside `_archive/`",
                path=entry.path, entry_point=entry.path,
            ))
    return findings


def check_no_router(inventory: Inventory) -> list[Finding]:
    """The configured router must resolve to one supported file."""
    if inventory.router.exists and inventory.router.kind:
        return []
    return [_finding(
        "op-no-router",
        "the configured canonical router does not exist or is not a supported router",
        path=inventory.router.path,
    )]


def _function_spans(tree: ast.AST) -> list[tuple[int, int, int]]:
    spans: list[tuple[int, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spans.append((node.lineno, getattr(node, "end_lineno", node.lineno), node.col_offset))
    return spans


def _comment_is_call(comment: str) -> bool:
    source = comment.partition("#")[2].strip()
    if not source:
        return False
    try:
        expression = ast.parse(source, mode="eval")
    except SyntaxError:
        return False
    return isinstance(expression.body, ast.Call)


def check_disabled_check(inventory: Inventory) -> list[Finding]:
    """Find call-shaped comments inside AST-identified entry-point control flow."""
    findings: list[Finding] = []
    for entry in inventory.entry_points:
        if entry.language != "python":
            continue
        source = _source(inventory, entry)
        tree = _tree(inventory, entry)
        if source is None or tree is None:
            continue
        spans = _function_spans(tree)
        try:
            tokens = tokenize.generate_tokens(io.StringIO(source).readline)
            for token in tokens:
                if token.type != tokenize.COMMENT or not _comment_is_call(token.string):
                    continue
                line, column = token.start
                if any(start <= line <= end and column > indent
                       for start, end, indent in spans):
                    findings.append(_finding(
                        "op-disabled-check",
                        f"entry point `{entry.path}` comments out a verification call",
                        path=entry.path, entry_point=entry.path,
                    ))
        except (IndentationError, tokenize.TokenError):
            continue
    return findings


CHECKS: tuple[Callable[[Inventory], list[Finding]], ...] = (
    check_undocumented,
    check_registry_stale,
    check_adhoc_root,
    check_untyped_exit,
    check_unarmed_write,
    check_orphan,
    check_no_router,
)

AST_CHECK_REGISTRY = register_checks(*(tuple((check.__name__, check) for check in CHECKS)))
CHECK_REGISTRY = register_checks(
    *(tuple((check.__name__, check) for check in CHECKS) +
      (("disabled-check", check_disabled_check),))
)


def run_ast_checks(inventory: Inventory) -> list[Finding]:
    """Run the seven non-tokenize checks in their stable code order."""
    return AST_CHECK_REGISTRY.run(inventory)


def run_checks(inventory: Inventory) -> list[Finding]:
    """Run all eight checks in the declared registry order."""
    return CHECK_REGISTRY.run(inventory)
