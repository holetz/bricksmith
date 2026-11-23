"""Collecting a run's documentation artifacts and publishing the document, over an in-memory tracking client."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from bricksmith.mlops.documentation import DocumentationError, DocumentInputs, write_directory
from bricksmith.mlops.documentation.artifacts import DOCUMENT_HTML, DOCUMENT_MARKDOWN, NARRATIVE, SPECIFICATION
from bricksmith.mlops.documentation.publish import TAG_DOCUMENTATION, collect_run, publish_document
from bricksmith.mlops.store import ModelStore


class NotFoundError(Exception):
    """What the tracking client raises for an artifact the run does not carry."""


class FakeClient:
    """Run artifacts held in a directory; texts and version tags kept in memory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.texts: dict[tuple[str, str], str] = {}
        self.tags: dict[tuple[str, str], dict[str, str]] = {}

    def download_artifacts(self, run_id: str, path: str, dst_path: str) -> str:
        source = self.root / run_id / path
        if not source.is_file():
            msg = f"RESOURCE_DOES_NOT_EXIST: {path}"
            raise NotFoundError(msg)
        target = Path(dst_path) / path  # like MlflowClient: the artifact path is kept under dst_path
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(shutil.copy(source, target))

    def log_text(self, run_id: str, text: str, artifact_file: str) -> None:
        self.texts[run_id, artifact_file] = text

    def set_model_version_tag(self, name: str, version: str, key: str, value: str) -> None:
        self.tags.setdefault((name, version), {})[key] = value


def inputs() -> DocumentInputs:
    return DocumentInputs.from_files(
        {
            NARRATIVE: {"purpose": "rank accounts"},
            SPECIFICATION: {
                "estimator": "Tree",
                "features": ["a"],
                "kind": "tree",
                "importance": [{"feature": "a", "importance": 1.0}],
            },
        },
        "run-1",
    )


def test_collect_run_reads_what_the_run_carries(tmp_path: Path) -> None:
    write_directory(inputs(), tmp_path / "store" / "run-1")
    client = FakeClient(tmp_path / "store")
    collected = collect_run(client, "run-1")
    assert collected == inputs()
    kept = collect_run(client, "run-1", tmp_path / "out")
    assert kept == inputs()
    assert (tmp_path / "out" / NARRATIVE).is_file()


def test_collect_run_propagates_other_errors(tmp_path: Path) -> None:
    class Broken(FakeClient):
        def download_artifacts(self, run_id: str, path: str, dst_path: str) -> str:  # noqa: ARG002
            msg = "permission denied"
            raise PermissionError(msg)

    with pytest.raises(PermissionError):
        collect_run(Broken(tmp_path), "run-1")


def test_publish_logs_both_files_and_tags_the_version(tmp_path: Path) -> None:
    client = FakeClient(tmp_path)
    done = publish_document(client, inputs(), generated_at="t", store=ModelStore(client), model="m.s.attrition",
                            version=3)  # fmt: skip
    assert client.texts["run-1", DOCUMENT_MARKDOWN] == done.document.markdown
    assert client.texts["run-1", DOCUMENT_HTML] == done.document.html
    tag = json.loads(client.tags["m.s.attrition", "3"][TAG_DOCUMENTATION])
    assert tag == {"html": DOCUMENT_HTML, "markdown": DOCUMENT_MARKDOWN, "run_id": "run-1"}
    assert (done.model, done.version) == ("m.s.attrition", 3)


def test_publish_without_a_version_only_logs(tmp_path: Path) -> None:
    client = FakeClient(tmp_path)
    publish_document(client, inputs(), generated_at="t")
    assert set(client.texts) == {("run-1", DOCUMENT_MARKDOWN), ("run-1", DOCUMENT_HTML)}
    assert not client.tags
    publish_document(client, inputs(), generated_at="t", model="m", version=1)
    assert TAG_DOCUMENTATION in client.tags["m", "1"]


def test_publish_refuses_half_a_version(tmp_path: Path) -> None:
    client: Any = FakeClient(tmp_path)
    with pytest.raises(DocumentationError, match="needs both model and version"):
        publish_document(client, inputs(), generated_at="t", model="m")
