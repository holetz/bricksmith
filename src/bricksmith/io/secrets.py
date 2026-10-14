"""Secret references resolved at the point of use; resolved values live only in a redactor."""

from __future__ import annotations

import base64
import binascii
import re
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bricksmith.io.errors import SecretError

if TYPE_CHECKING:
    from collections.abc import Callable

    from bricksmith.clients import Client

PREFIX = "secret:"
REDACTED = "<redacted>"
_SENSITIVE = re.compile(r"pass|secret|token|key|credential", re.IGNORECASE)
_PART = re.compile(r"[A-Za-z0-9_.\-]+")


@dataclass(frozen=True)
class SecretRef:
    """A pointer ``secret:<scope>/<key>``; it holds no secret value."""

    scope: str
    key: str

    def __post_init__(self) -> None:
        """Validate both parts."""
        if not _PART.fullmatch(self.scope) or not _PART.fullmatch(self.key):
            msg = "a secret reference must look like secret:<scope>/<key>"
            raise SecretError(msg)

    def __str__(self) -> str:
        """Return the reference text (never a value)."""
        return f"{PREFIX}{self.scope}/{self.key}"

    @classmethod
    def parse(cls, text: str) -> SecretRef:
        """Parse ``secret:<scope>/<key>``."""
        scope, _, key = text.removeprefix(PREFIX).partition("/")
        return cls(scope, key)


def is_ref(value: object) -> bool:
    """Whether ``value`` is a secret reference string."""
    return isinstance(value, str) and value.startswith(PREFIX)


type SecretResolver = Callable[[SecretRef], str]


def workspace_resolver(secrets_client: Client) -> SecretResolver:
    """Resolve through a Databricks secrets client (``Clients.secrets()``), decoding the API's base64 value."""

    def resolve(ref: SecretRef) -> str:
        try:
            response = secrets_client.get_secret(scope=ref.scope, key=ref.key)
            return base64.b64decode(response.value).decode()
        except (binascii.Error, UnicodeDecodeError, AttributeError):
            msg = f"secret {ref} has an unreadable value"
            raise SecretError(msg) from None
        except Exception as error:  # noqa: BLE001 - never echo what the client reports
            msg = f"secret {ref} could not be resolved ({type(error).__name__})"
            raise SecretError(msg) from None

    return resolve


class Redactor:
    """Collects resolved secret values for this process and scrubs them from any text."""

    def __init__(self) -> None:
        """Start empty."""
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        """Never show the collected values."""
        return f"Redactor(values={len(self._values)})"

    def add(self, value: str) -> None:
        """Remember a resolved value (and its common quoted forms) for scrubbing."""
        if value:
            with self._lock:
                self._values.update({value, repr(value)[1:-1]})

    def scrub(self, text: str) -> str:
        """Replace every remembered value in ``text``, longest first."""
        with self._lock:
            values = sorted(self._values, key=len, reverse=True)
        for value in values:
            text = text.replace(value, REDACTED)
        return text


def resolve_params(params: dict[str, object], resolver: SecretResolver | None, redactor: Redactor) -> dict[str, object]:
    """Return ``params`` with every reference value replaced by its secret; values go to ``redactor``."""
    resolved: dict[str, object] = {}
    for name, value in params.items():
        if is_ref(value):
            if resolver is None:
                msg = f"parameter {name!r} is a secret reference but no secret resolver is configured"
                raise SecretError(msg)
            secret = resolver(SecretRef.parse(str(value)))
            redactor.add(secret)
            resolved[name] = secret
        else:
            resolved[name] = value
    return resolved


def describe_params(params: dict[str, object]) -> dict[str, object]:
    """Return ``params`` for reports: references are shown as ``<secret-ref>``, never resolved."""
    return {
        name: "<secret-ref>" if is_ref(value) else REDACTED if _SENSITIVE.search(name) else value
        for name, value in sorted(params.items())
    }
