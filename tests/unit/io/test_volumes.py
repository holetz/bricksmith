"""Path validation, bounded artifact I/O, both clients, shared schemas and materialization policies."""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from bricksmith.errors import AuthorizationError, ValidationError
from bricksmith.io import (
    ArtifactNotFoundError,
    ArtifactStore,
    FilesApiVolumeClient,
    FilesystemVolumeClient,
    LimitExceededError,
    MaterializationPolicy,
    Materializer,
    PathError,
    PermanentIoError,
    Table,
    VolumeRef,
    validate_relative,
)

if TYPE_CHECKING:
    from pathlib import Path


class NotFound(Exception):  # noqa: N818 - mirrors the SDK class name the client matches on
    pass


class FakeFiles:
    """In-memory stand-in for ``workspace.files`` with the SDK's method names."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def upload(self, file_path: str, contents: io.BytesIO, *, overwrite: bool | None = None) -> None:
        if file_path in self.blobs and not overwrite:
            msg = "exists"
            raise ValueError(msg)
        self.blobs[file_path] = contents.read()

    def download(self, file_path: str) -> SimpleNamespace:
        if file_path not in self.blobs:
            raise NotFound(file_path)
        return SimpleNamespace(contents=io.BytesIO(self.blobs[file_path]))

    def delete(self, file_path: str) -> None:
        if file_path not in self.blobs:
            raise NotFound(file_path)
        del self.blobs[file_path]

    def get_metadata(self, file_path: str) -> SimpleNamespace:
        if file_path not in self.blobs:
            raise NotFound(file_path)
        return SimpleNamespace(content_length=len(self.blobs[file_path]))

    def list_directory_contents(self, directory_path: str) -> list[SimpleNamespace]:
        prefix = directory_path.rstrip("/") + "/"
        names: dict[str, SimpleNamespace] = {}
        for path, blob in self.blobs.items():
            if path.startswith(prefix):
                head, _, rest = path[len(prefix) :].partition("/")
                names[head] = SimpleNamespace(name=head, is_directory=bool(rest), file_size=None if rest else len(blob))
        return list(names.values())


@pytest.mark.parametrize(
    "bad",
    ["", "/etc/passwd", "../x", "a/../b", "a/./b", "a//b", "a\\b", "a/..", "%2e%2e/x", "a\x00b", "...", "x" * 2000],
)
def test_path_validation_rejects_escapes(bad: str) -> None:
    with pytest.raises(PathError):
        validate_relative(bad)


def test_path_validation_accepts_ordinary_names() -> None:
    assert validate_relative("reports/2026-01/summary v1.json") == "reports/2026-01/summary v1.json"
    assert validate_relative("dir/") == "dir"
    assert validate_relative("", allow_empty=True) == ""


def test_volume_names_are_explicit_and_validated() -> None:
    ref = VolumeRef.parse("cat_a.sch_b.vol_c")
    assert ref.root == "/Volumes/cat_a/sch_b/vol_c"
    for bad in ("a.b", "a.b.c.d", "a.b.c/../d", "a.b.1c"):
        with pytest.raises(PathError):
            VolumeRef.parse(bad)


def test_artifact_store_on_the_files_api_client() -> None:
    files = FakeFiles()
    store = ArtifactStore("cat.sch.one", FilesApiVolumeClient(files), max_bytes=64)
    path = store.write("reports/a.json", "{}")
    assert path == "/Volumes/cat/sch/one/reports/a.json"
    assert store.read_text("reports/a.json") == "{}"
    assert store.exists("reports/a.json")
    store.write_json("reports/b.json", {"k": 1})
    assert [e.name for e in store.list("reports")] == ["a.json", "b.json"]
    assert [(e.name, e.is_dir) for e in store.list()] == [("reports", True)]
    store.delete("reports/a.json")
    assert not store.exists("reports/a.json")
    with pytest.raises(ArtifactNotFoundError):
        store.read("reports/a.json")
    with pytest.raises(LimitExceededError):
        store.write("big.bin", b"x" * 65)
    files.blobs[store.path("raw.bin")] = b"y" * 100
    with pytest.raises(LimitExceededError):
        store.read("raw.bin")
    with pytest.raises(PathError):
        store.write("../other", "x")


def test_two_artifact_resources_share_one_schema_without_naming_rules() -> None:
    files = FakeFiles()
    client = FilesApiVolumeClient(files)
    first = ArtifactStore("cat.shared.exports", client)
    second = ArtifactStore("cat.shared.scratch", client)
    first.write("same.txt", "one")
    second.write("same.txt", "two")
    assert first.read_text("same.txt") == "one"
    assert second.read_text("same.txt") == "two"
    assert sorted(files.blobs) == ["/Volumes/cat/shared/exports/same.txt", "/Volumes/cat/shared/scratch/same.txt"]


def test_filesystem_client_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "Volumes"
    store = ArtifactStore("cat.sch.vol", FilesystemVolumeClient(root), max_bytes=32)
    store.write("a/b.txt", "hello")
    assert (root / "cat/sch/vol/a/b.txt").read_text() == "hello"
    assert store.read_text("a/b.txt") == "hello"
    assert [e.name for e in store.list("a")] == ["b.txt"]
    assert store.list("missing") == []
    with pytest.raises(PermanentIoError):
        store.write("a/b.txt", "again", overwrite=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "cat/sch/vol/link").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks are not permitted on this system: {error}")
    with pytest.raises(PathError):
        store.write("link/stolen.txt", "x")
    assert not (outside / "stolen.txt").exists()
    store.delete("a/b.txt")
    with pytest.raises(ArtifactNotFoundError):
        store.delete("a/b.txt")
    with pytest.raises(LimitExceededError):
        store.write("big", b"z" * 33)


def _materializer(*, enabled: bool = True, **policy: int) -> tuple[Materializer, FakeFiles]:
    files = FakeFiles()
    store = ArtifactStore("cat.sch.mat", FilesApiVolumeClient(files))
    return Materializer(store, MaterializationPolicy(enabled=enabled, **policy)), files  # type: ignore[arg-type]


TABLE = Table(("id", "label"), ((1, "a"), (2, "b")))


def test_materialization_is_opt_in() -> None:
    disabled, files = _materializer(enabled=False)
    for call in (
        lambda: disabled.checkpoint("run1", "t", TABLE),
        lambda: disabled.restore("run1", "t"),
        lambda: disabled.export("run1", "t", TABLE),
        lambda: disabled.cleanup("run1"),
    ):
        with pytest.raises(AuthorizationError):
            call()
    assert files.blobs == {}


def test_checkpoint_restore_export_and_cleanup() -> None:
    mat, files = _materializer()
    assert mat.restore("run1", "t") is None
    mat.checkpoint("run1", "t", TABLE)
    assert mat.restore("run1", "t") == TABLE
    csv_path = mat.export("run1", "t", TABLE)
    assert csv_path.endswith("/_bricksmith/run1/t.csv")
    assert files.blobs[csv_path].decode() == "id,label\n1,a\n2,b\n"
    jsonl = mat.export("run1", "t", TABLE, fmt="jsonl")
    assert files.blobs[jsonl].decode().splitlines()[0] == '{"id": 1, "label": "a"}'
    mat.checkpoint("run2", "t", TABLE)
    assert mat.cleanup("run1") == 3
    assert [p for p in files.blobs if "run1" in p] == []
    assert [p for p in files.blobs if "run2" in p]  # other runs are untouched
    with pytest.raises(ValidationError):
        mat.export("run1", "t", TABLE, fmt="parquet")
    with pytest.raises(ValidationError):
        mat.checkpoint("../run", "t", TABLE)


def test_materialization_limits() -> None:
    mat, _ = _materializer(max_rows=1)
    with pytest.raises(LimitExceededError):
        mat.checkpoint("r", "t", TABLE)
    mat, _ = _materializer(max_files=2)
    mat.checkpoint("r", "a", TABLE)
    mat.checkpoint("r", "b", TABLE)
    mat.checkpoint("r", "a", TABLE)  # overwriting does not need room
    with pytest.raises(LimitExceededError):
        mat.checkpoint("r", "c", TABLE)
    with pytest.raises(ValidationError):
        mat.checkpoint("r", "d", Table(("x",), ((object(),),)))
