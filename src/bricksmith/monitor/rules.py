"""Limit rules: reviewable data that picks the limits (or the informational status) of one result.

A rule has an ``id`` and a ``version``, optional selectors (``dataset``, ``column``, ``segment``, ``metric``; absent
or empty means any) and optional validity dates, and one body: ``limits`` (a limits mapping, or ``null`` to disable
the judgment) or ``informational_segments`` / ``controlled_segments`` (segment values that are computed and shown
but never classified or gated).

Among the matching rules the most specific wins: a set ``dataset`` scores 4, ``segment`` 2 and ``column`` 1; equal
scores go to the highest ``version``, then to the smallest ``id``. Limits rules and informational rules are
resolved independently over the same result key.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import ABSOLUTE, Limits

DATASET_SCORE = 4
SEGMENT_SCORE = 2
COLUMN_SCORE = 1
SELECTORS = ("dataset", "segment", "column", "metric")
KEYS = frozenset({
    "id", "version", "dataset", "column", "segment", "metric", "valid_from", "valid_to", "limits",
    "informational_segments", "controlled_segments",
})  # fmt: skip
INFORMATIONAL_REASON = "informational segment"


@dataclass(frozen=True)
class LimitRule:
    """One validated rule; ``limits`` is meaningful only when ``has_limits`` (``None`` then disables the judgment)."""

    id: str
    version: int = 1
    dataset: str = ""
    column: str = ""
    segment: str = ""
    metric: str = ""
    valid_from: str = ""
    valid_to: str = ""
    limits: Limits | None = None
    has_limits: bool = False
    informational_segments: tuple[str, ...] = ()
    controlled_segments: tuple[str, ...] = ()

    @property
    def informational(self) -> bool:
        """Tell whether the rule marks segments informational (else it sets limits)."""
        return bool(self.informational_segments or self.controlled_segments)

    @property
    def score(self) -> int:
        """Return the specificity: dataset 4, segment 2 (an informational list counts as one), column 1."""
        segment = bool(self.segment) or self.informational
        return DATASET_SCORE * bool(self.dataset) + SEGMENT_SCORE * segment + COLUMN_SCORE * bool(self.column)

    def matches(self, *, dataset: str, column: str, segment: str, metric: str, period_date: str | None) -> bool:
        """Tell whether the rule applies to one result: selectors equal or absent, validity covering the period."""
        if (self.dataset and self.dataset != dataset) or (self.column and self.column != column):
            return False
        if (self.segment and self.segment != segment) or (self.metric and self.metric != metric):
            return False
        if self.valid_from or self.valid_to:
            if not period_date:
                return False
            if (self.valid_from and period_date < self.valid_from) or (self.valid_to and period_date > self.valid_to):
                return False
        return True

    def marks_informational(self, segment: str) -> bool:
        """Tell whether ``segment`` is informational under this rule (the whole population never is)."""
        if not segment:
            return False
        if self.informational_segments:
            return segment in self.informational_segments
        return bool(self.controlled_segments) and segment not in self.controlled_segments

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form (selectors and bounds only when set)."""
        data: dict[str, Any] = {"id": self.id, "version": self.version}
        data |= {k: getattr(self, k) for k in (*SELECTORS, "valid_from", "valid_to") if getattr(self, k)}
        if self.has_limits:
            data["limits"] = self.limits.to_dict() if self.limits else None
        if self.informational_segments:
            data["informational_segments"] = list(self.informational_segments)
        if self.controlled_segments:
            data["controlled_segments"] = list(self.controlled_segments)
        return data


@dataclass(frozen=True)
class Resolution:
    """The rules that apply to one result: the limits rule, the informational rule and the informational verdict."""

    limits_rule: LimitRule | None = None
    informational_rule: LimitRule | None = None
    informational: bool = False

    @property
    def rule(self) -> LimitRule | None:
        """Return the rule a result row reports: the informational rule when informational, else the limits rule."""
        return self.informational_rule if self.informational else self.limits_rule

    @property
    def rule_id(self) -> str:
        """Return the reported rule id (empty without a rule)."""
        return self.rule.id if self.rule else ""

    @property
    def rule_version(self) -> int:
        """Return the reported rule version (``0`` without a rule)."""
        return self.rule.version if self.rule else 0


def _best(candidates: list[LimitRule]) -> LimitRule | None:
    """Return the highest score, then highest version, then smallest id."""
    if not candidates:
        return None
    top = max((r.score, r.version) for r in candidates)
    return min((r for r in candidates if (r.score, r.version) == top), key=lambda r: r.id)


def resolve(  # noqa: PLR0913 - one keyword per selector
    rules: tuple[LimitRule, ...],
    *,
    dataset: str,
    column: str,
    segment: str,
    metric: str,
    period_date: str | None,
) -> Resolution:
    """Return the limits rule and the informational verdict that apply to one result."""
    if not rules:
        return Resolution()
    key = {"dataset": dataset, "column": column, "segment": segment, "metric": metric, "period_date": period_date}
    matching = [r for r in rules if r.matches(**key)]
    limits_rule = _best([r for r in matching if r.has_limits])
    info_rule = _best([r for r in matching if r.informational])
    return Resolution(limits_rule, info_rule, bool(info_rule and info_rule.marks_informational(segment)))


def _text(data: dict[str, Any], key: str, who: str) -> str:
    value = data.get(key) or ""
    if not isinstance(value, str):
        msg = f"{who}: {key} must be text, got {value!r}"
        raise MonitorError(msg)
    return value


def _day(data: dict[str, Any], key: str, who: str) -> str:
    text = _text(data, key, who)
    if text:
        try:
            date.fromisoformat(text)
        except ValueError:
            msg = f"{who}: {key} must be an ISO date (YYYY-MM-DD), got {text!r}"
            raise MonitorError(msg) from None
    return text


def _values(data: dict[str, Any], key: str, who: str) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        msg = f"{who}: {key} must be a non-empty list of segment values"
        raise MonitorError(msg)
    return tuple(value)


def _rule(item: object, position: int, owner: str, kinds: tuple[str, ...]) -> LimitRule:
    if not isinstance(item, dict):
        msg = f"{owner}: rules item {position} must be a mapping, got {item!r}"
        raise MonitorError(msg)
    name = item.get("id")
    if not isinstance(name, str) or not name.strip():
        msg = f"{owner}: rules item {position} needs an id"
        raise MonitorError(msg)
    who = f"{owner}: rule {name!r}"
    unknown = sorted(set(item) - KEYS)
    if unknown:
        msg = f"{who}: unknown keys {', '.join(unknown)}"
        raise MonitorError(msg)
    version = item.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        msg = f"{who}: version must be an integer >= 1, got {version!r}"
        raise MonitorError(msg)
    informational = _values(item, "informational_segments", who)
    controlled = _values(item, "controlled_segments", who)
    bodies = sum((bool(informational), bool(controlled), "limits" in item))
    if bodies != 1:
        msg = f"{who}: give exactly one of limits, informational_segments or controlled_segments"
        raise MonitorError(msg)
    if (informational or controlled) and item.get("segment"):
        msg = f"{who}: an informational rule lists its segments; it takes no segment selector"
        raise MonitorError(msg)
    if "limits" in item and not item.get("metric"):
        msg = f"{who}: a limits rule needs a metric"
        raise MonitorError(msg)
    limits = _limits(item.get("limits"), who, kinds)
    valid_from, valid_to = _day(item, "valid_from", who), _day(item, "valid_to", who)
    if valid_from and valid_to and valid_from > valid_to:
        msg = f"{who}: valid_from {valid_from} is after valid_to {valid_to}"
        raise MonitorError(msg)
    return LimitRule(
        id=name, version=version, dataset=_text(item, "dataset", who), column=_text(item, "column", who),
        segment=_text(item, "segment", who), metric=_text(item, "metric", who), valid_from=valid_from,
        valid_to=valid_to, limits=limits, has_limits="limits" in item, informational_segments=informational,
        controlled_segments=controlled,
    )  # fmt: skip


def _limits(value: object, who: str, kinds: tuple[str, ...]) -> Limits | None:
    if value is None:
        return None
    limits = Limits.from_dict(value)
    if limits.kind not in kinds:
        msg = f"{who}: limits kind {limits.kind!r} is not supported here (allowed: {', '.join(kinds)})"
        raise MonitorError(msg)
    return limits


def load_rules_file(path: str | Path, *, root: Path | str | None = None) -> list[Any]:
    """Read the rule mappings of a TOML (``[[rules]]``) or JSON (``{"rules": [...]}`` or a list) project file.

    A relative ``path`` resolves against the project ``root`` when one is given, else against the working directory;
    an absolute path is used as given.
    """
    file = Path(path) if root is None else Path(root) / path
    try:
        if file.suffix == ".toml":
            data = tomllib.loads(file.read_text(encoding="utf-8"))
        elif file.suffix == ".json":
            data = json.loads(file.read_text(encoding="utf-8"))
        else:
            msg = f"rules file {str(file)!r} must be .toml or .json"
            raise MonitorError(msg)
    except (OSError, ValueError) as error:
        msg = f"rules file {str(file)!r} cannot be read: {error}"
        raise MonitorError(msg) from None
    found = data.get("rules") if isinstance(data, dict) else data
    if not isinstance(found, list):
        msg = f"rules file {str(file)!r} must hold a list of rules under 'rules'"
        raise MonitorError(msg)
    return found


def parse_rules(
    value: object, *, owner: str = "rules", kinds: tuple[str, ...] = (ABSOLUTE,), root: Path | str | None = None
) -> tuple[LimitRule, ...]:
    """Validate an inline list of rule mappings, or the path of a rules file, into rules.

    ``kinds`` are the limit kinds a rule may use (stability: absolute only). An ``(id, version)`` pair is unique.
    A relative rules file path resolves against ``root`` (the project root) when given.
    """
    if value is None:
        return ()
    items = load_rules_file(value, root=root) if isinstance(value, str) else value
    if not isinstance(items, list):
        msg = f"{owner}: rules must be a list of rule mappings or the path of a rules file"
        raise MonitorError(msg)
    rules = tuple(_rule(item, i, owner, kinds) for i, item in enumerate(items, start=1))
    seen = [(r.id, r.version) for r in rules]
    if len(set(seen)) != len(seen):
        msg = f"{owner}: rules repeat an (id, version) pair"
        raise MonitorError(msg)
    return rules


__all__ = [
    "INFORMATIONAL_REASON",
    "LimitRule",
    "Resolution",
    "load_rules_file",
    "parse_rules",
    "resolve",
]
