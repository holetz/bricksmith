"""Generate the site `nav` from the bundle's publication allow-list.

The nav is read from the bundle's allowlisted homes and written into `zensical.toml`. It lives here
rather than in `projection.py` because that module derives the glossary snippet from the glossary,
and nothing about ordering pages is that.

Three properties the generator has to hold, and each one is a bug the naive version has:

* **Semantic order, never alphabetical.** Zensical falls back to the folder tree when `nav` is
  absent, which sorts alphabetically. The reading order of a Diátaxis site is not its sort order,
  and raw `catalog/`/`external/` homes are not site inputs, so `HOME_ORDER` below is the contract.
* **A title a human wrote survives.** The derived title is a default, not an assertion. Whoever
  fixes `Ci Cd` to `CI/CD` must not have to fix it again after the next run — which is why this
  generator reads the existing nav before writing one, and why it can never be "delete and
  rewrite".
* **Idempotent to the byte.** Running twice changes nothing, so the check mode is just a string
  comparison and a stale nav is a diff rather than an opinion.
"""
from __future__ import annotations

import os
import re

from quenching.knowledge.corpus import is_private_name
from quenching.knowledge.schema import ASSET_DIRS, EXEMPT
from quenching.knowledge.site_source import PUBLISHED_HOMES

# The site's reading order. The nav is deliberately generated from the same publication allow-list
# as the staged source tree: Zensical has no supported exclusion setting, so catalogues and external
# research must be absent from both the menu and `docs_dir`, not merely omitted from this list.
HOME_ORDER = ("tutorials", "how-to", "documentation", "project",
              "standards", "concepts", "vision", "decisions")

_ATTR_SUFFIX = re.compile(r"\s*\{[^}]*\}\s*$")
_H1 = re.compile(r"^#\s+(.*?)\s*$", re.MULTILINE)
_FM_TITLE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)


def derive_title(text: str, fallback: str) -> str:
    """A section or page title from its own file, in the three forms this bundle actually uses.

    `# Getting started` -> `Getting started`; ``# `standards/quality/` `` -> `Quality`;
    ``# `standards/` — current/active reference`` -> `Standards`; and an `{ .attr }` suffix is
    stripped before any of that. A path-shaped H1 yields its LAST segment, because the heading
    names the folder and the nav is already nested under its parent.
    """
    frontmatter_title = _FM_TITLE.search(_frontmatter(text))
    raw = frontmatter_title.group(1) if frontmatter_title else ""
    if not raw:
        heading = _H1.search(_body(text))
        raw = heading.group(1) if heading else ""
    raw = _ATTR_SUFFIX.sub("", raw).strip()
    if len(raw) > 1 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1]
    raw = raw.split(" — ")[0].strip()
    if raw.startswith("`") and raw.endswith("`"):
        inner = raw.strip("`").strip()
        if "/" in inner:
            segment = [p for p in inner.split("/") if p]
            raw = segment[-1].replace("-", " ").title() if segment else inner
        else:
            raw = inner
    return raw or fallback


def _frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:] if end == -1 else text[3:end]


def _body(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    return text if end == -1 else text[end + 4:]


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return ""


def _entries(bundle_root: str, rel_dir: str) -> list:
    """One directory's nav entries: its own `index.md` first, then subsections, then pages.

    `assets/` and the harness pointers are excluded: the first holds no pages the reader wants a
    sidebar row for, the second is agent-facing. A private (`.`/`_`) name is skipped too, as the
    staged source skips it, so no row points at a page the site never carries. The staged-source builder decides which non-page
    assets are copied; these paths simply carry no nav line.
    """
    abs_dir = os.path.join(bundle_root, rel_dir) if rel_dir else bundle_root
    try:
        names = sorted(os.listdir(abs_dir))
    except OSError:
        return []
    out: list = []
    index_rel = f"{rel_dir}/index.md" if rel_dir else "index.md"
    if os.path.isfile(os.path.join(bundle_root, index_rel)):
        out.append(index_rel)
    for name in names:
        if is_private_name(name) or name in ASSET_DIRS or name in EXEMPT:
            continue
        child_rel = f"{rel_dir}/{name}" if rel_dir else name
        if os.path.isdir(os.path.join(abs_dir, name)):
            nested = _entries(bundle_root, child_rel)
            if nested:
                out.append({"__section__": child_rel, "entries": nested})
        elif name.endswith(".md") and name != "index.md":
            out.append(child_rel)
    return out


def build_nav(bundle_root: str) -> list:
    """The nav for the bounded site source, in `HOME_ORDER`.

    Unknown homes are intentionally not appended: a new high-volume or internal home must earn a
    publication decision before it can reach Zensical.
    """
    nav: list = ["index.md"]
    ordered = [h for h in HOME_ORDER if h in PUBLISHED_HOMES
               and os.path.isdir(os.path.join(bundle_root, h))]
    for home in ordered:
        entries = _entries(bundle_root, home)
        if entries:
            nav.append({"__section__": home, "entries": entries})
    if os.path.isfile(os.path.join(bundle_root, "glossary.md")):
        nav.append("glossary.md")
    return nav


def _title_for(bundle_root: str, section_rel: str, kept: dict[str, str]) -> str:
    if section_rel in kept:
        return kept[section_rel]
    fallback = section_rel.split("/")[-1].replace("-", " ").title()
    return derive_title(_read(os.path.join(bundle_root, section_rel, "index.md")), fallback)


def _toml_str(value: str) -> str:
    """A TOML basic string: backslash first, then the quote, so neither is escaped twice."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


_ESCAPE = re.compile(r'\\(.)')


def _unescape(value: str) -> str:
    return _ESCAPE.sub(lambda m: m.group(1), value)


def render_nav(bundle_root: str, nav: list, kept: dict[str, str], indent: str = "  ") -> str:
    """The `nav = [ ... ]` block, formatted so a human diff shows one page per line."""
    lines = [f"{indent}nav = ["]

    def emit(items: list, depth: int) -> None:
        pad = indent + "  " * depth
        for item in items:
            if isinstance(item, str):
                lines.append(f"{pad}  {_toml_str(item)},")
            else:
                title = _title_for(bundle_root, item["__section__"], kept)
                lines.append(f"{pad}  {{ {_toml_str(title)} = [")
                emit(item["entries"], depth + 1)
                lines.append(f"{pad}  ] }},")

    emit(nav, 0)
    lines.append(f"{indent}]")
    return "\n".join(lines)


_SECTION_TITLE = re.compile(
    r'\{\s*"((?:[^"\\]|\\.)+)"\s*=\s*\[\s*\n?\s*"((?:[^"\\]|\\.)+)/index\.md"')


def kept_titles(config_text: str) -> dict[str, str]:
    """Section titles already in the config, keyed by the section's own directory.

    A section is identified by its `index.md`, never by its position: reordering the nav must not
    make the generator forget what a section was called.
    """
    return {_unescape(match.group(2)): _unescape(match.group(1)) for match in _SECTION_TITLE.finditer(config_text)}


def _bracket_delta(line: str) -> int:
    """`[` minus `]` on one line, outside TOML basic/literal strings and comments.

    A section title such as `Intervalos [0, 1)` is a string, and counting its bracket would leave
    the nav array open forever. Single-line strings never cross a line, so no state carries over.
    """
    delta, quote, i = 0, "", 0
    while i < len(line):
        char = line[i]
        if quote:
            if char == "\\" and quote == '"':
                i += 1
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#":
            break
        elif char == "[":
            delta += 1
        elif char == "]":
            delta -= 1
        i += 1
    return delta


class NavUnclosedError(ValueError):
    """The config opens `nav = [` and the file ends before the array closes."""


def replace_nav(config_text: str, block: str) -> str:
    """Swap the `nav = [...]` array in place, touching nothing else in the file.

    Bracket-counted rather than parsed, for the same reason `ensure_nav` was: this file is a
    human's, and a TOML round-trip would reformat every key it did not come for. An array that
    never closes raises `NavUnclosedError`: returning the text intact would read as `current`.
    """
    lines = config_text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if re.match(r"\s*nav\s*=\s*\[", line)), None)
    if start is None:
        return config_text
    depth = 0
    for end in range(start, len(lines)):
        depth += _bracket_delta(lines[end])
        if depth == 0:
            return "".join(lines[:start]) + block + "\n" + "".join(lines[end + 1:])
    raise NavUnclosedError(f"`nav = [` opened on line {start + 1} never closes")


def generate(bundle_root: str, config_path: str) -> tuple[str, str]:
    """Return `(current_text, desired_text)` for the config. Writing is the caller's."""
    current = _read(config_path)
    indent_match = re.search(r"^(\s*)nav\s*=\s*\[", current, re.MULTILINE)
    indent = indent_match.group(1) if indent_match else "  "
    block = render_nav(bundle_root, build_nav(bundle_root), kept_titles(current), indent)
    return current, replace_nav(current, block)
