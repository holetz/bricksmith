"""Collect a run's documentation artifacts and publish the rendered document beside them.

``collect_run`` downloads whatever of the artifact layout (:data:`PATHS`) a tracking run carries; a missing file
is skipped and renders as "not recorded". ``publish_document`` renders the document, logs the Markdown and HTML
files to the same run and, given a registered model version, sets the version tag ``bricksmith_documentation``
to the run-relative path of the HTML file. The client is any object with the ``MlflowClient`` methods used here
(``download_artifacts``, ``log_text``, ``set_model_version_tag``); tests pass an in-memory fake.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.documentation.artifacts import (
    DOCUMENT_HTML,
    DOCUMENT_MARKDOWN,
    PATHS,
    DocumentationError,
    DocumentInputs,
    read_directory,
)
from bricksmith.mlops.documentation.render import Document, render_document

if TYPE_CHECKING:
    from bricksmith.mlops.store import ModelStore

TAG_DOCUMENTATION = "bricksmith_documentation"


@dataclass(frozen=True)
class Publication:
    """What ``publish_document`` wrote: the document, the run, the two artifact paths and the tagged version."""

    document: Document
    run_id: str
    markdown_path: str = DOCUMENT_MARKDOWN
    html_path: str = DOCUMENT_HTML
    model: str | None = None
    version: int | None = None


def _missing(error: Exception) -> bool:
    text = f"{type(error).__name__} {error}".lower()
    # On a Databricks workspace MLflow reports a missing artifact only as a failed download of its path.
    words = (
        "not found",
        "notfound",
        "does not exist",
        "no such file",
        "resource_does_not",
        "failed to download artifacts",
    )
    return any(word in text for word in words)


def collect_run(client: Any, run_id: str, directory: Path | str | None = None) -> DocumentInputs:  # noqa: ANN401 - an MlflowClient
    """Download the layout artifacts of ``run_id`` and read them; artifacts the run lacks are skipped.

    Without ``directory`` the files go to a temporary directory removed after reading.
    """
    if directory is None:
        with tempfile.TemporaryDirectory(prefix="bricksmith-doc-") as tmp:
            return collect_run(client, run_id, tmp)
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    for path in PATHS:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        # Where the file lands differs by artifact store (``dst/path`` locally, ``dst/<file name>`` on a Databricks
        # workspace), so each download goes to its own directory and the returned file is moved to ``root / path``.
        with tempfile.TemporaryDirectory(dir=root) as landing:
            try:
                downloaded = client.download_artifacts(run_id, path, landing)
            except Exception as error:
                if not _missing(error):
                    raise
                continue
            Path(downloaded).replace(target)
    return read_directory(root, run_id)


def publish_document(  # noqa: PLR0913 - the version target is keyword-only
    client: Any,  # noqa: ANN401 - an MlflowClient
    inputs: DocumentInputs,
    *,
    generated_at: str | None = None,
    store: ModelStore | None = None,
    model: str | None = None,
    version: int | None = None,
) -> Publication:
    """Render ``inputs``, log both files to its run and, with ``model`` and ``version``, tag that version.

    The tag is set through ``store`` (a :class:`~bricksmith.mlops.store.ModelStore`), or through ``client`` when no
    store is given. Its value is JSON with the run id and both artifact paths.
    """
    if (model is None) != (version is None):
        msg = "documentation: tagging a version needs both model and version"
        raise DocumentationError(msg)
    document = render_document(inputs, generated_at=generated_at)
    client.log_text(inputs.run_id, document.markdown, DOCUMENT_MARKDOWN)
    client.log_text(inputs.run_id, document.html, DOCUMENT_HTML)
    if model is not None and version is not None:
        value = json.dumps(
            {"html": DOCUMENT_HTML, "markdown": DOCUMENT_MARKDOWN, "run_id": inputs.run_id},
            sort_keys=True,
            separators=(",", ":"),
        )
        if store is not None:
            store.tag_version(model, version, TAG_DOCUMENTATION, value)
        else:
            client.set_model_version_tag(model, str(version), TAG_DOCUMENTATION, value)
    return Publication(document=document, run_id=inputs.run_id, model=model, version=version)


__all__ = ["TAG_DOCUMENTATION", "Publication", "collect_run", "publish_document"]
