"""The ``bricksmith.toml`` settings used by discovery and graph validation."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping

PROJECT_FILE = "bricksmith.toml"
DEFAULT_CATALOGS_ROOT = "catalogs"


@dataclass(frozen=True)
class Exemption:
    """A dated waiver for one validation rule on matching subjects (``*`` is the only wildcard)."""

    rule: str
    subject: str
    reason: str
    expires: date

    def matches(self, code: str, subject: str) -> bool:
        """Tell whether this exemption covers a diagnostic."""
        pattern = ".*".join(re.escape(part) for part in self.subject.split("*"))
        return code == self.rule and re.fullmatch(pattern, subject) is not None


@dataclass(frozen=True)
class Project:
    """Where tasks live and how logical names map to physical ones."""

    root: Path
    catalogs_root: str = DEFAULT_CATALOGS_ROOT
    exclude: tuple[str, ...] = ()
    catalogs: Mapping[str, str] = field(default_factory=dict)
    schemas: Mapping[str, str] = field(default_factory=dict)
    exemptions: tuple[Exemption, ...] = ()

    @property
    def catalogs_path(self) -> Path:
        """Return the directory holding ``<catalog>/<schema>`` folders."""
        return self.root / self.catalogs_root


def _table(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        msg = f"{PROJECT_FILE}: [{key}] must be a table"
        raise ConfigError(msg)
    return value


def _names(data: Mapping[str, Any], key: str) -> dict[str, str]:
    table = _table(data, key)
    if not all(isinstance(value, str) and value for value in table.values()):
        msg = f"{PROJECT_FILE}: [mappings.{key}] values must be non-empty strings"
        raise ConfigError(msg)
    return dict(sorted(table.items()))


def _exemption(entry: object) -> Exemption:
    if not isinstance(entry, dict):
        msg = f"{PROJECT_FILE}: each [[exemptions]] entry must be a table"
        raise ConfigError(msg)
    texts = {key: entry.get(key) for key in ("rule", "subject", "reason")}
    if not all(isinstance(value, str) and value.strip() for value in texts.values()):
        msg = f"{PROJECT_FILE}: an exemption needs non-empty rule, subject and reason"
        raise ConfigError(msg)
    expires = entry.get("expires")
    if not isinstance(expires, date):
        msg = f"{PROJECT_FILE}: exemption for {texts['subject']!r} needs an expires date (expires = 2027-01-31)"
        raise ConfigError(msg)
    return Exemption(
        rule=str(texts["rule"]), subject=str(texts["subject"]), reason=str(texts["reason"]), expires=expires
    )


def load_project(root: Path | str = ".") -> Project:
    """Read ``bricksmith.toml`` under ``root``; a missing file yields the defaults.

    Only the ``[tasks]``, ``[mappings]`` and ``[[exemptions]]`` tables are read here; other
    capabilities own their own tables.
    """
    base = Path(root)
    path = base / PROJECT_FILE
    if not path.is_file():
        return Project(root=base)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        msg = f"{PROJECT_FILE} is not valid TOML: {error}"
        raise ConfigError(msg) from None
    tasks = _table(data, "tasks")
    catalogs_root = tasks.get("root", DEFAULT_CATALOGS_ROOT)
    exclude = tasks.get("exclude", [])
    if not isinstance(catalogs_root, str) or not catalogs_root:
        msg = f"{PROJECT_FILE}: tasks.root must be a relative directory name"
        raise ConfigError(msg)
    if not isinstance(exclude, list) or not all(isinstance(item, str) for item in exclude):
        msg = f"{PROJECT_FILE}: tasks.exclude must be a list of glob patterns"
        raise ConfigError(msg)
    mappings = _table(data, "mappings")
    exemptions = data.get("exemptions", [])
    if not isinstance(exemptions, list):
        msg = f"{PROJECT_FILE}: exemptions must be an array of tables"
        raise ConfigError(msg)
    return Project(
        root=base,
        catalogs_root=catalogs_root,
        exclude=tuple(exclude),
        catalogs=_names(mappings, "catalogs"),
        schemas=_names(mappings, "schemas"),
        exemptions=tuple(_exemption(entry) for entry in exemptions),
    )
