"""Static discovery: read task declarations and literal reads from source, never importing it."""

from __future__ import annotations

import ast
import fnmatch
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bricksmith.errors import ValidationError
from bricksmith.tasks.declaration import DATA, EXTERNAL, READ_METHODS, TEMPORAL, TaskDeclaration, make_declaration
from bricksmith.tasks.diagnostics import ERROR, INFO, WARNING, Diagnostic

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from bricksmith.tasks.project import Project

DECLARED = "declared"
INFERRED = "inferred"
_DECLARATION_MODULE = "bricksmith.tasks"
_IDENTITY_FIELDS = frozenset({"kind", "name", "outputs", "variants"})
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SCOPE_DEPTH = 3
type _Function = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True, order=True)
class ReadSite:
    """One read of a task: what is referenced, how, and whether it was declared or inferred."""

    reference: str
    kind: str
    provenance: str
    path: str
    line: int


@dataclass(frozen=True)
class TaskDefinition:
    """A discovered task: its declaration, logical scope, source location and every known read."""

    catalog: str
    schema: str
    declaration: TaskDeclaration
    function: str
    path: str
    line: int
    reads: tuple[ReadSite, ...] = ()

    @property
    def qualified_name(self) -> str:
        """Return ``catalog.schema.task``, the identity before parameter variants."""
        return f"{self.catalog}.{self.schema}.{self.declaration.name}"


@dataclass(frozen=True)
class Discovery:
    """Everything found in a project: task definitions, diagnostics and the files parsed."""

    definitions: tuple[TaskDefinition, ...]
    diagnostics: tuple[Diagnostic, ...]
    files: tuple[str, ...]


@dataclass
class _Module:
    path: Path
    relative: str
    tree: ast.Module
    functions: dict[str, _Function] = field(default_factory=dict)
    decorators: set[str] = field(default_factory=set)
    imported_functions: dict[str, tuple[Path, str]] = field(default_factory=dict)
    imported_modules: dict[str, Path] = field(default_factory=dict)


@dataclass
class _Found:
    sites: set[ReadSite] = field(default_factory=set)
    dynamic: set[tuple[str, int]] = field(default_factory=set)
    unfollowed: set[tuple[str, int, str]] = field(default_factory=set)


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


class _Scanner:
    def __init__(self, project: Project) -> None:
        self.project = project
        self.root = project.root.resolve()
        self.diagnostics: list[Diagnostic] = []
        self._modules: dict[Path, _Module | None] = {}

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def report(self, code: str, severity: str, subject: str, message: str, where: tuple[str, int]) -> None:
        self.diagnostics.append(Diagnostic(code, severity, subject, message, *where))

    # -- modules and imports -------------------------------------------------------------------

    def load(self, path: Path) -> _Module | None:
        path = path.resolve()
        if path not in self._modules:
            self._modules[path] = self._parse(path)
        return self._modules[path]

    def _parse(self, path: Path) -> _Module | None:
        if not path.is_file() or not path.is_relative_to(self.root):
            return None
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, ValueError, OSError):
            return None
        module = _Module(path=path, relative=self.relative(path), tree=tree)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                module.functions[node.name] = node
            elif isinstance(node, ast.Import):
                self._plain_import(module, node)
            elif isinstance(node, ast.ImportFrom):
                self._from_import(module, node)
        return module

    @staticmethod
    def _plain_import(module: _Module, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == _DECLARATION_MODULE:
                module.decorators.add(f"{alias.asname or alias.name}.task")

    def _from_import(self, module: _Module, node: ast.ImportFrom) -> None:
        if node.level == 0:
            for alias in node.names:
                if node.module == _DECLARATION_MODULE and alias.name == "task":
                    module.decorators.add(alias.asname or alias.name)
                elif node.module == "bricksmith" and alias.name == "tasks":
                    module.decorators.add(f"{alias.asname or alias.name}.task")
            return
        base = module.path.parent
        for _ in range(node.level - 1):
            base = base.parent
        for part in (node.module or "").split("."):
            base = base / part if part else base
        for alias in node.names:
            local = alias.asname or alias.name
            submodule = self._module_file(base / alias.name)
            owner = self._module_file(base)
            if submodule is not None:
                module.imported_modules[local] = submodule
            elif owner is not None:
                module.imported_functions[local] = (owner, alias.name)

    @staticmethod
    def _module_file(base: Path) -> Path | None:
        for candidate in (base.with_name(base.name + ".py"), base / "__init__.py"):
            if candidate.is_file():
                return candidate
        return None

    def _callee(self, module: _Module, func: ast.expr) -> tuple[_Module, _Function] | None:
        if isinstance(func, ast.Name):
            if func.id in module.functions:
                return module, module.functions[func.id]
            if func.id in module.imported_functions:
                path, name = module.imported_functions[func.id]
                other = self.load(path)
                if other is not None and name in other.functions:
                    return other, other.functions[name]
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            path = module.imported_modules.get(func.value.id)
            other = self.load(path) if path is not None else None
            if other is not None and func.attr in other.functions:
                return other, other.functions[func.attr]
        return None

    # -- read inference ------------------------------------------------------------------------

    def infer(
        self, module: _Module, function: _Function, context: frozenset[str], found: _Found, seen: set[object]
    ) -> None:
        """Collect literal context reads in ``function`` and in resolvable helpers given the context."""
        key = (module.path, function.name, context)
        if key in seen or not context:
            return
        seen.add(key)
        names = set(context)
        for node in ast.walk(function):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id in names:
                names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in names:
                self._read_call(module, node, func.attr, found)
                continue
            positions = [i for i, arg in enumerate(node.args) if isinstance(arg, ast.Name) and arg.id in names]
            keywords = [k.arg for k in node.keywords if k.arg and isinstance(k.value, ast.Name) and k.value.id in names]
            if not positions and not keywords:
                continue
            callee = self._callee(module, func)
            if callee is None:
                found.unfollowed.add((module.relative, node.lineno, _dotted(func) or "<expression>"))
                continue
            arguments = callee[1].args
            positional = [arg.arg for arg in (*arguments.posonlyargs, *arguments.args)]
            accepted = {*positional, *(arg.arg for arg in arguments.kwonlyargs)}
            bound = {positional[i] for i in positions if i < len(positional)} | {k for k in keywords if k in accepted}
            self.infer(callee[0], callee[1], frozenset(bound), found, seen)

    @staticmethod
    def _read_call(module: _Module, node: ast.Call, method: str, found: _Found) -> None:
        kind = READ_METHODS.get(method)
        if kind is None:
            return
        named = [keyword.value for keyword in node.keywords if keyword.arg == "name"]
        argument = node.args[0] if node.args else (named[0] if named else None)
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str) and argument.value:
            found.sites.add(ReadSite(argument.value, kind, INFERRED, module.relative, node.lineno))
        else:
            found.dynamic.add((module.relative, node.lineno))

    # -- declarations --------------------------------------------------------------------------

    def _decorator(self, module: _Module, function: _Function) -> ast.expr | None:
        for decorator in function.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if _dotted(target) in module.decorators:
                return decorator
        return None

    def _declaration(self, module: _Module, function: _Function, decorator: ast.expr) -> TaskDeclaration | None:
        subject = f"{module.relative}:{function.name}"
        where = (module.relative, decorator.lineno)
        if not isinstance(decorator, ast.Call) or decorator.args:
            self.report("invalid_declaration", ERROR, subject, "use @task(kind=..., ...) with keywords only", where)
            return None
        values: dict[str, object] = {}
        usable = True
        for keyword in decorator.keywords:
            try:
                if keyword.arg is None:
                    raise ValueError  # noqa: TRY301 - '**mapping' is handled like any non-literal value
                values[keyword.arg] = ast.literal_eval(keyword.value)
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                name = keyword.arg or "**"
                usable = usable and name not in _IDENTITY_FIELDS and name != "**"
                message = f"{name}= is not a literal, so it cannot be read without running the module"
                self.report("non_literal_declaration", ERROR, subject, message, where)
        if not usable:
            return None
        try:
            return make_declaration(function.name, values)
        except ValidationError as error:
            self.report("invalid_declaration", ERROR, subject, error.message, where)
            return None

    def _scope(self, module: _Module) -> tuple[str, str] | None:
        parts = module.path.relative_to(self.project.catalogs_path.resolve()).parts
        if len(parts) < _SCOPE_DEPTH or not all(_IDENTIFIER.fullmatch(part) for part in parts[:2]):
            return None
        return parts[0], parts[1]

    def definitions(self, module: _Module) -> Iterator[TaskDefinition]:
        for function in module.functions.values():
            decorator = self._decorator(module, function)
            if decorator is None:
                continue
            declaration = self._declaration(module, function, decorator)
            scope = self._scope(module)
            if scope is None:
                message = "a task file must live under <catalog>/<schema>/ with identifier folder names"
                self.report("invalid_scope", ERROR, f"{module.relative}:{function.name}", message, (module.relative, 0))
            if declaration is None or scope is None:
                continue
            yield self._definition(module, function, declaration, scope)

    def _definition(
        self, module: _Module, function: _Function, declaration: TaskDeclaration, scope: tuple[str, str]
    ) -> TaskDefinition:
        where = (module.relative, function.lineno)
        sites = {ReadSite(name, DATA, DECLARED, *where) for name in declaration.reads}
        sites |= {ReadSite(name, EXTERNAL, DECLARED, *where) for name in declaration.external}
        sites |= {ReadSite(name, TEMPORAL, DECLARED, *where) for name in declaration.state}
        covered = bool(sites)
        found = _Found()
        parameters = [arg.arg for arg in (*function.args.posonlyargs, *function.args.args)]
        self.infer(module, function, frozenset(parameters[:1]), found, set())
        subject = f"{scope[0]}.{scope[1]}.{declaration.name}"
        for path, line in sorted(found.dynamic):
            message = (
                "a read name is computed at run time; it is assumed to be covered by the declared reads"
                if covered
                else "a read name is computed at run time and the task declares no reads; "
                "declare it with reads=, external= or state="
            )
            self.report("dynamic_read", INFO if covered else ERROR, subject, message, (path, line))
        for path, line, name in sorted(found.unfollowed):
            message = f"the context is passed to {name}, which discovery cannot follow; declare the reads it performs"
            self.report("unfollowed_helper", INFO if covered else WARNING, subject, message, (path, line))
        return TaskDefinition(
            catalog=scope[0],
            schema=scope[1],
            declaration=declaration,
            function=function.name,
            path=module.relative,
            line=function.lineno,
            reads=tuple(sorted(sites | found.sites)),
        )


def _candidates(project: Project) -> Iterator[Path]:
    base = project.catalogs_path
    for path in sorted(base.rglob("*.py")):
        parts = path.relative_to(base).parts
        relative = path.relative_to(project.root).as_posix()
        hidden = any(part.startswith(".") or part == "__pycache__" for part in parts)
        if not hidden and not any(fnmatch.fnmatchcase(relative, pattern) for pattern in project.exclude):
            yield path


def discover(project: Project) -> Discovery:
    """Find every task under the project's catalogs root by parsing source files.

    Only files mentioning ``bricksmith`` are parsed. Exclusions are glob patterns matched against
    the path relative to the project root (``*`` also crosses ``/``).
    """
    scanner = _Scanner(project)
    definitions: list[TaskDefinition] = []
    files: list[str] = []
    if not project.catalogs_path.is_dir():
        return Discovery((), (), ())
    for path in _candidates(project):
        relative = path.relative_to(project.root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            text = "bricksmith"
        if "bricksmith" not in text:
            continue
        files.append(relative)
        module = scanner.load(path)
        if module is None:
            scanner.report(
                "unreadable_module", ERROR, relative, "the file could not be parsed as Python", (relative, 0)
            )
            continue
        definitions.extend(scanner.definitions(module))
    definitions.sort(key=lambda item: (item.qualified_name, item.path, item.line))
    return Discovery(tuple(definitions), tuple(sorted(set(scanner.diagnostics))), tuple(files))
