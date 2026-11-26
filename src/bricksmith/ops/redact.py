"""Redaction: secrets and row contents never reach events, reports or alert payloads."""
# ruff: noqa: E501 - regular expressions stay on one line

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

REDACTED = "<redacted>"
MAX_TEXT = 500
_MIN_ATS_FOR_PATH_ADDRESS = 2  # a path address needs an earlier "@" to separate
_MAX_SCAN = 32_768  # patterns scan at most this many characters, whatever the limit
_SENSITIVE_KEY = re.compile(
    r"pass|secret|token|api[_-]?key|credential|authorization|auth|private[_-]?key|cookie|dsn", re.IGNORECASE
)
_ROW_KEYS = frozenset(
    {"row", "rows", "record", "records", "sample", "samples", "values", "data", "payload", "content", "body", "frame"}
)
# Known non-secret words that are exactly ``<word>pass`` (``lowpass``, ``bypass``, ``compass``...). Any other
# concatenated ``<qualifier>pass`` (``storepass``, ``keypass``, ``firewallpass``, ``callpass``) is a credential key:
# the exemption needs the WHOLE letter run before ``pass`` to be one of these words, so a qualifier that merely ends
# in one fails closed. Fixed-width lookbehinds anchored at a non-letter or the text start keep the match linear.
_NON_SECRET_PASS_PREFIXES = (
    "low",
    "high",
    "band",
    "all",
    "multi",
    "com",
    "by",
    "over",
    "sur",
    "tres",
    "under",
    "im",
)
_OPEN_PASS = "(?<=[a-z])" + "".join(f"(?<!^{w})(?<![^a-z]{w})" for w in _NON_SECRET_PASS_PREFIXES) + "pass"
_KEYWORD = (
    r"(?:(?-i:(?<![a-z])|(?<=[a-z])(?=[A-Z]))"
    r"(?:pass(?:word|wd|phrase|code)?s?|secrets?|token|credentials?|authorization|(?:api[ _-]?|private[_-]?)key)"
    rf"|{_OPEN_PASS}"
    r"|(?:secrets?|token)(?:[_-]?key)?|secretkeys?|passwords?|passwds?|passphrases?|apikeys?)(?-i:(?![a-z]))"
)
_SEP = r"[ \t]*(?:=>|:=|==|=|:)[ \t]*"
_HEADER = re.compile(
    rf"(?P<qk>\\?[\"'`])(?=[\w .-]{{0,64}}?{_KEYWORD})[\w .-]{{1,64}}(?P=qk){_SEP}"
    rf"|(?<![\w.-])(?=[\w.-]*?{_KEYWORD})[\w.-]+[\"'`]?{_SEP}",
    re.IGNORECASE,
)
_BLOCK = re.compile(r"[|>](?:[1-9][+-]?|[+-][1-9]?)?[ \t]*(?:#[^\r\n]*)?(?:\r?\n|$)")
_PREFIX = re.compile(r"[bBrRuUfF]{1,2}(?=[\"'`])")
_UNQUOTED = re.compile(r"[^\s,;'\"&)]+")
_SEMICOLON_VALUE = re.compile(r"[^;\r\n]+")
_PLAIN_QUOTES = frozenset({'"', "'", "`"})
_CURLY_CLOSERS = {"\u201c": "\u201d", "\u2018": "\u2019"}
_URL_USER = re.compile(r"://[^/\s:@]+:")
_NON_SPACE = re.compile(r"\S+")
_ADDRESS_TAIL = re.compile(r"[\w-]+(?:\.[\w-]+)+(?![\w@/:-]|\.\w)")
_PORT = re.compile(r"\d{1,5}(?:[/?#]|$)")
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


def _first_line_end(text: str, start: int) -> int:
    """Index of the first carriage return or line feed at or after ``start``, else the end of the text."""
    ends = (found for found in (text.find("\r", start), text.find("\n", start)) if found >= 0)
    return min(ends, default=len(text))


def _closes(text: str, i: int, quote: str) -> bool:
    """Whether the character at ``i`` closes a ``quote`` region: an apostrophe directly before a letter is prose (``it's``)."""
    return text[i] == quote and (quote != "'" or not text[i + 1 : i + 2].isalnum())


def _quote_step(text: str, i: int, quote: str) -> tuple[int, str]:
    """Advance one character through a quoted region: return the index of the character handled and the quote state."""
    ch = text[i]
    if not quote:
        return i, ch
    if ch == "\\":
        return i + 1, quote
    return i, "" if _closes(text, i, quote) else quote


class _BracketScan:
    """Resolve the ``]`` matching each ``[`` with one forward pass, sharing what a pass learns.

    A pass from a start resolves every bracket it opens outside quotes (a stack), caching each result; a cached
    unclosed bracket ends the pass, a cached match is jumped. A start first seen inside a quoted region of an
    earlier pass needs its own pass: only those rescans of already-scanned text draw from ``budget``, so early
    unclosed brackets can never exhaust it.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.ends: dict[int, int] = {}
        self.scanned = 0
        self.budget = 4 * len(text) + 4096
        self._last_closer: dict[str, int] = {}

    def _opens_quote(self, i: int) -> bool:
        """Whether the quote character at ``i`` can open a quoted region.

        An apostrophe must not follow a letter or digit (``it's``, ``james'``). Any quote character needs a valid
        closer later (for an apostrophe, one not directly followed by a letter or digit): a lone quote is prose,
        never a string that swallows the rest of the input.
        """
        text = self.text
        quote = text[i]
        if quote == "'" and i > 0 and text[i - 1].isalnum():
            return False
        last = self._last_closer.get(quote)
        if last is None:
            last = text.rfind(quote)
            while last >= 0 and not _closes(text, last, quote):
                last = text.rfind(quote, 0, last)
            self._last_closer[quote] = last
        return i < last

    def end(self, start: int) -> int:
        """Index past the ``]`` matching the ``[`` at ``start``, or ``-1`` when it is unclosed or out of budget."""
        if start in self.ends:
            return self.ends[start]
        rescan = start < self.scanned
        if rescan and self.budget <= 0:
            return -1
        reached = self._pass(start)
        if rescan:
            self.budget -= reached - start
        self.scanned = max(self.scanned, reached)
        return self.ends[start]

    def _pass(self, start: int) -> int:
        text = self.text
        n = len(text)
        stack: list[int] = []
        quote = ""
        i = start
        while i < n:
            ch = text[i]
            if quote or (ch in _PLAIN_QUOTES and self._opens_quote(i)):
                i, quote = _quote_step(text, i, quote)
            elif ch == "[":
                known = self.ends.get(i)
                if known is None:
                    stack.append(i)
                elif known < 0:
                    break  # unclosed from here on, so every enclosing bracket is unclosed too
                else:
                    i = known
                    continue
            elif ch == "]" and stack:
                self.ends[stack.pop()] = i + 1
                if not stack:
                    return i + 1
            i += 1
        for open_at in stack:
            self.ends[open_at] = -1
        return min(i, n)


def _bracketed_end(scan: _BracketScan, start: int) -> int:
    """End of the bracketed value opening at ``start``: past its matching ``]``, even across lines.

    An unclosed ``[`` ends at the end of its first line.
    """
    end = scan.end(start)
    return end if end >= 0 else _first_line_end(scan.text, start)


def _mask_assignments(text: str) -> str:
    """Mask the value of every sensitive-looking ``key SEP value`` in one left-to-right sweep."""
    out: list[str] = []
    pos = 0
    scan = _BracketScan(text)
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
        elif text.startswith("[", i):
            replacement = (REDACTED, _bracketed_end(scan, i))
        else:
            bare = _UNQUOTED.match(text, i)
            replacement = (REDACTED, bare.end()) if bare else None
        if replacement is None:
            continue
        out += (text[pos:i], replacement[0])
        pos = replacement[1]
    out.append(text[pos:])
    return "".join(out)


def _after_host(rest: str, ats: list[int]) -> bool:
    """Whether the last ``@`` sits in a path: an earlier ``@`` is followed by a host and then a ``/``, ``?`` or ``#`` before it."""
    if len(ats) < _MIN_ATS_FOR_PATH_ADDRESS:
        return False
    prev, last = ats[-2], ats[-1]
    sep = min((i for i in (rest.find(c, prev + 1, last) for c in "/?#") if i >= 0), default=-1)
    return sep > prev + 1


def _credential_separator(rest: str) -> int:
    """Index of the ``@`` that ends the password in ``rest``, or ``-1`` when the text holds no credential.

    ``rest`` stops where the next URL starts. A leading ``1-5 digits`` followed by ``/``, ``?``, ``#`` or the end
    is ``host:port``: only an ``@`` before the first ``?`` or ``#`` that is neither a path handle (``/@alice``)
    nor the tail of a path address counts. Otherwise the separator is the last ``@``, skipping ``/@`` handles
    once an earlier ``@`` exists and trailing addresses (``a@b.com``) once an earlier ``@`` exists and a ``/``, ``?`` or ``#`` precedes it, so a password
    holding ``@``, ``?``, ``#`` or ``/`` is masked whole while a path or query address stays visible.
    """
    port = _PORT.match(rest) is not None
    limit = len(rest)
    if port:
        limit = min((i for i in (rest.find("?"), rest.find("#")) if i >= 0), default=limit)
    ats: list[int] = []
    at = rest.find("@", 0, limit)
    while at >= 0:
        if not (at > 0 and rest[at - 1] == "/" and (port or ats)):
            ats.append(at)
        at = rest.find("@", at + 1, limit)
    while ats and _ADDRESS_TAIL.match(rest, ats[-1] + 1) and (port or _after_host(rest, ats)):
        ats.pop()
    return ats[-1] if ats else -1


def _mask_url_credentials(text: str) -> str:
    """Keep the user and the host; mask the password, which may itself hold ``/`` or ``@``."""
    out: list[str] = []
    pos = 0
    scan = 0
    while match := _URL_USER.search(text, scan):
        start = match.end()
        nxt = text.find("://", start)
        if nxt >= 0:
            while nxt > start and (text[nxt - 1].isalnum() or text[nxt - 1] in "+.-"):
                nxt -= 1
        end = _NON_SPACE.match(text, start, len(text) if nxt < 0 else nxt)
        stop = end.end() if end else start
        at = _credential_separator(text[start:stop])
        if at < 0:
            scan = start  # not a credential: rescan the text it spans, a later URL may hold one
            continue
        out += (text[pos:start], REDACTED)
        pos = scan = start + at
    out.append(text[pos:])
    return "".join(out)


def redact_text(text: str, secrets: Iterable[str] = (), limit: int = MAX_TEXT) -> str:
    """Mask secret-looking assignments, tokens, credentials in URLs, ``Row(...)`` and literal ``secrets``; bound the length."""
    out = text
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        out = out.replace(secret, REDACTED)
    window = max(limit, _MAX_SCAN)
    cut = len(out) > window
    out = out[:window]
    out = _mask_url_credentials(_mask_assignments(_BEARER.sub(rf"\1 {REDACTED}", out)))
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
