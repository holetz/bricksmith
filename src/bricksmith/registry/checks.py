"""Findings, naming checks, ratchet baseline, exceptions and the offline ``check``."""

from __future__ import annotations

import datetime as dt
import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from bricksmith.registry.errors import fail
from bricksmith.registry.model import UNCLASSIFIED, ColumnSpec, Registry

_CASE_PATTERNS = {
    "snake": r"[a-z][a-z0-9]*(_[a-z0-9]+)*",
    "upper_snake": r"[A-Z][A-Z0-9]*(_[A-Z0-9]+)*",
    "kebab": r"[a-z][a-z0-9]*(-[a-z0-9]+)*",
    "lower": r"[a-z][a-z0-9]*",
    "camel": r"[a-z][a-zA-Z0-9]*",
    "pascal": r"[A-Z][a-zA-Z0-9]*",
}
_TOKEN = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z0-9])")


if TYPE_CHECKING:
    from bricksmith.registry.preset import Preset, ScopeRule


class _Add(Protocol):
    """Callable that records one raw finding."""

    def __call__(self, rule: str, subject: str, message: str, location: str) -> None:
        """Record the finding."""


@dataclass(frozen=True)
class Finding:
    """One violation. ``id`` is stable across runs and independent of message wording."""

    id: str
    rule: str
    severity: str
    message: str
    location: str
    status: str = "active"  # active | baselined | excepted

    def to_dict(self) -> dict[str, str]:
        """Return the JSON form."""
        return {
            "id": self.id,
            "rule": self.rule,
            "severity": self.severity,
            "status": self.status,
            "location": self.location,
            "message": self.message,
        }


@dataclass(frozen=True)
class CheckReport:
    """Result of a check: all findings sorted by id plus the ratchet verdict."""

    findings: tuple[Finding, ...]
    stale_baseline: tuple[str, ...]

    @property
    def failing(self) -> tuple[Finding, ...]:
        """Active error findings: the ones that fail the check."""
        return tuple(f for f in self.findings if f.status == "active" and f.severity == "error")

    @property
    def ok(self) -> bool:
        """True when nothing fails."""
        return not self.failing

    def to_dict(self) -> dict[str, object]:
        """Stable JSON form (sorted findings, sorted keys when dumped)."""
        return {
            "ok": self.ok,
            "failing": len(self.failing),
            "findings": [f.to_dict() for f in self.findings],
            "stale_baseline": list(self.stale_baseline),
        }

    def to_json(self) -> str:
        """Serialise with sorted keys."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def to_text(self) -> str:
        """Human-readable lines, one per finding, then a verdict."""
        lines = [f"{f.severity:7} {f.status:9} {f.id}  ({f.location}) {f.message}" for f in self.findings]
        lines.append(f"{'OK' if self.ok else 'FAILED'}: {len(self.failing)} failing of {len(self.findings)} findings")
        if self.stale_baseline:
            lines.append(f"{len(self.stale_baseline)} baseline entries no longer occur; refresh the baseline")
        return "\n".join(lines)


def tokens(name: str) -> list[str]:
    """Split a name into lowercase tokens on separators and case boundaries."""
    return [token.lower() for token in _TOKEN.findall(name)]


def check_name(scope: str, name: str, rule: ScopeRule, vocabulary: tuple[str, ...]) -> list[tuple[str, str]]:
    """Return ``(rule_id, message)`` pairs for every naming rule ``name`` breaks in ``scope``."""
    out: list[tuple[str, str]] = []
    if rule.case != "any" and not re.fullmatch(_CASE_PATTERNS[rule.case], name):
        out.append(("naming.case", f"{scope} name '{name}' is not {rule.case}"))
    allowed = {t.lower() for t in rule.allowed_tokens}
    if rule.vocabulary:
        allowed |= {t.lower() for t in vocabulary}
    if allowed or rule.vocabulary:
        bad = sorted({t for t in tokens(name) if t not in allowed})
        if bad:
            out.append(("naming.token", f"{scope} name '{name}' uses tokens outside the vocabulary: {bad}"))
    if rule.prefixes and not name.startswith(rule.prefixes):
        out.append(("naming.prefix", f"{scope} name '{name}' must start with one of {list(rule.prefixes)}"))
    if rule.suffixes and not name.endswith(rule.suffixes):
        out.append(("naming.suffix", f"{scope} name '{name}' must end with one of {list(rule.suffixes)}"))
    if not rule.min_length <= len(name) <= rule.max_length:
        out.append(
            ("naming.length", f"{scope} name '{name}' length {len(name)} outside {rule.min_length}..{rule.max_length}")
        )
    if name.lower() in {r.lower() for r in rule.reserved}:
        out.append(("naming.reserved", f"{scope} name '{name}' is a reserved word"))
    return out


def _name_findings(registry: Registry, preset: Preset, add: _Add) -> None:
    names: dict[tuple[str, str], str] = {}
    for column in registry.columns:
        names.setdefault(("column", column), "columns")
    for table, spec in registry.tables.items():
        catalog, schema, short = table.split(".")
        names.setdefault(("catalog", catalog), table)
        names.setdefault(("schema", schema), table)
        names.setdefault(("table", short), table)
        for column in spec.columns:
            names.setdefault(("column", column), table)
    for (scope, name), location in sorted(names.items()):
        for rule, message in check_name(scope, name, preset.scopes[scope], preset.vocabulary):
            add(rule, f"{scope}:{name}", message, location)


def _table_findings(registry: Registry, preset: Preset, add: _Add) -> None:
    for table, tspec in registry.tables.items():
        if not tspec.grain:
            add("registry.grain_missing", table, f"table '{table}' declares no grain", table)
        for col in tspec.grain:
            if col not in tspec.columns:
                add("registry.grain_unknown", f"{table}.{col}", f"grain column '{col}' is not in '{table}'", table)
        for col, override in tspec.columns.items():
            resolved = registry.resolve(table, col)
            if resolved is None:
                add("registry.column_unknown", f"{table}.{col}", f"column '{col}' is undefined", table)
            elif col not in registry.columns or {"type", "sensitivity", "description"} & set(override):
                _check_column(add, resolved, f"{table}.{col}", table, preset)


def _raw_findings(registry: Registry, preset: Preset) -> dict[str, tuple[str, str, str]]:
    """Collect ``id -> (rule, message, location)`` before severity, baseline and exceptions."""
    found: dict[str, tuple[str, str, str]] = {}

    def add(rule: str, subject: str, message: str, location: str) -> None:
        found.setdefault(f"{rule}:{subject}", (rule, message, location))

    _name_findings(registry, preset, add)
    for column in registry.duplicates:
        add("registry.duplicate_column", column, f"column '{column}' is defined more than once", "columns")
    for column, spec in registry.columns.items():
        _check_column(add, spec, column, "columns", preset)
    _table_findings(registry, preset, add)
    return found


def _check_column(add: _Add, spec: ColumnSpec, subject: str, where: str, preset: Preset) -> None:
    column = spec.name
    if not spec.type:
        add("registry.type_missing", subject, f"column '{column}' has no logical type", where)
    elif re.sub(r"\(.*\)$", "", spec.type).lower() not in preset.logical_types:
        add("registry.type_unknown", subject, f"column '{column}' type '{spec.type}' is not allowed", where)
    if spec.sensitivity != UNCLASSIFIED and spec.sensitivity not in preset.sensitivity_levels:
        add(
            "registry.sensitivity_invalid",
            subject,
            f"column '{column}' sensitivity '{spec.sensitivity}' unknown",
            where,
        )
    if not spec.description.strip():
        add("registry.description_missing", subject, f"column '{column}' has no description", where)


def severity_for(rule: str, preset: Preset) -> str:
    """Resolve a rule's severity: exact key, then the longest matching glob, else ``error``."""
    if rule in preset.severity:
        return preset.severity[rule]
    matches = [key for key in preset.severity if fnmatch.fnmatchcase(rule, key)]
    return preset.severity[max(matches, key=len)] if matches else "error"


def load_baseline(path: Path | str) -> frozenset[str]:
    """Read a baseline file; a missing file means no tolerated violations."""
    target = Path(path)
    if not target.exists():
        return frozenset()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if data["version"] != 1:
            fail("baseline has an unsupported version")
        return frozenset(str(item) for item in data["findings"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        fail(f"{target}: invalid baseline file ({error})")


def write_baseline(path: Path | str, report: CheckReport) -> None:
    """Record the error findings of ``report`` as tolerated (not excepted ones)."""
    ids = sorted(f.id for f in report.findings if f.severity == "error" and f.status != "excepted")
    Path(path).write_text(json.dumps({"version": 1, "findings": ids}, indent=2) + "\n", encoding="utf-8")


def run_check(
    registry: Registry,
    preset: Preset,
    baseline: frozenset[str] = frozenset(),
    today: dt.date | None = None,
) -> CheckReport:
    """Run every check offline. Baselined errors are tolerated, new ones fail, expired exceptions fail."""
    today = today or dt.datetime.now(tz=dt.UTC).date()
    raw = _raw_findings(registry, preset)
    live: list[tuple[str, str]] = []  # (pattern, expires) of active exemptions
    expired: list[Finding] = []
    for exemption in preset.exemptions:
        try:
            deadline = dt.date.fromisoformat(exemption.expires)
        except ValueError:
            fail(f"exception '{exemption.finding}': invalid date '{exemption.expires}'")
        if deadline >= today:
            live.append((exemption.finding, exemption.expires))
        else:
            rule = "exception.expired"
            expired.append(
                Finding(
                    f"{rule}:{exemption.finding}", rule, severity_for(rule, preset),
                    f"exception for '{exemption.finding}' expired on {exemption.expires}: {exemption.reason}",
                    "exceptions",
                )
            )  # fmt: skip
    findings: list[Finding] = list(expired)
    seen: set[str] = set()
    for finding_id, (rule, message, location) in raw.items():
        severity = severity_for(rule, preset)
        if severity == "off":
            continue
        seen.add(finding_id)
        if any(fnmatch.fnmatchcase(finding_id, pattern) for pattern, _ in live):
            status = "excepted"
        elif severity == "error" and finding_id in baseline:
            status = "baselined"
        else:
            status = "active"
        findings.append(Finding(finding_id, rule, severity, message, location, status))
    findings.sort(key=lambda f: f.id)
    return CheckReport(tuple(findings), tuple(sorted(baseline - seen)))
