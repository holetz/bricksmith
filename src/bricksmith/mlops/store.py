"""The model store: explicit identities, Unity Catalog names and explicit version or alias resolution.

``ModelStore`` wraps the tracking client from ``Clients.tracking()`` (an ``MlflowClient`` whose registry is
``databricks-uc``). Tests pass an in-memory fake with the same methods. Prediction never resolves ``latest``.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.errors import ModelAmbiguousError, ModelError, ModelIncompatibleError, ModelNotFoundError
from bricksmith.mlops.meta import FILE, ModelMetadata

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

TAG_MODEL = "bricksmith_model"
TAG_STATE = "bricksmith_state"
TAG_EVENTS = "bricksmith_events"
TAG_HISTORY = "bricksmith_history_"
ACTIVE, RETIRED = "active", "retired"
MAX_EVENTS = 8
_ALIAS = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
_IMPLICIT = ("latest", "newest", "current", "default")


def registered_name(context: ExecutionContext, scope: str, model: str) -> str:
    """Return ``<catalog>.<schema>.<model>``: the logical ``catalog.schema`` scope through the context mapping."""
    catalog, _, schema = scope.partition(".")
    if not catalog or not schema or "." in schema:
        msg = f"scope must be 'catalog.schema', got {scope!r}"
        raise ModelError(msg)
    return f"{context.physical_catalog(catalog)}.{context.physical_schema(schema)}.{model}"


def check_selector(alias: str | None, version: int | str | None) -> tuple[str | None, int | None]:
    """Validate that exactly one explicit selector is given; ``latest``-style names and aliases are refused."""
    if alias is None and version is None:
        msg = "name an explicit version or alias; the latest version is never chosen implicitly"
        raise ModelAmbiguousError(msg)
    if alias is not None and version is not None:
        msg = "give either a version or an alias, not both"
        raise ModelAmbiguousError(msg)
    if alias is not None:
        if alias.lower() in _IMPLICIT or not _ALIAS.match(alias) or re.fullmatch(r"v\d+", alias, re.IGNORECASE):
            msg = f"alias {alias!r} is not an explicit, valid alias (implicit names such as latest are refused)"
            raise ModelAmbiguousError(msg)
        return alias, None
    text = str(version)
    if not text.isdigit() or int(text) < 1:
        msg = f"version must be a positive integer, got {version!r}"
        raise ModelAmbiguousError(msg)
    return None, int(text)


def _is_missing(error: Exception) -> bool:
    text = f"{getattr(error, 'error_code', '')} {error}".upper()
    return isinstance(error, LookupError) or any(
        m in text for m in ("RESOURCE_DOES_NOT_EXIST", "NOT_FOUND", "DOES NOT EXIST")
    )


@dataclass(frozen=True)
class ResolvedModel:
    """One concrete registered version, resolved from an explicit selector."""

    name: str
    model: str
    version: int
    run_id: str
    aliases: tuple[str, ...]
    state: str

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return {
            "aliases": list(self.aliases),
            "model": self.model,
            "name": self.name,
            "run_id": self.run_id,
            "state": self.state,
            "version": self.version,
        }


def _default_download(uris: tuple[str, str] | None) -> Callable[[str, int, Path], Path]:
    def download(name: str, version: int, dest: Path) -> Path:
        import mlflow.artifacts  # noqa: PLC0415

        tracking, registry = uris or (None, None)
        got = mlflow.artifacts.download_artifacts(
            artifact_uri=f"models:/{name}/{version}", dst_path=str(dest), tracking_uri=tracking, registry_uri=registry
        )
        return Path(got)

    return download


class ModelStore:
    """Registry and run operations over a tracking client (real ``MlflowClient`` or a fake)."""

    def __init__(
        self,
        client: Any,  # noqa: ANN401 - MlflowClient or an in-memory fake with the same methods
        *,
        uris: tuple[str, str] | None = None,
        downloader: Callable[[str, int, Path], Path] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 300.0,
    ) -> None:
        """Bind the client; ``uris`` are the (tracking, registry) URIs used to download artifacts."""
        self.client = client
        self.uris = uris
        self._download = downloader or _default_download(uris)
        self._sleep = sleep
        self._timeout = timeout

    @classmethod
    def from_clients(cls, clients: Clients) -> ModelStore:
        """Build the store for ``clients.context``; the client comes from ``Clients.tracking()``."""
        from bricksmith.clients import tracking_uris  # noqa: PLC0415

        return cls(clients.tracking(), uris=tracking_uris(clients.context))

    # -- runs -----------------------------------------------------------------------------------------------
    def experiment_id(self, experiment: str) -> str:
        """Return the id of the named experiment, creating it when absent."""
        found = self.client.get_experiment_by_name(experiment)
        if found is not None and getattr(found, "lifecycle_stage", "active") == "active":
            return str(found.experiment_id)
        return str(self.client.create_experiment(experiment))

    def log_run(  # noqa: PLR0913 - one keyword per recorded kind of evidence
        self,
        experiment: str,
        *,
        run_name: str,
        tags: Mapping[str, str],
        params: Mapping[str, Any],
        metrics: Mapping[str, float | None],
        artifact_dir: Path,
        artifact_path: str = "model",
    ) -> str:
        """Create a finished run holding the params, metrics, tags and the artifact directory; return its id."""
        run = self.client.create_run(self.experiment_id(experiment), run_name=run_name, tags=dict(tags))
        run_id = str(run.info.run_id)
        try:
            for key, value in sorted(params.items()):
                self.client.log_param(run_id, key, str(value)[:6000])
            for key, number in sorted(metrics.items()):
                if number is not None:
                    self.client.log_metric(run_id, key, float(number))
            self.client.log_artifacts(run_id, str(artifact_dir), artifact_path)
            self._verify_upload(run_id, artifact_dir, artifact_path)
        except BaseException:
            self.client.set_terminated(run_id, "FAILED")
            raise
        self.client.set_terminated(run_id, "FINISHED")
        return run_id

    def _verify_upload(self, run_id: str, artifact_dir: Path, artifact_path: str) -> None:
        """Require every local payload file in the persisted run inventory before claiming success."""
        expected = {
            f"{artifact_path.rstrip('/')}/{file.relative_to(artifact_dir).as_posix()}".lstrip("/")
            for file in artifact_dir.rglob("*")
            if file.is_file()
        }
        if not expected:
            msg = "model artifact payload contains no files"
            raise ModelError(msg)
        pending = [artifact_path]
        visited: set[str] = set()
        persisted: set[str] = set()
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            try:
                entries = self.client.list_artifacts(run_id, path)
            except Exception as error:
                msg = f"unable to verify persisted model artifacts ({type(error).__name__})"
                raise ModelError(msg) from error
            for entry in entries:
                if entry.is_dir:
                    pending.append(str(entry.path))
                else:
                    persisted.add(str(entry.path))
        missing = expected - persisted
        if missing:
            msg = f"model artifact upload returned without persisting {len(missing)} required files"
            raise ModelError(msg)

    # -- registry -------------------------------------------------------------------------------------------
    def registered(self, name: str) -> Any | None:  # noqa: ANN401
        """Return the registered model or None when it does not exist."""
        try:
            return self.client.get_registered_model(name)
        except Exception as error:
            if _is_missing(error):
                return None
            raise

    def ensure_model(self, name: str, model: str) -> None:
        """Create the registered model for identity ``model`` or verify that it already belongs to it."""
        existing = self.registered(name)
        if existing is None:
            self.client.create_registered_model(name, tags={TAG_MODEL: model})
            return
        owner = dict(existing.tags or {}).get(TAG_MODEL)
        if owner != model:
            msg = f"registered model {name} belongs to {owner or 'no bricksmith model'}, not {model!r}"
            raise ModelIncompatibleError(msg)

    def create_version(self, name: str, model: str, run_id: str, tags: Mapping[str, str] | None = None) -> int:
        """Register ``runs:/<run>/model`` as a new version of ``name`` and wait until it is ready."""
        self.ensure_model(name, model)
        merged = {TAG_MODEL: model, TAG_STATE: ACTIVE, **(tags or {})}
        created = self.client.create_model_version(name, f"runs:/{run_id}/model", run_id=run_id, tags=merged)
        version = int(created.version)
        waited = 0.0
        while str(getattr(self.client.get_model_version(name, str(version)), "status", "READY")) not in {
            "READY",
            "ModelVersionStatus.READY",
        }:
            if waited >= self._timeout:
                msg = f"model version {name} v{version} did not become ready"
                raise ModelError(msg)
            self._sleep(2.0)
            waited += 2.0
        return version

    def aliases(self, name: str) -> dict[str, int]:
        """Return ``{alias: version}`` for the registered model."""
        found = self.registered(name)
        if found is None:
            return {}
        return {a: int(v) for a, v in sorted(dict(found.aliases or {}).items())}

    def versions(self, name: str) -> list[ResolvedModel]:
        """Return every version of ``name`` in ascending order."""
        mapping = self.aliases(name)
        out = []
        for item in self.client.search_model_versions(f"name = '{name}'"):
            version = int(item.version)
            tags = dict(item.tags or {})
            out.append(
                ResolvedModel(
                    name,
                    tags.get(TAG_MODEL, ""),
                    version,
                    str(item.run_id),
                    tuple(sorted(a for a, v in mapping.items() if v == version)),
                    tags.get(TAG_STATE, ACTIVE),
                )
            )
        return sorted(out, key=lambda r: r.version)

    def resolve(
        self, name: str, model: str, *, alias: str | None = None, version: int | str | None = None
    ) -> ResolvedModel:
        """Resolve one explicit selector to a version of identity ``model``; never picks ``latest``."""
        chosen_alias, chosen_version = check_selector(alias, version)
        if self.registered(name) is None:
            msg = f"registered model {name} does not exist"
            raise ModelNotFoundError(msg)
        if chosen_alias is not None:
            chosen_version = self.aliases(name).get(chosen_alias)
            if chosen_version is None:
                msg = f"alias {chosen_alias!r} is not set on {name}"
                raise ModelNotFoundError(msg)
        found = {r.version: r for r in self.versions(name)}.get(int(chosen_version or 0))
        if found is None:
            msg = f"version {chosen_version} does not exist on {name}"
            raise ModelNotFoundError(msg)
        if found.model != model:
            msg = f"{name} v{found.version} belongs to {found.model or 'no bricksmith model'}, not {model!r}"
            raise ModelIncompatibleError(msg)
        if found.state == RETIRED:
            msg = f"{name} v{found.version} is retired"
            raise ModelNotFoundError(msg)
        return found

    def set_alias(self, name: str, alias: str, version: int) -> None:
        """Point ``alias`` at ``version``."""
        self.client.set_registered_model_alias(name, alias, str(version))

    def remove_alias(self, name: str, alias: str) -> None:
        """Remove ``alias`` from the registered model."""
        self.client.delete_registered_model_alias(name, alias)

    def tag_version(self, name: str, version: int, key: str, value: str) -> None:
        """Set one tag on a version."""
        self.client.set_model_version_tag(name, str(version), key, value)

    def tags(self, name: str, version: int) -> dict[str, str]:
        """Return the tags of a version."""
        return dict(self.client.get_model_version(name, str(version)).tags or {})

    def events(self, name: str, version: int) -> list[dict[str, Any]]:
        """Return the recorded review events of a version (oldest first)."""
        return list(json.loads(self.tags(name, version).get(TAG_EVENTS, "[]")))

    def record_event(self, name: str, version: int, event: Mapping[str, Any]) -> None:
        """Append a review event to a version, keeping the newest ``MAX_EVENTS``."""
        kept = [*self.events(name, version), dict(event)][-MAX_EVENTS:]
        self.tag_version(name, version, TAG_EVENTS, json.dumps(kept, sort_keys=True, separators=(",", ":")))

    def history(self, name: str, alias: str) -> list[dict[str, Any]]:
        """Return the alias history (oldest first) from the registered model tag."""
        found = self.registered(name)
        raw = dict((found.tags if found else None) or {}).get(TAG_HISTORY + alias, "[]")
        return list(json.loads(raw))

    def record_history(self, name: str, alias: str, entry: Mapping[str, Any]) -> None:
        """Append an alias change to the history tag, keeping the newest ``MAX_EVENTS``."""
        kept = [*self.history(name, alias), dict(entry)][-MAX_EVENTS:]
        self.client.set_registered_model_tag(
            name, TAG_HISTORY + alias, json.dumps(kept, sort_keys=True, separators=(",", ":"))
        )

    # -- artifacts ------------------------------------------------------------------------------------------
    def fetch(self, resolved: ResolvedModel, dest: Path) -> tuple[Path, ModelMetadata]:
        """Download the pinned version into ``dest`` and return its directory and metadata (identity checked)."""
        path = self._download(resolved.name, resolved.version, dest)
        meta_file = path / FILE
        if not meta_file.is_file():
            msg = f"{resolved.name} v{resolved.version} has no {FILE}; it was not logged by bricksmith"
            raise ModelIncompatibleError(msg)
        meta = ModelMetadata.from_json(meta_file.read_text(encoding="utf-8"))
        if meta.model != resolved.model:
            msg = (
                f"artifact of {resolved.name} v{resolved.version} declares model {meta.model!r}, not {resolved.model!r}"
            )
            raise ModelIncompatibleError(msg)
        return path, meta

    # -- cleanup --------------------------------------------------------------------------------------------
    def delete_model(self, name: str) -> None:
        """Delete every version and then the registered model (test teardown and explicit purge only)."""
        if self.registered(name) is None:
            return
        for item in self.versions(name):
            self.client.delete_model_version(name, str(item.version))
        self.client.delete_registered_model(name)

    def delete_experiment(self, experiment: str) -> None:
        """Delete the named experiment when it exists."""
        found = self.client.get_experiment_by_name(experiment)
        if found is not None:
            self.client.delete_experiment(found.experiment_id)
