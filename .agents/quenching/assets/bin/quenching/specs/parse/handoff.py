"""`## Handoff` — the global block plus one block per `### N.` heading, and which one
a scoped write targets.

Moved verbatim out of the pre-refactor specs script."""
from __future__ import annotations

import re

from quenching.specs.parse.sections import parse_sections
from quenching.specs.parse.text import HEADING_RE, mask_comments


HANDOFF_HEADING_NUM_RE = re.compile(r"^(\d+)\.")


def parse_handoff(text: str) -> dict:
    """`## Handoff` split into its global block plus one block per `### N.` heading — the
    SAME level-3 grouping `parse_tasks` already runs over `## Tasks`, reused rather than
    redefined. A block is matched to its `## Tasks` counterpart by the LEADING NUMERAL in
    its own heading text (`### 2. Segunda seção` → section 2), never by encounter order
    and never by comparing title text: a section can close — and never receive its own
    `## Handoff` block — before any later section's block is ever written, so encounter
    order and the task-section number are not the same count. A heading with no leading
    numeral (malformed, or hand-edited) falls back to one past the highest section seen.

    No `### ` heading at all — today's flat format, or a spec not yet built — reads back as
    a single global block and an empty `blocks` list, so nothing existing needs to migrate."""
    sections = parse_sections(mask_comments(text))
    if "Handoff" not in sections:
        return {"global": "", "blocks": []}
    base = sections["Handoff"]["lineno"] + 1
    lines = sections["Handoff"]["lines"]
    global_lines: list[str] = []
    blocks: list[dict] = []
    cur: dict | None = None
    body_start = 0

    def flush(end: int) -> None:
        if cur is not None:
            cur["body"] = "\n".join(lines[body_start:end])
            cur["blockEndLineno"] = base + end

    for i, line in enumerate(lines):
        hm = HEADING_RE.match(line)
        if hm and len(hm.group(1)) == 3:
            flush(i)
            title = hm.group(2).strip()
            num = HANDOFF_HEADING_NUM_RE.match(title)
            section = int(num.group(1)) if num else max((b["section"] for b in blocks),
                                                         default=0) + 1
            cur = {"section": section, "title": title,
                   "lineno": base + i, "body": "", "blockEndLineno": None}
            blocks.append(cur)
            body_start = i + 1
            continue
        if cur is None:
            global_lines.append(line)
    flush(len(lines))
    return {"global": "\n".join(global_lines), "blocks": blocks}


def current_handoff_section(tasks: list[dict]) -> int | None:
    """The `### N.` a scoped `## Handoff` write targets: the section of the next task
    still open and unblocked — the same task `cq specs next` would hand out — or, once
    every remaining task is blocked, the first blocked one's section, matching what
    `next` itself falls back to. Every task done → the LAST section, so a finished run's
    Handoff still describes where it landed. No tasks at all → None, nothing to scope to."""
    if not tasks:
        return None
    open_tasks = [t for t in tasks if not t["checked"]]
    if not open_tasks:
        return tasks[-1]["section"] or 1
    actionable = [t for t in open_tasks if not t["blocked"]]
    return (actionable[0] if actionable else open_tasks[0])["section"] or 1


def task_section_title(body_text: str, n: int) -> str | None:
    """The literal `### N. <title>` heading text `## Tasks` gives section N, read once so a
    `## Handoff` block opened for the first time can borrow a human-readable title instead
    of inventing one. Display gloss only — `parse_handoff` matches blocks to `## Tasks` by
    the heading's own leading numeral, never by this text."""
    sections = parse_sections(mask_comments(body_text))
    if "Tasks" not in sections:
        return None
    count = 0
    for line in sections["Tasks"]["lines"]:
        hm = HEADING_RE.match(line)
        if hm and len(hm.group(1)) == 3:
            count += 1
            if count == n:
                return hm.group(2).strip()
    return None
