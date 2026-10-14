"""Volume artifact I/O: explicit resource names, validated relative paths and bounded transfers.

A volume is named by ``catalog.schema.volume``; an artifact by that volume plus a relative path.
Any number of volumes (and artifact prefixes) may live in one schema; nothing here derives a name
from a model or a task. Two interchangeable clients exist: the SDK Files API for local runs
(``FilesApiVolumeClient``) and the ``/Volumes`` filesystem inside Databricks compute
(``FilesystemVolumeClient``).
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from bricksmith.io.errors import LimitExceededError, PathError, PermanentIoError

if TYPE_CHECKING:
    from bricksmith.clients import Client, Clients

VOLUMES_ROOT = "/Volumes"
MAX_PATH_LENGTH = 1024
DEFAULT_MAX_BYTES = 16 * 1024 * 1024
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,254}")
_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-=@+ ]{0,254}")


class ArtifactNotFoundError(PermanentIoError):
    """The artifact or directory does not exist."""

    code = "io_not_found"


@dataclass(frozen=True)
class VolumeRef:
    """Explicit volume resource name ``catalog.schema.volume``."""

    catalog: str
    schema: str
    volume: str

    def __post_init__(self) -> None:
        """Validate every part."""
        for part in (self.catalog, self.schema, self.volume):
            if not _NAME.fullmatch(part):
                msg = f"{part!r} is not a valid catalog, schema or volume name"
                raise PathError(msg)

    @classmethod
    def parse(cls, text: str) -> VolumeRef:
        """Parse ``catalog.schema.volume``."""
        parts = text.split(".")
        if len(parts) != 3:  # noqa: PLR2004 - three-level name
            msg = "a volume resource name looks like catalog.schema.volume"
            raise PathError(msg)
        return cls(*parts)

    def __str__(self) -> str:
        """Return the three-level name."""
        return f"{self.catalog}.{self.schema}.{self.volume}"

    @property
    def root(self) -> str:
        """Return the volume's absolute path."""
        return f"{VOLUMES_ROOT}/{self.catalog}/{self.schema}/{self.volume}"


def validate_relative(path: str, *, allow_empty: bool = False) -> str:
    """Return a normalized relative path or raise ``PathError`` (absolute, ``..``, ``.``, empty segments)."""
    if not isinstance(path, str):  # pyright: ignore[reportUnnecessaryIsInstance]
        msg = "an artifact path must be a string"
        raise PathError(msg)
    if path == "":
        if allow_empty:
            return ""
        msg = "an artifact path cannot be empty"
        raise PathError(msg)
    if len(path) > MAX_PATH_LENGTH or path.startswith("/") or "\\" in path or any(ord(c) < 32 for c in path):  # noqa: PLR2004
        msg = "an artifact path must be relative, use '/' separators and contain no control characters"
        raise PathError(msg)
    for segment in path.rstrip("/").split("/"):
        if segment in {"", ".", ".."} or not _SEGMENT.fullmatch(segment) or segment.strip(".") == "":
            msg = f"artifact path segment {segment!r} is not allowed"
            raise PathError(msg)
    return path.rstrip("/")


@dataclass(frozen=True)
class Entry:
    """One listing row: name relative to the listed directory."""

    name: str
    is_dir: bool
    size: int | None = None


class VolumeClient(Protocol):
    """Byte operations on absolute ``/Volumes/...`` paths."""

    def write(self, path: str, data: bytes, *, overwrite: bool) -> None:
        """Store ``data`` at ``path``."""
        ...

    def read(self, path: str, max_bytes: int) -> bytes:
        """Return the content; fail with ``LimitExceededError`` over ``max_bytes``."""
        ...

    def list(self, directory: str) -> list[Entry]:
        """Return the direct children of ``directory``."""
        ...

    def delete(self, path: str) -> None:
        """Remove a file."""
        ...

    def exists(self, path: str) -> bool:
        """Whether a file exists."""
        ...


def _too_big(path: str, limit: int) -> LimitExceededError:
    return LimitExceededError(f"artifact {path} is larger than the limit of {limit} bytes")


def _not_found(error: BaseException) -> bool:
    return "NotFound" in {cls.__name__ for cls in type(error).__mro__} or isinstance(error, FileNotFoundError)


class FilesApiVolumeClient:
    """Local runs: the SDK Files API (``workspace.files``)."""

    def __init__(self, files: Client) -> None:
        """Wrap the SDK files client (``Clients.io()``)."""
        self._files = files

    def write(self, path: str, data: bytes, *, overwrite: bool) -> None:
        """Upload ``data``."""
        self._files.upload(path, io.BytesIO(data), overwrite=overwrite)

    def read(self, path: str, max_bytes: int) -> bytes:
        """Download at most ``max_bytes`` (one more byte is read to detect overflow)."""
        try:
            response = self._files.download(path)
        except Exception as error:
            if _not_found(error):
                msg = f"artifact {path} does not exist"
                raise ArtifactNotFoundError(msg) from None
            raise
        data: bytes = response.contents.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise _too_big(path, max_bytes)
        return data

    def list(self, directory: str) -> list[Entry]:
        """List the direct children."""
        try:
            return [
                Entry(str(item.name), bool(item.is_directory), item.file_size)
                for item in self._files.list_directory_contents(directory)
            ]
        except Exception as error:
            if _not_found(error):
                return []
            raise

    def delete(self, path: str) -> None:
        """Delete the file."""
        self._files.delete(path)

    def exists(self, path: str) -> bool:
        """Whether the file's metadata can be read."""
        try:
            self._files.get_metadata(path)
        except Exception as error:
            if _not_found(error):
                return False
            raise
        return True


class FilesystemVolumeClient:
    """Deployed runs: the ``/Volumes`` FUSE filesystem; ``root`` is injectable for tests."""

    def __init__(self, root: Path | str = VOLUMES_ROOT) -> None:
        """Map ``/Volumes/...`` paths below ``root``."""
        self._root = Path(root)

    def _local(self, path: str) -> Path:
        relative = path.removeprefix(VOLUMES_ROOT).lstrip("/")
        resolved = (self._root / relative).resolve()
        if not resolved.is_relative_to(self._root.resolve()):
            msg = f"{path} escapes the volumes root"
            raise PathError(msg)
        return resolved

    def write(self, path: str, data: bytes, *, overwrite: bool) -> None:
        """Write the file, creating parent directories."""
        target = self._local(path)
        if target.exists() and not overwrite:
            msg = f"artifact {path} already exists"
            raise PermanentIoError(msg)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def read(self, path: str, max_bytes: int) -> bytes:
        """Read the file, refusing one over ``max_bytes``."""
        target = self._local(path)
        if not target.is_file():
            msg = f"artifact {path} does not exist"
            raise ArtifactNotFoundError(msg)
        if target.stat().st_size > max_bytes:
            raise _too_big(path, max_bytes)
        return target.read_bytes()

    def list(self, directory: str) -> list[Entry]:
        """List the direct children."""
        target = self._local(directory)
        if not target.is_dir():
            return []
        return [
            Entry(item.name, item.is_dir(), None if item.is_dir() else item.stat().st_size)
            for item in sorted(target.iterdir())
        ]

    def delete(self, path: str) -> None:
        """Delete the file."""
        target = self._local(path)
        if not target.is_file():
            msg = f"artifact {path} does not exist"
            raise ArtifactNotFoundError(msg)
        target.unlink()

    def exists(self, path: str) -> bool:
        """Whether the file exists."""
        return self._local(path).is_file()


def files_api_client(clients: Clients) -> FilesApiVolumeClient:
    """Local-run client from ``Clients`` (the SDK is created lazily through its provider)."""
    return FilesApiVolumeClient(clients.io())


class ArtifactStore:
    """Bounded artifact I/O inside one named volume."""

    def __init__(self, volume: VolumeRef | str, client: VolumeClient, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        """Bind a volume resource name, a client and the per-artifact byte limit."""
        self.volume = VolumeRef.parse(volume) if isinstance(volume, str) else volume
        self.client = client
        self.max_bytes = max_bytes

    def path(self, relative: str) -> str:
        """Return the absolute path of ``relative`` after validating it."""
        return f"{self.volume.root}/{validate_relative(relative)}"

    def write(self, relative: str, data: bytes | str, *, overwrite: bool = True) -> str:
        """Store ``data`` (text is UTF-8) and return the absolute path; larger than ``max_bytes`` fails."""
        raw = data.encode() if isinstance(data, str) else data
        path = self.path(relative)
        if len(raw) > self.max_bytes:
            raise _too_big(path, self.max_bytes)
        self.client.write(path, raw, overwrite=overwrite)
        return path

    def read(self, relative: str) -> bytes:
        """Return the artifact bytes."""
        return self.client.read(self.path(relative), self.max_bytes)

    def read_text(self, relative: str) -> str:
        """Return the artifact as UTF-8 text."""
        return self.read(relative).decode()

    def write_json(self, relative: str, value: object) -> str:
        """Store ``value`` as sorted JSON."""
        return self.write(relative, json.dumps(value, sort_keys=True))

    def read_json(self, relative: str) -> object:
        """Load a JSON artifact."""
        return json.loads(self.read_text(relative))

    def exists(self, relative: str) -> bool:
        """Whether the artifact exists."""
        return self.client.exists(self.path(relative))

    def list(self, prefix: str = "") -> list[Entry]:
        """List the direct children of the directory ``prefix`` (the volume root by default)."""
        clean = validate_relative(prefix, allow_empty=True)
        directory = f"{self.volume.root}/{clean}" if clean else self.volume.root
        return sorted(self.client.list(directory), key=lambda entry: entry.name)

    def delete(self, relative: str) -> None:
        """Remove one artifact."""
        self.client.delete(self.path(relative))
