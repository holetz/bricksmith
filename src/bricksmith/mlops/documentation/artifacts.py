"""The run artifact layout the technical model document is rendered from, and the loggers that write it.

Every file is JSON under a fixed path of the tracking run. Feature selection (``selection/*``) and the evaluation
(``evaluation/*``) are written by their own loggers; this module adds the development declaration and its
exclusion composition, the declared narrative and the model specification. ``DocumentInputs`` holds whatever of
the layout a run carries; a missing optional file is ``None`` and renders as "not recorded".
"""

from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from bricksmith.mlops.stages.config import Development

DEVELOPMENT = "development/development.json"
COMPOSITION = "development/composition.json"
NARRATIVE = "documentation/narrative.json"
SPECIFICATION = "documentation/specification.json"
SELECTION = {
    "ledger": "selection/ledger.json",
    "variables": "selection/variables.json",
    "features": "selection/features.json",
    "woe": "selection/woe.json",
}
EVALUATION = {
    "results": "evaluation/results.json",
    "distribution": "evaluation/distribution.json",
    "grades": "evaluation/grades.json",
    "evaluation": "evaluation/evaluation.json",
}
METADATA = "model/bricksmith_model.json"
#: Every artifact path the renderer reads, in a stable order.
PATHS = (DEVELOPMENT, COMPOSITION, NARRATIVE, SPECIFICATION, *SELECTION.values(), *EVALUATION.values(), METADATA)
DOCUMENT_MARKDOWN = "documentation/model_document.md"
DOCUMENT_HTML = "documentation/model_document.html"
#: Declared narrative fields, in document order. Their text carries no number: numbers come from artifacts.
NARRATIVE_FIELDS = ("purpose", "intended_use", "population", "target", "limitations", "inherent_risk", "materiality")
_DIGIT = re.compile(r"\d")


class DocumentationError(ValidationError):
    """A documentation input is invalid."""

    code = "documentation_invalid"


def _fail(message: str) -> DocumentationError:
    return DocumentationError(f"documentation: {message}")


def plain(value: object) -> Any:  # noqa: ANN401 - any JSON value
    """Return ``value`` as plain JSON data (dates as ISO text, keys sorted)."""
    return json.loads(
        json.dumps(value, sort_keys=True, default=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v))
    )


def check_narrative(narrative: Mapping[str, Any]) -> dict[str, str]:
    """Validate the declared narrative: known fields, non-empty text and no digit (numbers come from artifacts)."""
    unknown = sorted(set(narrative) - set(NARRATIVE_FIELDS))
    if unknown:
        msg = f"unknown narrative fields: {', '.join(unknown)}; allowed: {', '.join(NARRATIVE_FIELDS)}"
        raise _fail(msg)
    checked: dict[str, str] = {}
    for key in NARRATIVE_FIELDS:
        if key not in narrative:
            continue
        text = narrative[key]
        if not isinstance(text, str) or not text.strip():
            msg = f"narrative field {key!r} must be non-empty text"
            raise _fail(msg)
        if _DIGIT.search(text):
            msg = f"narrative field {key!r} contains a digit; numbers must come from persisted artifacts, not free text"
            raise _fail(msg)
        checked[key] = text.strip()
    return checked


@dataclass(frozen=True)
class DocumentInputs:
    """What one run persisted for its technical document; ``None`` marks an artifact the run does not carry."""

    run_id: str
    narrative: Mapping[str, str] = field(default_factory=dict)
    development: Mapping[str, Any] | None = None
    composition: list[dict[str, Any]] | None = None
    specification: Mapping[str, Any] | None = None
    selection: Mapping[str, Any] | None = None
    evaluation: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        """Validate the narrative."""
        check_narrative(self.narrative)

    @property
    def title(self) -> str:
        """The model name: from the model metadata, else the development, else the evaluation."""
        for source, key in ((self.metadata, "model"), (self.development, "model")):
            if source and source.get(key):
                return str(source[key])
        if self.evaluation and self.evaluation.get("evaluation"):
            return str(self.evaluation["evaluation"].get("name", "model"))
        return "model"

    def files(self) -> dict[str, Any]:
        """Return the artifacts as ``path -> JSON data`` (only those present)."""
        out: dict[str, Any] = {}
        singles = {DEVELOPMENT: self.development, COMPOSITION: self.composition, SPECIFICATION: self.specification,
                   METADATA: self.metadata, NARRATIVE: dict(self.narrative) if self.narrative else None}  # fmt: skip
        out.update({path: data for path, data in singles.items() if data is not None})
        for group, paths in ((self.selection, SELECTION), (self.evaluation, EVALUATION)):
            for key, path in paths.items():
                if group is not None and group.get(key) is not None:
                    out[path] = group[key]
        return {path: plain(out[path]) for path in PATHS if path in out}

    @classmethod
    def from_files(cls, files: Mapping[str, Any], run_id: str) -> DocumentInputs:
        """Rebuild from ``path -> JSON data`` (the layout of :data:`PATHS`)."""

        def group(paths: Mapping[str, str]) -> dict[str, Any] | None:
            found = {key: files[path] for key, path in paths.items() if path in files}
            return found or None

        return cls(
            run_id=run_id,
            narrative=files.get(NARRATIVE) or {},
            development=files.get(DEVELOPMENT),
            composition=files.get(COMPOSITION),
            specification=files.get(SPECIFICATION),
            selection=group(SELECTION),
            evaluation=group(EVALUATION),
            metadata=files.get(METADATA),
        )


def write_directory(inputs: DocumentInputs, directory: Path | str) -> list[Path]:
    """Write the artifacts of ``inputs`` under ``directory`` with the run layout; return the written files."""
    root = Path(directory)
    written: list[Path] = []
    for path, data in inputs.files().items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(target)
    return written


def read_directory(directory: Path | str, run_id: str) -> DocumentInputs:
    """Read whatever of the run layout ``directory`` holds (a downloaded run, or :func:`write_directory` output)."""
    root = Path(directory)
    files: dict[str, Any] = {}
    for path in PATHS:
        file = root / path
        if file.is_file():
            try:
                files[path] = json.loads(file.read_text(encoding="utf-8"))
            except ValueError as error:
                msg = f"{path} is not valid JSON: {error}"
                raise _fail(msg) from error
    return DocumentInputs.from_files(files, run_id)


def development_files(development: Development, composition: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Return the development artifacts: the declaration and its exclusion composition (``composition(by=sample)``)."""
    return {DEVELOPMENT: plain(development.to_dict()), COMPOSITION: plain([dict(r) for r in composition])}


def _active_mlflow() -> Any | None:  # noqa: ANN401 - the mlflow module
    try:
        mlflow: Any = importlib.import_module("mlflow")
    except ImportError:
        return None
    return mlflow if mlflow.active_run() is not None else None


def _log(files: Mapping[str, Any]) -> bool:
    mlflow = _active_mlflow()
    if mlflow is None:
        return False
    for path, data in files.items():
        mlflow.log_dict(data, path)
    return True


def log_development(development: Development, composition: Iterable[Mapping[str, Any]] = ()) -> bool:
    """Log the development declaration and its composition to the active MLflow run; ``False`` without one."""
    return _log(development_files(development, composition))


def log_narrative(narrative: Mapping[str, Any]) -> bool:
    """Validate and log the declared narrative to the active MLflow run; ``False`` without one."""
    return _log({NARRATIVE: check_narrative(narrative)})


def log_specification(specification: Mapping[str, Any]) -> bool:
    """Log a model specification (:func:`model_specification`) to the active MLflow run; ``False`` without one."""
    return _log({SPECIFICATION: plain(specification)})


__all__ = [
    "COMPOSITION",
    "DEVELOPMENT",
    "DOCUMENT_HTML",
    "DOCUMENT_MARKDOWN",
    "EVALUATION",
    "METADATA",
    "NARRATIVE",
    "NARRATIVE_FIELDS",
    "PATHS",
    "SELECTION",
    "SPECIFICATION",
    "DocumentInputs",
    "DocumentationError",
    "check_narrative",
    "development_files",
    "log_development",
    "log_narrative",
    "log_specification",
    "plain",
    "read_directory",
    "write_directory",
]
