"""Diagnostics reported by discovery and graph validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

ERROR = "error"
WARNING = "warning"
INFO = "info"
EXEMPT = "exempt"
SEVERITIES = (ERROR, WARNING, INFO, EXEMPT)


@dataclass(frozen=True, order=True)
class Diagnostic:
    """One finding: a stable ``code``, a severity, the subject it concerns and where it was found."""

    code: str
    severity: str
    subject: str
    message: str
    path: str = ""
    line: int = 0

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "code": self.code,
            "line": self.line,
            "message": self.message,
            "path": self.path,
            "severity": self.severity,
            "subject": self.subject,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Diagnostic:
        """Rebuild a diagnostic from :meth:`to_dict` output."""
        return cls(
            code=str(data["code"]),
            severity=str(data["severity"]),
            subject=str(data["subject"]),
            message=str(data["message"]),
            path=str(data.get("path", "")),
            line=int(data.get("line", 0)),
        )

    def text(self) -> str:
        """Return the one-line human form."""
        where = f" ({self.path}:{self.line})" if self.path else ""
        return f"{self.severity}[{self.code}] {self.subject}: {self.message}{where}"
