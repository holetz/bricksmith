"""`## ` sections of a spec document — the parse, the three-state rule, and the gates
computed over them.

Moved verbatim out of the pre-refactor specs script."""
from __future__ import annotations

import re

from quenching.specs.parse.text import (BULLET_RE, FENCE_RE, HEADING_RE, PLACEHOLDER_RE,
                                        STANDARD_PATH_RE, SUBHEADING_RE, has_real_content, split_lines,
                                        strip_comments)
from quenching.specs.schema import canonical_headings, load_schema, phase_spec


def parse_sections(text: str) -> dict[str, dict]:
    """Every level-2 section of a spec file, keyed by its heading text.

    Sub-headings (`###`+) belong to their parent section — `## Impact` carries its parsed
    `### Standards …` sub-heading, and splitting on them would orphan it.

    Each entry carries `lines` (the body), `lineno` (0-based, of the heading itself), and
    `filled` — the three-state distinction the whole contract rests on: a heading that is
    present but empty is MALFORMED, which is neither an answer nor a not-yet.

    **A fenced block is never read as a heading.** `## Tasks` routinely carries a shell block,
    and a `## ` comment inside one used to open a phantom section — which `validate` then
    reported as a stray heading, and which silently truncated the real section at that line."""
    out: dict[str, dict] = {}
    current: str | None = None
    buf: list[str] = []
    start = 0
    lines = split_lines(text)
    fence: str | None = None

    def flush() -> None:
        if current is not None and current not in out:
            body = "\n".join(buf)
            out[current] = {"lines": list(buf), "lineno": start,
                            "filled": has_real_content(body), "body": body}

    for lineno, line in enumerate(lines):
        fm = FENCE_RE.match(line)
        if fm:
            mark = fm.group(1)
            if fence is None:
                fence = mark[0] * len(mark)
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
            if current is not None:
                buf.append(line)
            continue
        if fence is not None:
            if current is not None:
                buf.append(line)
            continue
        m = HEADING_RE.match(line)
        if m and len(m.group(1)) == 2:
            flush()
            current = m.group(2).strip()
            buf = []
            start = lineno
            continue
        if current is not None:
            buf.append(line)
    flush()
    return out


def section_spans(text: str) -> list[tuple[str, int, int]]:
    """Every level-2 block of a document as `(heading, first line, one past the last)`, in order
    and REPEATS INCLUDED — `parse_sections` keys by heading and keeps the first, so a repeated
    heading is invisible to it. Same fence rule."""
    spans: list[list] = []
    fence: str | None = None
    lines = split_lines(text)
    for lineno, line in enumerate(lines):
        fm = FENCE_RE.match(line)
        if fm:
            mark = fm.group(1)
            if fence is None:
                fence = mark[0] * len(mark)
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        m = HEADING_RE.match(line)
        if m and len(m.group(1)) == 2:
            if spans:
                spans[-1][2] = lineno
            spans.append([m.group(2).strip(), lineno, len(lines)])
    return [(h, a, b) for h, a, b in spans]


def duplicate_headings(text: str, schema: dict | None = None) -> list[str]:
    """The canonical headings a document carries MORE THAN ONCE (first-seen order)."""
    canon = set(canonical_headings(schema))
    seen: set[str] = set()
    dup: list[str] = []
    for h, _, _ in section_spans(text):
        if h in canon and h in seen and h not in dup:
            dup.append(h)
        seen.add(h)
    return dup


def section_state(sections: dict, heading: str) -> str:
    """`absent` · `empty` (present but malformed) · `filled`."""
    if heading not in sections:
        return "absent"
    return "filled" if sections[heading]["filled"] else "empty"


def stray_headings(sections: dict, schema: dict | None = None) -> list[str]:
    canon = set(canonical_headings(schema))
    # Old provider documents may still carry `Overview`. Keep them readable while
    # removing the heading from the active contract and every derived projection.
    return [h for h in sections if h not in canon and h != "Overview"]


def parse_impact_standards(text: str, schema: dict | None = None) -> list[str]:
    """The `/docs/standards/**.md` paths a spec DECLARES it will write, read from the one
    fixed sub-heading of `## Impact`.

    Only that sub-heading is parsed, and deliberately so. Its siblings name paths the spec
    does NOT promise to write — a background standard it *may* resolve, the product code it
    touches — and parsing those would flag a spec for not writing a doc it never claimed. A
    spec with no such sub-heading declares nothing and is never flagged: the check is
    opt-in by writing the heading."""
    s = schema or load_schema()
    imp = s.get("impact", {})
    anchors = [imp.get("parsedSubheading", "")] + list(imp.get("acceptedAliases", []))
    anchor_re = re.compile(
        r"^\s*(?:#{3,6}\s+|\*\*)\s*(?:" +
        "|".join(re.escape(a.rstrip("/ ")) for a in anchors if a) + r")",
        re.IGNORECASE)
    sections = parse_sections(strip_comments(text))
    if "Impact" not in sections:
        return []
    out: list[str] = []
    seen: set[str] = set()
    collecting = False
    for line in sections["Impact"]["lines"]:
        if anchor_re.match(line):
            collecting = True
            continue
        if SUBHEADING_RE.match(line):     # any other sub-heading closes the parsed zone
            collecting = False
            continue
        if not collecting or not BULLET_RE.match(line):
            continue
        # An unfilled `<placeholder>` declares nothing — the same rule has_real_content
        # uses. Without it the shipped template's own example bullet would make every
        # freshly captured spec report sp-impact-uncovered against a path nobody wrote.
        for m in STANDARD_PATH_RE.finditer(PLACEHOLDER_RE.sub("", line)):
            if m.group(0) not in seen:
                seen.add(m.group(0))
                out.append(m.group(0))
    return out


def _gate_over(sections: dict, name: str, entry: list, warn_when_empty: list) -> dict:
    """Which of `entry` is absent or present-but-empty. `- none — <reason>` counts as
    filled and never appears here."""
    missing, malformed = [], []
    for h in entry:
        st = section_state(sections, h)
        if st == "absent":
            missing.append(h)
        elif st == "empty":
            malformed.append(h)
    warn = [h for h in warn_when_empty if section_state(sections, h) != "filled"]
    return {"phase": name, "missing": missing, "malformed": malformed,
            "warn": warn, "ok": not missing and not malformed}


def gate_report(info: dict, phase: str, schema: dict | None = None) -> dict:
    """What stands between this spec and `phase` — the entry gate of a real folder."""
    ph = phase_spec(phase, schema)
    return _gate_over(info["sections"], phase,
                      ph.get("entryGate", []), ph.get("warnWhenEmpty", []))


def ready_gate(schema: dict | None = None) -> dict:
    """The ready set, read from the ONE stage rule marked `gate: true`.

    v2 held these ten sections in the `ready/` phase's `entryGate`; v3 has no `ready/`
    folder, so the same set lives on the derived stage instead. It is read from the schema
    rather than restated here for the same reason it is not restated in schema.json: two
    declared sources of one fact diverge."""
    s = schema or load_schema()
    for r in s.get("stages", {}).get("derived", []):
        if r.get("gate"):
            return {"sections": list(r.get("when", {}).get("filled", [])),
                    "warnWhenEmpty": list(r.get("warnWhenEmpty", []))}
    return {"sections": [], "warnWhenEmpty": []}


def ready_report(info: dict, schema: dict | None = None) -> dict:
    """The ready gate applied to one spec. A FLOOR, not a verdict: nothing refuses on it
    any more — `execute` reports it and asks for the `approved` stamp inline."""
    g = ready_gate(schema)
    return _gate_over(info["sections"], "ready", g["sections"], g["warnWhenEmpty"])
