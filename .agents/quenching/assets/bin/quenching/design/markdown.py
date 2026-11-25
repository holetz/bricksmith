"""Small deterministic Markdown/YAML helpers for generated design artifacts."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import escape as html_escape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class MarkdownBlock:
    kind: str
    value: Any


def split_h2(markdown: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in markdown.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            current = match.group(1)
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def read_sections(paths: list[Path]) -> dict[str, list[str]]:
    collected: dict[str, list[str]] = {}
    for path in paths:
        try:
            sections = split_h2(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        for heading, body in sections.items():
            if body:
                collected.setdefault(heading, []).append(body)
    return collected


def yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def yaml_map(value: dict[str, Any], indent: int = 0) -> list[str]:
    lines: list[str] = []
    pad = " " * indent
    for key, item in value.items():
        safe_key = key if re.fullmatch(r"[A-Za-z0-9_-]+", str(key)) else yaml_scalar(key)
        if isinstance(item, dict):
            lines.append(f"{pad}{safe_key}:")
            lines.extend(yaml_map(item, indent + 2))
        else:
            lines.append(f"{pad}{safe_key}: {yaml_scalar(item)}")
    return lines


def parse_yaml_subset(text: str) -> dict[str, Any]:
    """Parse the map-only YAML subset Impeccable 4.1.2 itself accepts."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or re.match(r"^\s*#", raw):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = raw[indent:]
        colon = _top_level_colon(content)
        if colon < 0:
            continue
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()
        key = _parse_scalar(content[:colon].strip())
        rest = _strip_comment(content[colon + 1:].strip())
        parent = stack[-1][1]
        if not isinstance(key, str):
            key = str(key)
        if not rest:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(rest)
    return root


def parse_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("DESIGN.md must start with YAML frontmatter")
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise ValueError("DESIGN.md frontmatter is not closed") from exc
    return parse_yaml_subset("\n".join(lines[1:end])), "\n".join(lines[end + 1:]).lstrip("\n")


def parse_document_frontmatter(markdown: str) -> tuple[dict[str, str], str]:
    """Parse the flat frontmatter used by genre documents."""
    front, body = parse_frontmatter(markdown)
    return {str(key): str(value) for key, value in front.items()}, body


def parse_markdown(markdown: str) -> list[MarkdownBlock]:
    """Parse the bounded Markdown subset used by editorial genre bodies."""
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[MarkdownBlock] = []
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        fence = re.match(r"^\s*(`{3,})(\w*)\s*$", lines[index])
        if fence:
            marker = fence.group(1)
            index += 1
            code: list[str] = []
            while index < len(lines) and not re.match(rf"^\s*{re.escape(marker)}\s*$", lines[index]):
                code.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.append(MarkdownBlock("code", "\n".join(code)))
            continue
        heading = re.match(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$", lines[index])
        if heading:
            blocks.append(MarkdownBlock("heading", (len(heading.group(1)), heading.group(2))))
            index += 1
            continue
        if index + 1 < len(lines) and _is_table_delimiter(lines[index + 1]) and "|" in lines[index]:
            header = _table_row(lines[index])
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                rows.append(_table_row(lines[index]))
                index += 1
            blocks.append(MarkdownBlock("table", (header, rows)))
            continue
        list_match = re.match(r"^\s*([-+*]|\d+[.])\s+(.+)$", lines[index])
        if list_match:
            ordered = list_match.group(1)[0].isdigit()
            items: list[str] = []
            while index < len(lines):
                match = re.match(r"^\s*([-+*]|\d+[.])\s+(.+)$", lines[index])
                if not match or match.group(1)[0].isdigit() != ordered:
                    break
                index += 1
                item_lines = [match.group(2)]
                while index < len(lines) and lines[index].strip():
                    if re.match(r"^\s*([-+*]|\d+[.])\s+", lines[index]):
                        break
                    if not re.match(r"^\s+", lines[index]):
                        break
                    item_lines.append(lines[index].strip())
                    index += 1
                items.append("\n".join(item_lines))
            blocks.append(MarkdownBlock("list", (ordered, items)))
            continue
        paragraph = [lines[index].strip()]
        index += 1
        while index < len(lines) and lines[index].strip() and not _starts_block(lines, index):
            paragraph.append(lines[index].strip())
            index += 1
        blocks.append(MarkdownBlock("paragraph", "\n".join(paragraph)))
    return blocks


def render_markdown(markdown: str, medium: str) -> str:
    """Render the supported Markdown subset as safe HTML or Typst source."""
    if medium not in {"html", "typst", "pdf"}:
        raise ValueError(f"unsupported Markdown medium: {medium}")
    target = "typst" if medium == "pdf" else medium
    rendered = [_render_block(block, target) for block in parse_markdown(markdown)]
    return "\n".join(item for item in rendered if item).strip()


def _starts_block(lines: list[str], index: int) -> bool:
    line = lines[index]
    return bool(re.match(r"^\s*(?:`{3,}|#{1,6}\s+|[-+*]\s+|\d+[.]\s+)", line))


def _is_table_delimiter(line: str) -> bool:
    cells = _table_row(line)
    return bool(cells) and all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in cells)


def _table_row(line: str) -> list[str]:
    value = line.strip()
    value = value.removeprefix("|")
    if value.endswith("|") and not value.endswith("\\|"):
        value = value[:-1]
    return [cell.strip().replace("\\|", "|") for cell in value.split("|")]


def _render_block(block: MarkdownBlock, medium: str) -> str:
    if block.kind == "paragraph":
        body = _inline(block.value, medium)
        return f"<p>{body}</p>" if medium == "html" else body
    if block.kind == "heading":
        level, value = block.value
        body = _inline(value, medium)
        return f"<h{level}>{body}</h{level}>" if medium == "html" else f"{'=' * level} {body}"
    if block.kind == "code":
        if medium == "html":
            return f"<pre><code>{html_escape(block.value)}</code></pre>"
        return f"#raw(block: true, {json.dumps(block.value, ensure_ascii=False)})"
    if block.kind == "list":
        ordered, items = block.value
        if medium == "html":
            tag = "ol" if ordered else "ul"
            return f"<{tag}>" + "".join(f"<li>{_inline(item, medium)}</li>" for item in items) + f"</{tag}>"
        marker = "+" if ordered else "-"
        return "\n".join(f"{marker} {_inline(item, medium)}" for item in items)
    header, rows = block.value
    if medium == "html":
        head = "<tr>" + "".join(f"<th>{_inline(cell, medium)}</th>" for cell in header) + "</tr>"
        body = "".join("<tr>" + "".join(f"<td>{_inline(cell, medium)}</td>" for cell in row) + "</tr>" for row in rows)
        return f"<table><thead>{head}</thead><tbody>{body}</tbody></table>"
    cells = header + [cell for row in rows for cell in row]
    return "#table(columns: %d, %s)" % (len(header), ", ".join(f"[{_inline(cell, medium)}]" for cell in cells))


def _inline(value: str, medium: str) -> str:
    pattern = re.compile(r"(`+)(.+?)\1|\[([^\]]+)\]\(([^)]+)\)|(\*\*|__)(.+?)\5|(\*|_)(.+?)\7", re.DOTALL)
    result: list[str] = []
    cursor = 0
    for match in pattern.finditer(value):
        result.append(_plain(value[cursor:match.start()], medium))
        if match.group(2) is not None:
            result.append(_code(match.group(2), medium))
        elif match.group(3) is not None:
            label, destination = match.group(3), match.group(4)
            if _safe_link(destination):
                result.append(_link(label, destination, medium))
            else:
                result.append(_plain(match.group(0), medium))
        elif match.group(6) is not None:
            result.append(_strong(match.group(6), medium))
        else:
            result.append(_emphasis(match.group(8), medium))
        cursor = match.end()
    result.append(_plain(value[cursor:], medium))
    return "".join(result)


def _plain(value: str, medium: str) -> str:
    if medium == "html":
        return html_escape(value).replace("\n", "<br>\n")
    return re.sub(r"([\\#$\[\]])", r"\\\1", value).replace("\n", "\n")


def _code(value: str, medium: str) -> str:
    return f"<code>{html_escape(value)}</code>" if medium == "html" else f"#raw({json.dumps(value, ensure_ascii=False)})"


def _link(label: str, destination: str, medium: str) -> str:
    if medium == "html":
        return f'<a href="{html_escape(destination, quote=True)}">{_inline(label, medium)}</a>'
    return f"#link({json.dumps(destination)})[{_inline(label, medium)}]"


def _strong(value: str, medium: str) -> str:
    body = _inline(value, medium)
    return f"<strong>{body}</strong>" if medium == "html" else f"#strong[{body}]"


def _emphasis(value: str, medium: str) -> str:
    body = _inline(value, medium)
    return f"<em>{body}</em>" if medium == "html" else f"#emph[{body}]"


def _safe_link(destination: str) -> bool:
    parsed = urlparse(destination.strip())
    return not parsed.scheme or parsed.scheme.lower() in {"http", "https"}


def _top_level_colon(value: str) -> int:
    quote: str | None = None
    for index, char in enumerate(value):
        if quote:
            if char == quote and (index == 0 or value[index - 1] != "\\"):
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == ":":
            return index
    return -1


def _strip_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if quote:
            if char == quote and value[index - 1] != "\\":
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == "#" and index > 0 and value[index - 1].isspace():
            return value[:index].rstrip()
    return value


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if value == "true":
        return True
    if value == "false":
        return False
    if value in {"null", "~"}:
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?(?:\d+\.\d*|\.\d+)", value):
        return float(value)
    return value
