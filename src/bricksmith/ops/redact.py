"""Redaction: secrets and row contents never reach events, reports or alert payloads."""
# ruff: noqa: E501 - regular expressions stay on one line

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

REDACTED = "<redacted>"
MAX_TEXT = 500
_SENSITIVE_KEY = re.compile(
    r"pass|secret|token|api[_-]?key|credential|authorization|auth|private[_-]?key|cookie|dsn", re.IGNORECASE
)
_ROW_KEYS = frozenset(
    {"row", "rows", "record", "records", "sample", "samples", "values", "data", "payload", "content", "body", "frame"}
)
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}"), rf"\1 {REDACTED}"),
    (
        re.compile(
            r"(?i)([\w.-]*(?:pass|secret|token|api[_-]?key|credential|authorization|private[_-]?key)[\w.-]*)(['\"]?\s*[=:]\s*)(['\"]?)[^\s,;'\"&)]+"
        ),
        rf"\1\2\3{REDACTED}",
    ),
    (re.compile(r"(?i)(://[^/\s:@]+:)[^@\s/]+@"), rf"\1{REDACTED}@"),
    (re.compile(r"\bdapi[0-9a-f]{12,}\b"), REDACTED),
    (re.compile(r"\b(?:eyJ[A-Za-z0-9_-]{8,}\.){1,2}[A-Za-z0-9_-]{8,}\b"), REDACTED),
    (re.compile(r"\bRow\((?:[^()]|\([^()]*\))*\)"), f"Row({REDACTED})"),
    (
        re.compile(r"(?i)\b(rows?|records?|values|sample|data)(\s*[=:]\s*)[\[({].*?[\])}](?=\s|$|[,;])"),
        rf"\1\2{REDACTED}",
    ),
)


def redact_text(text: str, secrets: Iterable[str] = (), limit: int = MAX_TEXT) -> str:
    """Mask secret-looking assignments, tokens, credentials in URLs, ``Row(...)`` and literal ``secrets``; bound the length."""
    out = text
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        out = out.replace(secret, REDACTED)
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    if len(out) > limit:
        out = out[: max(limit - 1, 0)] + "…"
    return out


def redact_value(value: Any, secrets: Iterable[str] = (), *, key: str = "", depth: int = 0) -> Any:  # noqa: ANN401, PLR0911
    """Redact a JSON-like value recursively: sensitive keys and row-content keys are masked, text is scrubbed."""
    if key and (_SENSITIVE_KEY.search(key) or key.lower() in _ROW_KEYS):
        return REDACTED
    if depth > 6:  # noqa: PLR2004 - bounds recursion on pathological inputs
        return REDACTED
    keep = tuple(secrets)
    if isinstance(value, dict):
        return {str(k): redact_value(v, keep, key=str(k), depth=depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_value(v, keep, depth=depth + 1) for v in list(value)[:50]]
    if isinstance(value, str):
        return redact_text(value, keep)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(repr(value), keep)
