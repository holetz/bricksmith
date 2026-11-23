"""The section engine — resolving a requested heading name, and splicing one section's
block back into a document in canonical position.

Moved verbatim out of the pre-refactor specs script."""
from __future__ import annotations

from quenching.specs.parse.handoff import (current_handoff_section, parse_handoff,
                                           task_section_title)
from quenching.specs.parse.sections import section_spans
from quenching.specs.parse.tasks import parse_tasks
from quenching.specs.parse.text import FENCE_RE, HEADING_RE, body_after_frontmatter, split_lines
from quenching.specs.schema import canonical_headings, section_guidance


def _canonical_index(heading: str, schema: dict | None = None) -> int:
    canon = canonical_headings(schema)
    return canon.index(heading) if heading in canon else len(canon)


def resolve_heading_name(name: str, candidates: list[str]) -> str | None:
    """One requested name onto the candidate that answers it, or None.

    **`§X` and `## X` are the same request, and a UNIQUE prefix resolves** — the two forms
    `SECTION_CASES` pins, which the components pillar carries verbatim. The command bodies already cite
    sections as `§Handoff`, so a reader that refused over the marker would be unusable from the
    very prose it serves. An ambiguous prefix resolves to nothing: a guess between two headings is
    worse than the refusal that names them.

    It takes the candidate list as an argument for one reason — so the canonical cases exercise
    THIS function over the fixture's headings, rather than a second copy of the rule written
    inside the selftest. A rule proved against a private re-implementation is not proved."""
    want = " ".join(name.strip().lstrip("#§").strip().split()).lower()
    if not want:
        return None
    for c in candidates:
        if c.lower() == want:
            return c
    hits = [c for c in candidates if c.lower().startswith(want)]
    return hits[0] if len(hits) == 1 else None


def _match_heading(heading: str) -> str | None:
    """Case-insensitive lookup onto the canonical spelling. Headings are a parsed contract,
    so the FILE always carries canonical English — but a human typing `cq specs section x
    validation` should not get a stray section for their trouble."""
    return resolve_heading_name(heading, canonical_headings())


def split_section_stream(text: str, candidates: list[str]) -> list[tuple[str | None, str]]:
    """One stdin stream cut into the sections it carries, in the order it carries them.

    **The delimiter is the document's own `## <Heading>` line** — the exact form the plural
    read already prints — so writing N sections needs no syntax the file does not already
    have, and read and write round-trip.

    **Self-describing or not at all.** A stream declares itself by OPENING on a canonical
    `## <Heading>`; anything else — prose first, a heading that resolves to nothing — makes it
    one raw body, and the empty list says so. The caller then writes it whole into the single
    heading it was given, which is the behaviour every caller had before this function
    existed, byte for byte. Once a stream has declared itself, every later `## ` in it must
    resolve too: there the unresolved name is a refusal, not a reason to re-read the whole
    stream as prose.

    **Cut by MEMBERSHIP, never by position** — each block's name goes through
    `resolve_heading_name` against the candidate set the caller passes in, exactly as
    `_match_heading` resolves a name typed on the command line, and an unresolved one comes
    back as `None` for the caller to refuse by name. The candidate set is an argument for the
    reason `/docs/standards/code/canonical-set-parsing.md` gives: the cases prove the
    function that ships, not a copy of its rule.

    **A fenced block is never read as a heading** — the rule `parse_sections` and the
    components reader already hold, and the one that keeps a `## Design` inside a ```` ``` ````
    example from cutting the body that quotes it.

    Deliberately NOT `parse_sections`: that reader answers a different question — one entry
    per heading, keyed, three-state — and it collapses a repeated heading onto the first.
    Here the repeat is the finding, so the blocks stay a list and every one of them survives.
    """
    heads: list[str | None] = []
    bodies: list[list[str]] = []
    buf: list[str] = []
    fence: str | None = None
    for line in split_lines(text):
        m = FENCE_RE.match(line)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark[0] * len(mark)
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
        elif fence is None:
            h = HEADING_RE.match(line)
            if h and len(h.group(1)) == 2:
                name = resolve_heading_name(h.group(2).strip(), candidates)
                if not heads and (name is None or any(prev.strip() for prev in buf)):
                    return []
                heads.append(name)
                buf = []
                bodies.append(buf)
                continue
        buf.append(line)
    return [(h, "\n".join(b).strip("\n")) for h, b in zip(heads, bodies)]


def nested_heading(text: str) -> str | None:
    """The first unfenced `## ` line of a text, or None — the line that, spliced into a document,
    would open a section the writer never named. Same fence rule `parse_sections` holds."""
    fence: str | None = None
    for line in split_lines(text):
        m = FENCE_RE.match(line)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark[0] * len(mark)
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
        elif fence is None:
            h = HEADING_RE.match(line)
            if h and len(h.group(1)) == 2:
                return line.strip()
    return None


def unclosed_fence(text: str) -> str | None:
    """The opening line of a fence the text never closes, or None.

    Spliced into a document, such a fence runs past the section's end, and `parse_sections`
    then reads every following `## ` as code: the sections after it vanish, and the next write
    of this section deletes them. Same fence rule `parse_sections` holds."""
    found = unclosed_fence_at(text)
    return found[1] if found else None


def unclosed_fence_at(text: str) -> tuple[int, str] | None:
    """`(1-based line number, opening line)` of the fence the text never closes, or None —
    `unclosed_fence` with the position, so a check over a whole stored document can name the
    line a reader has to go to."""
    fence: str | None = None
    opener: tuple[int, str] = (0, "")
    for lineno, line in enumerate(split_lines(text), 1):
        m = FENCE_RE.match(line)
        if not m:
            continue
        mark = m.group(1)
        if fence is None:
            fence, opener = mark[0] * len(mark), (lineno, line.strip())
        elif mark[0] == fence[0] and len(mark) >= len(fence):
            fence = None
    return opener if fence is not None else None


def whole_body(content: str, heading: str) -> tuple[str, str | None]:
    """A body this command splices whole under `## <heading>`, checked for the one thing that
    makes the splice write a second section: an unfenced `## ` line of its own.

    A FIRST line that is the section's own heading is the form the read prints, so it is
    dropped rather than refused — writing back what was read must round-trip. Any other
    unfenced `## ` line comes back as the refusal; the same fence rule `parse_sections`
    holds keeps a quoted `## Design` inside a code block legal. Returns (body, offending line)."""
    lines = split_lines(content)
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is not None:
        h = HEADING_RE.match(lines[first])
        if h and len(h.group(1)) == 2 and h.group(2).strip().lower() == heading.lower():
            lines = lines[first + 1:]
    bad = nested_heading("\n".join(lines))
    if bad:
        return content, bad
    return "\n".join(lines), None


def fold_stray_heading(info: dict, stray: str,
                       schema: dict | None = None) -> tuple[str, str | None]:
    """Demote one stray `## X` to `### X` **in place**, so its whole body becomes part of the
    canonical section that precedes it. Returns the new text and the host heading, or the text
    untouched and `None` when no canonical section stands above the stray.

    **The edit is one `#` prepended to one line, and that is the design, not an economy.**
    `parse_sections` promotes every `## X` to a top-level section and keeps `###`+ with its
    parent, so demoting the heading is exactly what moves the body — no lines are cut, moved or
    re-emitted, which is what makes "the text is preserved byte for byte" a property of the
    operation rather than a claim to be tested. Routing this through `upsert_section` instead
    would re-splice the HOST in canonical position and could reorder a document whose sections
    are out of schema order, for an edit that never needed to move anything.

    **The host is the nearest canonical section ABOVE the stray, and never one named by the
    caller.** Position in the document is the strongest evidence of intent: the stray was
    written *inside* a body and only became a sibling because of its heading level. A stray
    with nothing canonical above it has no honest host, and `None` is the refusal — inventing
    one would give the text an owner its author never chose.

    Resolving the stray to a heading this document actually carries belongs to the caller,
    which needs the candidate list to refuse by name anyway."""
    canon = set(canonical_headings(schema))
    sec = info["sections"][stray]
    above = sorted((s["lineno"], h) for h, s in info["sections"].items()
                   if s["lineno"] < sec["lineno"] and h in canon)
    if not above:
        return info["text"], None
    lines = split_lines(info["text"], keepends=True)
    # sections were parsed from the body, so the heading's line number needs the frontmatter back
    fm_offset = len(lines) - len(split_lines(body_after_frontmatter(info["text"]), keepends=True))
    at = sec["lineno"] + fm_offset
    lines[at] = "#" + lines[at]
    return "".join(lines), above[-1][1]


def upsert_section(info: dict, heading: str, block: str) -> tuple[str, str]:
    """Replace a section's block, or create it in CANONICAL POSITION when absent.

    Position is derived from the schema's declared order, not from where the writer
    happened to be: a spec whose `## Tasks` was written before its `## Proposal` still
    reads in contract order, so a human and the parser see the same document."""
    text = info["text"]
    lines = split_lines(text, keepends=True)
    # sections were parsed from the body, so their line numbers need the frontmatter back
    fm_offset = len(lines) - len(split_lines(body_after_frontmatter(text), keepends=True))
    if heading in info["sections"]:
        sec = info["sections"][heading]
        start = sec["lineno"] + fm_offset
        end = start + 1 + len(sec["lines"])
        # A repeated heading is a defect `parse_sections` hides (it keeps the FIRST); the
        # rewrite is the repair, so the later copies of the same heading go with it.
        drop = [(a + fm_offset, b + fm_offset)
                for h, a, b in section_spans(body_after_frontmatter(text))
                if h == heading and a + fm_offset > start]
        for a, b in sorted(drop, reverse=True):
            lines[a:b] = []
        return "".join(lines[:start]) + block + "".join(lines[end:]), "replaced"
    idx = _canonical_index(heading)
    following = [sec["lineno"] + fm_offset for h, sec in info["sections"].items()
                 if _canonical_index(h) > idx]
    if following:
        at = min(following)
        return "".join(lines[:at]) + block + "\n" + "".join(lines[at:]), "created"
    return text.rstrip() + "\n\n" + block, "created"


def write_handoff_block(info: dict, scope: str, body: str) -> tuple[str, str]:
    """Replace ONE piece of `## Handoff` — `scope="global"` for the evergreen block, or
    `scope="current"` for the `### N.` block matching the next actionable task's section
    — and leave every OTHER block exactly as it stood. That is the whole of "closing": a
    section's block is simply never targeted again once its tasks are done, the same way
    a merged branch needs no explicit "done" flag.

    Reuses `upsert_section` for the actual splice rather than re-deriving its frontmatter
    offset math — this function's only job is to compute `## Handoff`'s new FULL body
    with one block swapped, then hand that whole block to the existing writer."""
    body_text = body_after_frontmatter(info["text"])
    parsed = parse_handoff(body_text)
    new_body = body.strip("\n")

    if scope == "global":
        global_text, blocks = new_body, parsed["blocks"]
    else:
        tasks = parse_tasks(body_text)
        n = current_handoff_section(tasks)
        if n is None:
            return info["text"], "no-op"
        global_text = parsed["global"].strip("\n")
        title = next((b["title"] for b in parsed["blocks"] if b["section"] == n),
                     None) or task_section_title(body_text, n) or str(n)
        blocks = [dict(b, body=new_body) if b["section"] == n else b
                 for b in parsed["blocks"]]
        if not any(b["section"] == n for b in parsed["blocks"]):
            blocks = blocks + [{"section": n, "title": title, "body": new_body}]

    parts = [global_text] if global_text else []
    parts += [f"### {b['title']}\n\n{b['body'].strip(chr(10))}" for b in blocks]
    section_text = "\n\n".join(p for p in parts if p.strip())
    block = f"## Handoff\n\n{section_text}\n" if section_text else section_guidance("Handoff")
    return upsert_section(info, "Handoff", block)
