"""Generic notification adapters: logger, JSON-lines file and a plain HTTP webhook (standard library only)."""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class LogAdapter:
    """Write the payload to a stdlib logger."""

    name = "log"

    def __init__(self, logger: logging.Logger | None = None, level: int = logging.WARNING) -> None:
        """Use ``logger`` (default ``bricksmith.ops.alerts``)."""
        self._logger = logger or logging.getLogger("bricksmith.ops.alerts")
        self._level = level

    def deliver(self, payload: Mapping[str, Any]) -> None:
        """Log one JSON line."""
        self._logger.log(self._level, "%s", json.dumps(payload, sort_keys=True))


class FileAdapter:
    """Append the payload as one JSON line to a file."""

    name = "file"

    def __init__(self, path: Path | str) -> None:
        """Keep the path; parent directories are created on delivery."""
        self._path = Path(path)
        self._lock = threading.Lock()

    def deliver(self, payload: Mapping[str, Any]) -> None:
        """Append one line."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True) + "\n")


class WebhookAdapter:
    """POST the payload as JSON to a URL. Errors never include the URL (it may carry a secret)."""

    name = "webhook"

    def __init__(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
        allow_insecure: bool = False,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        """Validate the URL scheme (``https``; ``http`` only with ``allow_insecure``)."""
        scheme = urlparse(url).scheme
        if scheme != "https" and not (allow_insecure and scheme == "http"):
            msg = "webhook url must use https"
            raise ValueError(msg)
        self._url = url
        self._headers = {"Content-Type": "application/json", **(headers or {})}
        self._timeout = timeout
        self._open = opener

    def deliver(self, payload: Mapping[str, Any]) -> None:
        """Send the request; a non-2xx status or network error raises a message without the URL."""
        request = urllib.request.Request(  # noqa: S310 - scheme validated in the constructor
            self._url, json.dumps(payload, sort_keys=True).encode(), self._headers, method="POST"
        )
        try:
            with self._open(request, timeout=self._timeout) as response:
                status = getattr(response, "status", 200)
        except urllib.error.HTTPError as error:
            msg = f"webhook answered HTTP {error.code}"
            raise RuntimeError(msg) from None
        except (urllib.error.URLError, OSError) as error:
            msg = f"webhook unreachable ({type(error).__name__})"
            raise RuntimeError(msg) from None
        if not 200 <= status < 300:  # noqa: PLR2004 - HTTP success range
            msg = f"webhook answered HTTP {status}"
            raise RuntimeError(msg)
