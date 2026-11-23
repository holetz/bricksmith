"""The frontmatter writers — one key, one record, the state fields a native surface
stores instead, and the legacy dated-basename fold.

Moved verbatim out of the pre-refactor specs script."""
from __future__ import annotations

import re

from quenching.common.frontmatter import parse_frontmatter
# The pre-date-in-frontmatter basename, read ONLY by the migration fold below — which
# exists precisely to recognise a layout this tool no longer writes. It moved here from
# `parse/spec.py` with the rest of the basename grammar's retirement: this is the one use
# left, and a pattern with one caller belongs beside it.
LEGACY_DATED_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md$")


# Every character `str.splitlines` breaks on — the split `parse_frontmatter` reads the block
# with. A scalar carrying one is read back as two lines, the second a key of its own: a title
# could stamp `approved:` (spec 1340).
SCALAR_LINE_BREAKS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")


def scalar_break(value: str) -> str | None:
    """The first character no frontmatter scalar may carry, as `U+XXXX`, or None.

    A line break is what forges a key; the other C0/C1 controls are refused beside it, since no
    title, tag or record value has a use for one. The tab is the one control a value keeps."""
    for c in value:
        if c in SCALAR_LINE_BREAKS or (c != "\t" and (c < " " or "\x7f" <= c <= "\x9f")):
            return f"U+{ord(c):04X}"
    return None


class UnclosedFence(ValueError):
    """The document opens a `---` frontmatter fence that never closes: a writer cannot land a
    key in it, and returning the text unchanged would let the caller report a write that did
    not happen. `main` carries it to exit 2."""

    def __init__(self, key: str) -> None:
        super().__init__(f"the frontmatter fence is never closed, so `{key}` cannot be written")
        self.key = key


def _refuse_line_break(value: str) -> None:
    if any(c in SCALAR_LINE_BREAKS for c in value):
        raise ValueError(f"a frontmatter value may not carry a line break: {value!r}")


def _top_level_key(line: str) -> str | None:
    if line[:1] in (" ", "\t") or ":" not in line:
        return None
    return line.split(":", 1)[0].strip()


def _continuation_end(lines: list[str], i: int, close: int) -> int:
    end = i + 1
    while end < close and lines[end][:1] in (" ", "\t") and lines[end].strip():
        end += 1
    return end


def set_frontmatter_key(text: str, key: str, value: str,
                        after: str | None = None) -> str:
    """Set one top-level frontmatter key, preserving every other line as authored.

    Rewriting the block wholesale would reformat a human's `refined: {mode, date}` and
    reorder their keys — a promote is a move, and the outcome stamp is the ONLY content it
    is allowed to write.

    Only a top-level line is the key — an indented `date:` belongs to a record — and its
    indented continuation (a `>-` scalar, a block list) is replaced with it, never orphaned.
    A value carrying a line break raises: it would forge a key of its own.

    `after` places a key that does not exist yet directly below a named one, instead of at
    the end of the block. It exists for `date`, which the marker fold inserts into documents
    written before the field did: appended, it would land under `verification` and every
    migrated spec would read in a different order from every freshly captured one, for no
    reason a reader could see. An absent `after` key falls back to the end."""
    _refuse_line_break(value)
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return f"---\n{key}: {value}\n---\n\n" + text
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        raise UnclosedFence(key)
    for i in range(1, close):
        if _top_level_key(lines[i]) == key:
            return "".join(lines[:i] + [f"{key}: {value}\n"]
                           + lines[_continuation_end(lines, i, close):])
    at = close
    if after:
        for i in range(1, close):
            if _top_level_key(lines[i]) == after:
                at = _continuation_end(lines, i, close)
                break
    lines.insert(at, f"{key}: {value}\n")
    return "".join(lines)


_PLAIN_BAD_START = set("-?:,[]{}#&*!|>'\"%@`")


def yaml_title_scalar(text: str) -> str:
    """`text` as a frontmatter scalar `parse_frontmatter` reads back unchanged.

    Plain when a YAML reader would return it as written — the shape `new` has always stamped,
    which the hybrid backend's title projection matches byte for byte. Quoted otherwise: a
    ` #` opens a comment, `: ` a mapping, a leading indicator a node, and a title with any of
    them was read back truncated or lost."""
    plain = (text == text.strip() and text != ""
             and text[0] not in _PLAIN_BAD_START
             and " #" not in text and "\t#" not in text
             and ": " not in text and not text.endswith(":"))
    if plain:
        return text
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text and "\\" not in text:
        return f'"{text}"'
    return "'" + text.replace("'", "''") + "'"


def set_frontmatter_title(text: str, title: str) -> str:
    """Set `title:` in the first key's place and drop every later top-level `title:` of the
    block, each with its continuation lines."""
    out = set_frontmatter_key(text, "title", yaml_title_scalar(title))
    lines = out.splitlines(keepends=True)
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), len(lines))
    seen = False
    kept = []
    i = 0
    while i < len(lines):
        if 0 < i < close and _top_level_key(lines[i]) == "title":
            if seen:
                i = _continuation_end(lines, i, close)
                continue
            seen = True
        kept.append(lines[i])
        i += 1
    return "".join(kept)


def strip_frontmatter_keys(text: str, keys: tuple[str, ...]) -> str:
    """`text` with each of `keys`' own frontmatter line removed — the WRITE half of
    `## Design` §Stored is not projected for `tags`/`assignee`/`start`/`target`: the
    native field is the storage, so the document stored alongside it never carries a second,
    unread copy that would go stale the instant a human edited the tracker instead.

    Unlike `hybrid_title_split`, this never refuses. `title` is required and paired with a
    `# <TITLE>` heading it has to reproduce at an exact offset; these four are optional
    scalars/lists with no second copy elsewhere in the document, so dropping the line is the
    whole operation — nothing to reconstruct on the way back, because `read_spec` reassembles
    the VALUES into `info['frontmatter']` directly rather than rebuilding the text."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        raise UnclosedFence(", ".join(keys))
    kept = [ln for i, ln in enumerate(lines)
           if not (1 <= i < close and ln.split(":", 1)[0].strip() in keys)]
    return "".join(kept)


def carry_forward_fields(old_fm: dict, new_fm: dict, keys: tuple[str, ...]) -> dict:
    """`keys`, from `new_fm` where THIS WRITE's own text declared one explicitly, and from
    `old_fm` (the pre-write read) where it did not.

    THE BUG THIS FIXES: a key `strip_frontmatter_keys` removes is gone from the document
    once stored, so an ORDINARY write — editing a section, ticking a task — hands
    `write_spec` text that never mentions `tags` at all. Reading that absence as "clear the
    tags" would wipe every stored field on the next unrelated write. Presence in `new_fm` is
    the signal an explicit `cmd_field`/`--subject` write leaves behind — `set_frontmatter_key`
    inserts the line it is asked to set — so presence, not truthiness, is what is asked
    (`tags: []` is a real instruction to clear; the key being ABSENT is silence)."""
    return {k: new_fm[k] if k in new_fm else old_fm.get(k) for k in keys}


def legacy_marker_fold(filename: str, text: str) -> tuple[str, str] | None:
    """`("<slug>.md", the document carrying its own `date:`)` for a spec still stored under
    the dated basename — or None when there is nothing to fold.

    THE PREFIX IS THE ONLY COPY OF THE DATE. A document written before `date:` existed keeps
    its capture date nowhere but that basename, so the fold has to move it into the
    frontmatter in the SAME step that drops it. Dropping first loses the fact; adding first
    and dropping later leaves two copies free to disagree in between. One function, both
    halves, so no caller can perform half of it.

    A document that already declares `date:` keeps its own — only the name was stale. The
    basename's copy is never allowed to win, because a human may have corrected the field and
    nobody ever renames an issue's marker to correct a date."""
    m = LEGACY_DATED_FILE_RE.match(filename)
    if not m:
        return None
    date, slug = m.group(1), m.group(2)
    if str(parse_frontmatter(text).get("date", "")).strip():
        return f"{slug}.md", text
    return f"{slug}.md", set_frontmatter_key(text, "date", date, after="title")


def _render_record(key: str, rec: dict) -> list[str]:
    """A record as frontmatter lines — flow where flow survives a round trip, block where
    it would not. A value carrying a comma cannot go in `{a: b, c: d}`, which
    `parse_frontmatter` splits on commas; a long one wraps in an editor and stops parsing
    as one line. Both are the block form's whole reason to exist."""
    parts = [f"{k}: {v}" for k, v in rec.items()]
    line = f"{key}: {{{', '.join(parts)}}}"
    if len(line) <= 96 and not any("," in str(v) for v in rec.values()):
        return [line + "\n"]
    return [f"{key}:\n"] + [f"  {p}\n" for p in parts]


def set_frontmatter_record(text: str, key: str, rec: dict) -> str:
    """Replace ONE record, its continuation lines included, preserving every other line.

    `set_frontmatter_key` writes one scalar line; a record is rendered here in flow or block
    form, and a block record already written occupies continuation lines this replaces with it."""
    for v in rec.values():
        _refuse_line_break(str(v))
    new_lines = _render_record(key, rec)
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "---\n" + "".join(new_lines) + "---\n\n" + text
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        raise UnclosedFence(key)
    for i in range(1, close):
        if _top_level_key(lines[i]) == key:
            return "".join(lines[:i] + new_lines + lines[_continuation_end(lines, i, close):])
    return "".join(lines[:close] + new_lines + lines[close:])


# The four STATE frontmatter keys — never records, each with a faithful native counterpart
# in at least one backend (`spec-backend.md` §A native value is the same fact).
FIELD_KEYS = ("tags", "assignee", "start", "target")
