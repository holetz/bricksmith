"""External inputs, SQL connectors and volume artifacts (see ``docs/guide/external-io.md``).

Importing this package loads only the standard library; drivers and the Databricks SDK are
imported by the caller or lazily through ``bricksmith.clients``.
"""

from bricksmith.io.contracts import Batch, IoRetry, Limits, Sink, Source, Table, classify, is_transient
from bricksmith.io.errors import (
    IoError,
    LimitExceededError,
    PathError,
    PermanentIoError,
    SecretError,
    TransientIoError,
)
from bricksmith.io.materialize import MaterializationPolicy, Materializer
from bricksmith.io.resources import ExternalResources, IoLink, io_lineage
from bricksmith.io.secrets import Redactor, SecretRef, workspace_resolver
from bricksmith.io.sql import SqlConnector, SqlSink, SqlSource
from bricksmith.io.transfer import (
    BookmarkState,
    BookmarkStore,
    FileBookmarkStore,
    MemoryBookmarkStore,
    TransferFailedError,
    TransferReport,
    transfer,
)
from bricksmith.io.volumes import (
    ArtifactNotFoundError,
    ArtifactStore,
    FilesApiVolumeClient,
    FilesystemVolumeClient,
    VolumeClient,
    VolumeRef,
    files_api_client,
    validate_relative,
)

__all__ = [
    "ArtifactNotFoundError",
    "ArtifactStore",
    "Batch",
    "BookmarkState",
    "BookmarkStore",
    "ExternalResources",
    "FileBookmarkStore",
    "FilesApiVolumeClient",
    "FilesystemVolumeClient",
    "IoError",
    "IoLink",
    "IoRetry",
    "LimitExceededError",
    "Limits",
    "MaterializationPolicy",
    "Materializer",
    "MemoryBookmarkStore",
    "PathError",
    "PermanentIoError",
    "Redactor",
    "SecretError",
    "SecretRef",
    "Sink",
    "Source",
    "SqlConnector",
    "SqlSink",
    "SqlSource",
    "Table",
    "TransferFailedError",
    "TransferReport",
    "TransientIoError",
    "VolumeClient",
    "VolumeRef",
    "classify",
    "files_api_client",
    "io_lineage",
    "is_transient",
    "transfer",
    "validate_relative",
    "workspace_resolver",
]
