"""Model metadata (the model card source), runtime capture and compatibility, and card rendering.

Standard library only: the metadata travels inside the artifact as ``bricksmith_model.json`` and is mirrored on
the tracking run and the registered version.
"""

from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.errors import ModelError, ModelIncompatibleError

if TYPE_CHECKING:
    from collections.abc import Mapping

FORMAT = "bricksmith.model/1"
FILE = "bricksmith_model.json"
CARD_FIELDS = ("summary", "intended_use", "limitations", "owner")
#: Packages whose versions are recorded; ``python`` is always recorded.
TRACKED_PACKAGES = ("mlflow", "scikit-learn", "numpy", "pandas")
#: Dependencies whose major.minor must equal the recording runtime for the artifact to load.
STRICT = ("python", "scikit-learn", "numpy")


def check_card(card: Mapping[str, str]) -> None:
    """Reject card fields outside :data:`CARD_FIELDS`."""
    extra = set(card) - set(CARD_FIELDS)
    if extra:
        msg = f"unknown model card fields: {', '.join(sorted(extra))}; allowed: {', '.join(CARD_FIELDS)}"
        raise ModelError(msg)


def capture_runtime(extra: tuple[str, ...] = ()) -> dict[str, str]:
    """Return the installed versions of the tracked packages and of the interpreter (sorted)."""
    found = {"python": platform.python_version()}
    for name in (*TRACKED_PACKAGES, *extra):
        try:
            found[name] = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            continue
    return dict(sorted(found.items()))


def code_revision(cwd: str | None = None) -> str:
    """Return the current Git commit (suffixed ``+dirty`` with local edits), or ``unknown`` outside a repository."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return head + ("+dirty" if dirty else "") if head else "unknown"


def _minor(version: str) -> str:
    return ".".join(version.split(".")[:2])


def check_runtime(recorded: Mapping[str, str], installed: Mapping[str, str]) -> list[str]:
    """Return one message per strict dependency whose major.minor differs (or is missing) in ``installed``."""
    problems: list[str] = []
    for name in STRICT:
        want = recorded.get(name)
        if want is None:
            continue
        have = installed.get(name)
        if have is None:
            problems.append(f"{name} {want} was recorded but is not installed")
        elif _minor(want) != _minor(have):
            problems.append(f"{name} {want} was recorded, runtime has {have}")
    return problems


@dataclass(frozen=True)
class ModelMetadata:
    """Everything needed to reproduce, review and load one trained model; the source of the model card."""

    model: str
    adapter: str
    features: tuple[tuple[str, str], ...]
    label: str
    key: tuple[str, ...]
    snapshot: str
    spec_config: str
    datasets: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    parameters: Mapping[str, Any] = field(default_factory=dict)
    metrics: Mapping[str, Mapping[str, float | None]] = field(default_factory=dict)
    dependencies: Mapping[str, str] = field(default_factory=dict)
    code_revision: str = "unknown"
    run_id: str = ""
    trained_at: str = ""
    card: Mapping[str, str] = field(default_factory=dict)
    migrated_from: int | None = None
    task: str = "binary_classification"

    def __post_init__(self) -> None:
        """Reject unknown card fields."""
        check_card(self.card)

    def to_dict(self) -> dict[str, Any]:
        """Return the plain, sorted form written into the artifact."""
        return {
            "adapter": self.adapter,
            "card": dict(sorted(self.card.items())),
            "code_revision": self.code_revision,
            "datasets": {k: dict(v) for k, v in sorted(self.datasets.items())},
            "dependencies": dict(sorted(self.dependencies.items())),
            "features": [{"name": n, "type": t} for n, t in self.features],
            "format": FORMAT,
            "key": list(self.key),
            "label": self.label,
            "metrics": {k: dict(v) for k, v in sorted(self.metrics.items())},
            "migrated_from": self.migrated_from,
            "model": self.model,
            "parameters": dict(sorted(self.parameters.items())),
            "run_id": self.run_id,
            "snapshot": self.snapshot,
            "spec_config": self.spec_config,
            "task": self.task,
            "trained_at": self.trained_at,
        }

    def to_json(self) -> str:
        """Return the stable JSON text."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ModelMetadata:
        """Rebuild from :meth:`to_dict`; an unknown format is incompatible."""
        if data.get("format") != FORMAT:
            msg = f"unsupported model metadata format {data.get('format')!r}; expected {FORMAT}"
            raise ModelIncompatibleError(msg)
        try:
            return cls(
                model=data["model"],
                adapter=data["adapter"],
                features=tuple((f["name"], f["type"]) for f in data["features"]),
                label=data["label"],
                key=tuple(data["key"]),
                snapshot=data["snapshot"],
                spec_config=data["spec_config"],
                datasets=data.get("datasets", {}),
                parameters=data.get("parameters", {}),
                metrics=data.get("metrics", {}),
                dependencies=data.get("dependencies", {}),
                code_revision=data.get("code_revision", "unknown"),
                run_id=data.get("run_id", ""),
                trained_at=data.get("trained_at", ""),
                card=data.get("card", {}),
                migrated_from=data.get("migrated_from"),
                task=data.get("task", "binary_classification"),
            )
        except (KeyError, TypeError) as error:
            msg = f"model metadata is incomplete: {error}"
            raise ModelIncompatibleError(msg) from error

    @classmethod
    def from_json(cls, text: str) -> ModelMetadata:
        """Parse the artifact file text."""
        try:
            return cls.from_dict(json.loads(text))
        except ValueError as error:
            msg = f"model metadata is not valid JSON: {error}"
            raise ModelIncompatibleError(msg) from error


def render_card(  # noqa: PLR0913 - keyword-only registry facts the caller knows
    meta: ModelMetadata,
    *,
    name: str = "",
    version: int | None = None,
    run_id: str = "",
    aliases: tuple[str, ...] = (),
    state: str = "",
    history: tuple[Mapping[str, Any], ...] = (),
) -> str:
    """Render the model card (Markdown) from metadata only, plus the registry facts the caller passes in."""
    title = f"{meta.model}" + (f" v{version}" if version is not None else "")
    lines = [f"# Model card: {title}", ""]
    facts = [("registered name", name), ("state", state), ("aliases", ", ".join(aliases)), ("task", meta.task)]
    facts += [("adapter", meta.adapter), ("trained at", meta.trained_at), ("code revision", meta.code_revision)]
    facts += [
        ("run", run_id or meta.run_id),
        ("re-logged from version", "" if meta.migrated_from is None else str(meta.migrated_from)),
    ]
    lines += [f"- {k}: {v}" for k, v in facts if v]
    for key in CARD_FIELDS:
        if meta.card.get(key):
            lines += ["", f"## {key.replace('_', ' ').capitalize()}", "", meta.card[key]]
    lines += ["", "## Feature contract", ""]
    lines += [f"- `{n}`: {t}" for n, t in meta.features]
    lines += [f"- label: `{meta.label}`; key: {', '.join(meta.key)}; snapshot: `{meta.snapshot}`"]
    lines += ["", "## Datasets", ""]
    for role, info in sorted(meta.datasets.items()):
        inputs = ", ".join(f"{t}@{v}" for t, v in sorted(info.get("inputs", {}).items())) or "none"
        lines.append(
            f"- {role}: fingerprint `{str(info.get('fingerprint', ''))[:12]}`, rows {info.get('rows', '?')}, "
            f"inputs {inputs}"
        )
    lines += ["", "## Parameters", ""] + [f"- {k}: {v}" for k, v in sorted(meta.parameters.items())]
    lines += ["", "## Metrics", ""]
    for split, values in sorted(meta.metrics.items()):
        lines += [
            f"- {split}: "
            + ", ".join(f"{k}={'n/a' if v is None else format(v, '.4f')}" for k, v in sorted(values.items()))
        ]
    lines += ["", "## Dependencies", ""] + [f"- {k} {v}" for k, v in sorted(meta.dependencies.items())]
    if history:
        lines += ["", "## Review history", ""]
        lines += [
            f"- {e.get('action', '?')} `{e.get('alias', '')}` v{e.get('to', '?')} by {e.get('reviewer', '?')}: "
            f"{e.get('reason', '')}"
            for e in history
        ]
    return "\n".join(lines) + "\n"
