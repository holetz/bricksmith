"""The section rule over FREE markdown — every heading, the body it owns, and the rule half.

Moved verbatim out of the pre-refactor components script.

`cq specs section` reads a SPEC, whose thirteen headings are a validated contract. A reference or
a standard is free markdown, so the two cannot share an implementation — but they must not
disagree about what a section IS. Per `/docs/standards/code/canonical-set-parsing.md`, what is
shared is the RULE, proved by both tools against the same canonical case list.
"""
from __future__ import annotations

import re

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
MD_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")

RULES_MARKER = "<!-- rules -->"
RATIONALE_MARKER = "<!-- rationale -->"


def _strip_frontmatter(lines: list[str]) -> list[str]:
    """A leading `---` block is a header, never content. ~1,424 chars per OKF doc that
    a section reader has no reason to carry."""
    if not lines or lines[0].strip() != "---":
        return lines
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[i + 1:]
    return lines


def markdown_sections(text: str) -> list[dict]:
    """Every heading of a markdown file, in order, each with the body it owns.

    Two rules, and both are the reason this is code rather than an `awk` in 25 command
    bodies:

    - **A section ends at the next heading of the same level or shallower.** So
      sub-headings travel with their parent, exactly as `## Impact` keeps its parsed
      `### Standards …` sub-heading in `cq specs`. Ending at the next heading of ANY
      level would orphan them.
    - **A fenced block is never read as a heading.** Several sections here open with
      ```` ```bash ```` blocks containing `## ` comments, and a line-matching reader
      slices the section in half at one — silently, returning a plausible answer.
    """
    lines = _strip_frontmatter(text.splitlines())
    heads: list[dict] = []
    fence: str | None = None
    for lineno, line in enumerate(lines):
        m = FENCE_RE.match(line)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark[0] * len(mark)
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        h = MD_HEADING_RE.match(line)
        if h:
            heads.append({"level": len(h.group(1)), "heading": h.group(2).strip(),
                          "lineno": lineno})
    for i, h in enumerate(heads):
        end = len(lines)
        for nxt in heads[i + 1:]:
            if nxt["level"] <= h["level"]:
                end = nxt["lineno"]
                break
        h["body"] = "\n".join(lines[h["lineno"] + 1:end]).strip("\n")
    return heads


def split_rule_and_rationale(body: str) -> tuple[str, bool]:
    """The rule half of a section, and whether a marker actually said where it ends.

    **Marker, never heuristic.** A model deciding per read which sentences are binding and
    which are the story behind them is non-deterministic, and its failure is silent: a
    dropped binding sentence shows up nowhere. The marker is written once, by whoever wrote
    the rule, and the read is mechanical.

    **No marker → the whole section, and the caller is TOLD.** The degradation is to
    today's behaviour, never to emptiness. A convention applied in five files must not turn
    the other eighteen into silence.

    **A marker's reach ends at the next heading.** Asked for a section, a caller gets its
    sub-sections with it — so a single `<!-- rationale -->` inside one `###` would otherwise
    truncate every rule after it, including whole sub-sections that carry no marker at all.
    Measured on `execution.md` §Delegating an executor: three normative `###` blocks
    disappeared behind one sub-section's rationale.
    """
    keep, marked, out = True, False, []
    fence: str | None = None
    for line in body.splitlines():
        m = FENCE_RE.match(line)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark[0] * len(mark)
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
        elif fence is None:
            if line.strip() == RULES_MARKER:
                keep, marked = True, True
                continue
            if line.strip() == RATIONALE_MARKER:
                keep = False
                continue
            if MD_HEADING_RE.match(line):
                if not keep and out and out[-1].strip():
                    out.append("")   # dropped rationale must not weld a heading to prose
                keep = True
        if keep:
            out.append(line)
    return "\n".join(out).strip("\n"), marked


def normalize_heading(name: str) -> str:
    """What a caller types against what the file carries. `§The commit`, `## The commit`
    and `the commit` are the same request — the citation form the bodies already use
    carries the `§`, and refusing over it would make the reader unusable from the very
    prose it exists to serve."""
    return " ".join(name.strip().lstrip("#§").strip().split()).casefold()


def select_sections(heads: list[dict], wanted: list[str]) -> tuple[list[dict], list[str]]:
    """The requested sections in the order they were ASKED FOR, plus the names that
    resolved to nothing. Duplicate headings resolve to the first — the same rule
    `parse_sections` applies in `cq specs`.

    **An exact name wins; failing that, a UNIQUE prefix resolves.** Free-markdown headings
    are long and full of punctuation — `## The commit — one per task, carrying its own
    ticked box` — and a caller citing `§The commit`, exactly as the command bodies do, must
    not have to reproduce an em-dash and a comma to be understood. A prefix matching two
    headings resolves to neither: ambiguity is a refusal, never a guess."""
    index: dict[str, dict] = {}
    for h in heads:
        index.setdefault(normalize_heading(h["heading"]), h)
    got, missing = [], []
    for name in wanted:
        key = normalize_heading(name)
        h = index.get(key)
        if h is None:
            hits = [v for k, v in index.items() if k.startswith(key)]
            h = hits[0] if len(hits) == 1 else None
        (got.append(h) if h else missing.append(name))
    return got, missing


def expand_section_args(heads: list[dict], raw: list[str]) -> list[str]:
    """The names a `--sections` value asks for. **A value is first an ADDRESS, and only
    then a list.**

    A heading may carry a comma of its own — `## What crosses, what stays` — so splitting
    every value unconditionally leaves those headings unciteable by their own title, which
    is the form the command bodies write. Each value is resolved whole first, by the same
    ladder `select_sections` applies; only one that resolves to nothing AND carries a comma
    is read as a list.

    Whole-first is what makes the rule deterministic — a value that is both a heading and a
    well-formed list has one reading, and it is the one that was cited — and it is why step
    one is the whole ladder rather than exact-match alone: a comma-free value resolving by
    prefix passes through untouched, so every prefix citation already written keeps working
    by construction. Measured over 14,944 simulated list values across this repo's markdown,
    none resolves whole, so the precedence costs the short form nothing."""
    wanted: list[str] = []
    for value in (v.strip() for v in raw):
        if not value:
            continue
        got, _ = select_sections(heads, [value])
        if got or "," not in value:
            # unresolved and comma-free travels on, so `cmd_read` refuses by name
            wanted.append(value)
        else:
            wanted.extend(p for p in (s.strip() for s in value.split(",")) if p)
    return wanted
