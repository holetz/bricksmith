"""Injectable, lazy client providers; heavy integrations are imported only when a client is built."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from bricksmith.env import require_profile_defined
from bricksmith.errors import ConfigError, MissingExtraError

if TYPE_CHECKING:
    from types import ModuleType

    from bricksmith.context import ExecutionContext

# Concrete client types live in optional packages that are not imported here.
type Client = Any
type Provider = Callable[[ExecutionContext], Client]
KINDS = ("spark", "workspace", "tracking", "secrets", "io")


def import_extra(module: str, extra: str) -> ModuleType:
    """Import ``module`` or raise an error naming the extra that provides it."""
    try:
        return importlib.import_module(module)
    except ImportError:
        msg = f"module {module!r} is not installed; install it with: pip install 'bricksmith[{extra}]'"
        raise MissingExtraError(msg) from None


def _check_profile(context: ExecutionContext) -> None:
    if context.profile is not None:
        require_profile_defined(context.profile, os.environ)


def _sdk_config(context: ExecutionContext) -> Client:
    _check_profile(context)
    core = import_extra("databricks.sdk.core", "sdk")
    if context.profile is not None:
        return core.Config(profile=context.profile)
    if context.host is None:
        msg = "no profile or workspace host is configured for this context"
        raise ConfigError(msg)
    return core.Config(host=context.host)


def _spark(context: ExecutionContext) -> Client:
    config = _sdk_config(context)
    connect = import_extra("databricks.connect", "spark")
    # Connect rejects an SDK config combined with builder compute parameters, so compute rides on the config.
    if context.compute == "serverless":
        config.serverless_compute_id = "auto"
    else:
        config.cluster_id = context.compute.removeprefix("cluster:")
    builder = connect.DatabricksSession.builder.sdkConfig(config)
    return builder.getOrCreate()


def _workspace(context: ExecutionContext) -> Client:
    sdk = import_extra("databricks.sdk", "sdk")
    return sdk.WorkspaceClient(config=_sdk_config(context))


def tracking_uris(context: ExecutionContext) -> tuple[str, str]:
    """Return the (tracking, Unity Catalog registry) URIs of ``context``; nothing is read from process globals."""
    suffix = f"://{context.profile}" if context.profile else ""
    return f"databricks{suffix}", f"databricks-uc{suffix}"


def _tracking(context: ExecutionContext) -> Client:
    _check_profile(context)
    mlflow = import_extra("mlflow", "mlops")
    tracking, registry = tracking_uris(context)
    return mlflow.MlflowClient(tracking_uri=tracking, registry_uri=registry)


class Clients:
    """Per-instance client cache for one context; supply ``providers`` to inject fakes."""

    def __init__(self, context: ExecutionContext, providers: Mapping[str, Provider] | None = None) -> None:
        """Bind ``context`` and optional provider overrides keyed by client kind."""
        unknown = set(providers or {}) - set(KINDS)
        if unknown:
            msg = f"unknown client kinds: {', '.join(sorted(unknown))}"
            raise ConfigError(msg)
        self.context = context
        self._providers: dict[str, Provider] = {
            "spark": _spark,
            "workspace": _workspace,
            "tracking": _tracking,
            "secrets": lambda _: self.workspace().secrets,
            "io": lambda _: self.workspace().files,
            **(providers or {}),
        }
        self._cache: dict[str, Client] = {}

    def get(self, kind: str) -> Client:
        """Return the client of ``kind``, creating it on first use."""
        if kind not in self._cache:
            self._cache[kind] = self._providers[kind](self.context)
        return self._cache[kind]

    def spark(self) -> Client:
        """Return the Spark session."""
        return self.get("spark")

    def workspace(self) -> Client:
        """Return the workspace client."""
        return self.get("workspace")

    def tracking(self) -> Client:
        """Return the model-tracking client."""
        return self.get("tracking")

    def secrets(self) -> Client:
        """Return the secrets client."""
        return self.get("secrets")

    def io(self) -> Client:
        """Return the file I/O client."""
        return self.get("io")
