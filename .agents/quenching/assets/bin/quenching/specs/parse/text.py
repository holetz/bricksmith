"""Text primitives the spec parser rests on — comment handling, the frontmatter
split, and the markdown regexes every other module here matches with.

Moved verbatim out of the pre-refactor specs script."""
from __future__ import annotations

import re


PLACEHOLDER_RE = re.compile(r"<[^>\n]+>")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
BULLET_RE = re.compile(r"^\s*[-*+]\s")
SUBHEADING_RE = re.compile(r"^\s*(?:#{1,6}\s+|\*\*\S)")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
STANDARD_PATH_RE = re.compile(r"/docs/standards/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*\.md")


def split_lines(text: str, keepends: bool = False) -> list[str]:
    """Split on `\\n` only, as `str.splitlines` would for a text with no other separator.

    `str.splitlines` also breaks on U+2028/U+2029/U+0085/VT/FF/FS-RS, so a task line carrying one
    was read as two lines and lost its `files:`/`verify:` (spec 1392). A `\\r\\n` ends a line
    too; a lone `\\r` does not."""
    if keepends:
        return re.findall(r"[^\n]*\n|[^\n]+", text)
    parts = text.split("\n")
    last = parts.pop()          # the text after the final `\n`: empty, or an unterminated line
    out = [ln[:-1] if ln.endswith("\r") else ln for ln in parts]
    if last:
        out.append(last)
    return out


def strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def mask_comments(text: str) -> str:
    """Blank out HTML-comment spans, preserving every newline and column so line numbers
    and offsets still line up with the original.

    The template documents the task format with example checkboxes inside its comment
    guidance. Without this, those examples parse as real tasks and a freshly captured spec
    reports phantom progress — and `task --check` needs the surviving `lineno` to point at
    the true line, which stripping (rather than masking) would break."""
    if "<!--" not in text:
        return text
    return _COMMENT_RE.sub(
        lambda m: "\n".join(" " * len(ln) for ln in m.group(0).split("\n")), text)


def body_after_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2]
    return text


def has_real_content(text: str) -> bool:
    """True when a block carries authored prose beyond the shipped template (headings,
    HTML comments, and `<placeholder>` lines don't count).

    `- none — <reason>` DOES count: an explicit null is an answer, and the whole
    explicit-none rule depends on this returning True for it."""
    body = strip_comments(text) if "<!--" in text else text
    for line in split_lines(body):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s[0].isascii() and s[0].isalpha() and "<" not in s:
            return True          # a plain prose line: nothing below could strip its first letter
        residue = PLACEHOLDER_RE.sub("", s)
        residue = re.sub(r"^[-*+]\s*(\[.?\])?\s*", "", residue)  # drop list/checkbox markers
        residue = re.sub(r"^\d+(?:\.\d+)*\s*", "", residue)      # drop a leading task id
        if re.search(r"[A-Za-z0-9]", residue):
            return True
    return False
