"""Redaction: secrets and row contents never reach events, reports or alert payloads."""
# ruff: noqa: E501 - regular expressions stay on one line

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

REDACTED = "<redacted>"
MAX_TEXT = 500
_MAX_SCAN = 32_768  # patterns scan at most this many characters, whatever the limit
_SENSITIVE_KEY = re.compile(
    r"pass|secret|token|api[_-]?key|credential|authorization|auth|private[_-]?key|cookie|dsn", re.IGNORECASE
)
_ROW_KEYS = frozenset(
    {"row", "rows", "record", "records", "sample", "samples", "values", "data", "payload", "content", "body", "frame"}
)
_KEYWORD = (
    r"(?:(?-i:(?<![a-z])|(?<=[a-z])(?=[A-Z]))"
    r"(?:pass(?:word|wd|phrase|code)?s?|secrets?|token|credentials?|authorization|(?:api[ _-]?|private[_-]?)key)"
    r"|(?<=[a-z])(?<!com)(?<!by)(?<!over)(?<!sur)(?<!tres)(?<!under)(?<!im)pass"
    r"|(?:secret|token)(?:[_-]?key)?|secretkeys?|passwords?|passwds?|passphrases?|apikey)(?-i:(?![a-z]))"
)
_SEP = r"[ \t]*(?:=>|:=|==|=|:)[ \t]*"
_HEADER = re.compile(
    rf"(?P<qk>\\?[\"'`])(?=[\w .-]{{0,64}}?{_KEYWORD})[\w .-]{{1,64}}(?P=qk){_SEP}"
    rf"|(?<![\w.-])(?=[\w.-]*?{_KEYWORD})[\w.-]+[\"'`]?{_SEP}",
    re.IGNORECASE,
)
_BLOCK = re.compile(r"[|>](?:[1-9][+-]?|[+-][1-9]?)?[ \t]*(?:\r?\n|$)")
_PREFIX = re.compile(r"[bBrRuUfF]{1,2}(?=[\"'`])")
_UNQUOTED = re.compile(r"[^\s,;'\"&)]+")
_SEMICOLON_VALUE = re.compile(r"[^;\r\n]+")
_PLAIN_QUOTES = frozenset({'"', "'", "`"})
_CURLY_CLOSERS = {"\u201c": "\u201d", "\u2018": "\u2019"}
_URL_CREDENTIALS = re.compile(r"(://[^/\s:@]+:)(\S+)")
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdapi[0-9a-f]{12,}\b"), REDACTED),
    (re.compile(r"\b(?:eyJ[A-Za-z0-9_-]{8,}\.){1,2}[A-Za-z0-9_-]{8,}\b"), REDACTED),
    (re.compile(r"\bRow\((?:[^()]|\([^()]*\))*\)"), f"Row({REDACTED})"),
    (
        re.compile(r"(?i)\b(rows?|records?|values|sample|data)(\s*[=:]\s*)[\[({].*?[\])}](?=\s|$|[,;])"),
        rf"\1\2{REDACTED}",
    ),
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}")


def _quoted_value(text: str, start: int) -> tuple[str, int] | None:
    """Mask a quoted value starting at ``start``: return the replacement and the end index, or ``None``."""
    escaped = text[start : start + 1] == "\\" and text[start + 1 : start + 2] in {'"', "'"}
    opener = text[start : start + (2 if escaped else 1)]
    if not escaped and opener not in _PLAIN_QUOTES and opener not in _CURLY_CLOSERS:
        return None
    if not escaped and opener in _PLAIN_QUOTES and text.startswith(opener * 3, start):
        opener *= 3
    closer = _CURLY_CLOSERS.get(opener, opener)
    i = start + len(opener)
    if escaped:
        return _escaped_value(text, i, opener)
    while i < len(text):
        if text.startswith(closer, i):
            if len(closer) == 1 and text.startswith(closer, i + 1) and opener not in _CURLY_CLOSERS:
                i += 2  # doubled quote is an escape
                continue
            return f"{opener}{REDACTED}{closer}", i + len(closer)
        i += 2 if text[i] == "\\" else 1
    return f"{opener}{REDACTED}{closer}", len(text)


def _escaped_value(text: str, i: int, opener: str) -> tuple[str, int]:
    """Scan an escaped-quoted value: a lone backslash before the quote closes it; longer runs are escapes."""
    quote = opener[1]
    n = len(text)
    while i < n:
        if text[i] != "\\":
            i += 1
            continue
        j = i
        while j < n and text[j] == "\\":
            j += 1
        if j < n and text[j] == quote:
            if j - i == 1:
                return f"{opener}{REDACTED}{opener}", j + 1
            j += 1
        i = j
    return f"{opener}{REDACTED}{opener}", n


def _after_semicolon(text: str, index: int) -> bool:
    """Whether only spaces or tabs separate ``index`` from a preceding ``;``."""
    while index > 0 and text[index - 1] in " \t":
        index -= 1
    return index > 0 and text[index - 1] == ";"


def _block_scalar_end(text: str, start: int, key_start: int) -> int:
    """End of a YAML block scalar: the lines after ``start`` indented deeper than the key's line."""
    line_start = text.rfind("\n", 0, key_start) + 1
    head = text[line_start:key_start]
    indent = len(head) - len(head.lstrip())
    end = start
    pos = start
    while pos < len(text):
        newline = text.find("\n", pos)
        line = text[pos : len(text) if newline < 0 else newline]
        if line.strip():
            if len(line) - len(line.lstrip()) <= indent:
                break
            end = pos + len(line.rstrip())
        if newline < 0:
            break
        pos = newline + 1
    return end


def _mask_assignments(text: str) -> str:
    """Mask the value of every sensitive-looking ``key SEP value`` in one left-to-right sweep."""
    out: list[str] = []
    pos = 0
    for match in _HEADER.finditer(text):
        if match.start() < pos:
            continue
        i = match.end()
        replacement: tuple[str, int] | None
        block = _BLOCK.match(text, i)
        if block:
            replacement = (REDACTED, _block_scalar_end(text, block.end(), match.start()))
        elif quoted := _quoted_value(text, prefix.end() if (prefix := _PREFIX.match(text, i)) else i):
            replacement = quoted
        elif _after_semicolon(text, match.start()):
            bare = _SEMICOLON_VALUE.match(text, i)
            replacement = (REDACTED, bare.end()) if bare else None
        else:
            bare = _UNQUOTED.match(text, i)
            replacement = (REDACTED, bare.end()) if bare else None
        if replacement is None:
            continue
        out += (text[pos:i], replacement[0])
        pos = replacement[1]
    out.append(text[pos:])
    return "".join(out)


def _mask_url_credentials(match: re.Match[str]) -> str:
    """Keep the user and the host; mask the password, which may itself hold ``/`` or ``@``."""
    rest = match.group(2)
    first = rest.find("@")
    if first < 0:
        return match.group(0)
    slash = rest.find("/", first)
    last = rest.rfind("@", 0, len(rest) if slash < 0 else slash)
    return f"{match.group(1)}{REDACTED}{rest[last:]}"


def redact_text(text: str, secrets: Iterable[str] = (), limit: int = MAX_TEXT) -> str:
    """Mask secret-looking assignments, tokens, credentials in URLs, ``Row(...)`` and literal ``secrets``; bound the length."""
    out = text
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        out = out.replace(secret, REDACTED)
    window = max(limit, _MAX_SCAN)
    cut = len(out) > window
    out = out[:window]
    out = _URL_CREDENTIALS.sub(_mask_url_credentials, _mask_assignments(_BEARER.sub(rf"\1 {REDACTED}", out)))
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    if cut or len(out) > limit:
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
