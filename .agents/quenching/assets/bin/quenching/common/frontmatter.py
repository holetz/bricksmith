"""The one frontmatter parser — the union of the three that preceded it.

`/docs/standards/code/frontmatter-parser.md` owns the comment rule, the canonical case list and
the anomaly set. Until this module it also owned a lockstep obligation — EDIT ALL THREE, OR NONE —
resting on a premise stated in the pre-refactor specs script: each script installed standalone
into a target repo's `.claude/hooks/`, "so none may import the others". Nothing installs any
more, so the premise is gone and the three copies collapse into this one.

NORMALIZING UPWARD, NOT PICKING A WINNER
----------------------------------------
The three never read the same YAML subset, and no two read the same one:

    form                          specs   skills   okf
    key: scalar, quotes, comment    yes     yes     yes
    inline list  [a, b]             yes     yes     no  — kept the literal "[a, b]"
    block list   - item             yes     yes     no  — read as ""
    block record (indented k: v)    yes     no      no
    flow map     {a: b}             yes     no      no  — kept the literal "{a: b}"
    block scalar | / >              no      yes     no  — kept the bare indicator

Nobody read the union: `specs` read records and flow maps but no block scalars, `skills` the
reverse. This parser reads every row, so the `knowledge` pillar (the pre-refactor OKF validator
script) gains the four forms it used to flatten and `components` (the pre-refactor components
script) gains records and flow maps.

WHAT THAT COSTS, DELIBERATELY
-----------------------------
`frontmatter_anomalies` had three divergent bodies, each a carve-out for what its own parser could
not read: `okf` reported every indented run because it read none of them, `skills` exempted block
lists and `hooks`, `specs` tested its own parse result. Only the last criterion survives a parser
that reads everything — **an anomaly is a key the parser could not turn into a value** — so
`knowledge` and `components` now report FEWER anomalies than they did. Nothing became invisible:
they stopped denouncing forms they can now read.
"""

import re

# The block-scalar header. Matched before the comment rule is applied, because a header carries no
# value for a `#` to trail.
BLOCK_SCALAR_RE = re.compile(r"^([|>])(?:[+-]?)(?:\d*)\s*$")
FRONTMATTER_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(.*)$")
KNOWN_FRONTMATTER_KEYS = {
    "name", "description", "argument-hint", "allowed-tools", "disallowed-tools",
    "user-invocable", "disable-model-invocation", "effort", "context", "agent",
    "background", "paths", "hooks",
}

# `hooks:` is read by `parse_frontmatter_hooks`, which is the `components` pillar's own reader for
# a nested block no generic top-level parser resolves. Whatever this parser makes of that block —
# today a flattened record — is not evidence either way, so the key is not this sidecar's to judge.
# It is a module constant and not a parameter: the exemption is a property of the dialect (a key
# with a dedicated reader in this same package), not a caller's policy. Handing each caller its own
# exemption set is how the three copies diverged in the first place.
ANOMALY_EXEMPT_KEYS = {"hooks"}


def _frontmatter_body(text: str) -> list[str] | None:
    """The lines between the leading `---` fences; `None` when there is no closed block."""
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    if lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i]
    return None


def _unquote(val: str) -> str:
    """Strip ONE matching pair of surrounding quotes, undoing the only escapes a writer needs.

    `''` inside single quotes and a backslash before `"` or `\\` inside double quotes; any other
    backslash stays, so a Windows path in double quotes is read as written.

    A lone opening quote is kept verbatim — the same answer the canonical `unterminated-quote` row
    demands from the scalar path, which is why every value in this module unquotes through here
    rather than through a `strip("'\\"")` that would eat it."""
    val = val.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        inner = val[1:-1]
        if val[0] == "'":
            return inner.replace("''", "'")
        return re.sub(r'\\(["\\])', r"\1", inner)
    return val


def _indented_run(body: list[str], start: int) -> tuple[list[str], int]:
    """The blank-or-indented lines beginning at `start` with trailing blanks dropped, and the index
    of the first line that is neither — i.e. everything belonging to the value above, and where the
    next top-level key begins."""
    run: list[str] = []
    j = start
    while j < len(body) and (not body[j].strip() or body[j][:1] in (" ", "\t")):
        run.append(body[j])
        j += 1
    while run and not run[-1].strip():
        run.pop()
    return run, j


def _quote_end(val: str) -> int | None:
    """Index just past the closing quote of a quoted scalar; `0` when the value is not quoted,
    `None` when the quote never closes.

    A quote opens a scalar only in the first position — mid-value it is an ordinary character,
    which is why `at the first '#'` is a plain scalar and its `#` is not inside quotes."""
    if not val or val[0] not in ("'", '"'):
        return 0
    quote, i = val[0], 1
    while i < len(val):
        if quote == '"' and val[i] == "\\":
            i += 2
            continue
        if val[i] == quote:
            if quote == "'" and val[i + 1:i + 2] == "'":   # YAML doubles a literal '
                i += 2
                continue
            return i + 1
        i += 1
    return None


def _list_elements(inner: str) -> list[str]:
    """The elements of an inline list's inside, split on the commas outside quotes. A quote
    opens only at an element's first position, as in `_quote_end`; an unterminated one keeps the
    rest of the text as one element."""
    out: list[str] = []
    rest = inner
    while True:
        lead = len(rest) - len(rest.lstrip())
        end = _quote_end(rest[lead:])
        scan_from = lead + end if end else lead
        if end is None:
            out.append(rest)
            return out
        comma = rest.find(",", scan_from)
        if comma < 0:
            out.append(rest)
            return out
        out.append(rest[:comma])
        rest = rest[comma + 1:]


def _split_list_comment(val: str) -> tuple[str, str]:
    """`_split_comment` for a value opening `[`: the quote rule applies to each element, so a
    ` #` inside `"sprint #3"` stays in the value."""
    pos = 1
    while pos <= len(val):
        lead = pos
        while lead < len(val) and val[lead] in " \t":
            lead += 1
        end = _quote_end(val[lead:])
        if end is None:
            return val, ""
        i = lead + end
        while i < len(val) and val[i] not in ",]":
            if val[i] == "#" and val[i - 1] in " \t":
                return val[:i].rstrip(), val[i:]
            i += 1
        if i >= len(val) or val[i] == "]":
            for j in range(i + 1, len(val)):
                if val[j] == "#" and val[j - 1] in " \t":
                    return val[:j].rstrip(), val[j:]
            return val, ""
        pos = i + 1
    return val, ""


def _split_comment(val: str) -> tuple[str, str]:
    """Split a scalar into (value, comment).

    A `#` opens a comment only when it is the first character of the value or is preceded by
    whitespace, and never inside a quoted scalar. The `val.split("#")[0]` this replaced honoured
    none of the three, so it cut every value at its first `#` — including this workspace's own spec
    titles, in every `status --json` call.

    An unterminated quote strips nothing: the scalar's extent is unknowable, so the value is kept
    verbatim and `frontmatter_anomalies` reports it rather than the parser guessing a boundary."""
    if val.startswith("["):
        return _split_list_comment(val)
    start = _quote_end(val)
    if start is None:
        return val, ""
    for i in range(start, len(val)):
        if val[i] == "#" and (i == 0 or val[i - 1] in " \t"):
            return val[:i].rstrip(), val[i:]
    return val, ""


def _read_block_scalar(body: list[str], start: int, style: str) -> tuple[str, int]:
    """Consume the indented run at `start` and return (value, index after it).

    `>` folds: consecutive non-empty lines join with a single space and a blank line becomes a
    newline — which is what Claude Code ultimately reads, so a character count taken here is the
    count the model pays for. `|` keeps the newlines."""
    raw, j = _indented_run(body, start)
    if not raw:
        return "", j
    indent = min(len(ln) - len(ln.lstrip()) for ln in raw if ln.strip())
    stripped = [ln[indent:] if len(ln) >= indent else ln.lstrip() for ln in raw]
    if style == "|":
        return "\n".join(stripped), j
    out: list[str] = []
    for ln in stripped:
        if not ln.strip():
            out.append("\n")
        elif out and out[-1] != "\n":
            out.append(" " + ln.rstrip())
        else:
            out.append(ln.rstrip())
    return "".join(out).strip(), j


def _read_block_list(body: list[str], start: int) -> tuple[list[str], int]:
    """The `- item` run beginning at `start`, and the index after it."""
    items: list[str] = []
    j = start
    while j < len(body) and body[j][:1] in (" ", "\t") and body[j].lstrip().startswith("- "):
        items.append(_unquote(body[j].lstrip()[2:]))
        j += 1
    return items, j


def _read_block_record(body: list[str], start: int) -> tuple[dict, int]:
    """The indented `k: v` mapping beginning at `start`, and the index after it.

    Indent decides, exactly as YAML does: a line deeper than the record's own keys continues the
    value above it rather than starting a new one, which is what lets a spec's explicit-none reason
    run past a single line."""
    rec: dict = {}
    last = None
    base_indent = None
    j = start
    while j < len(body) and body[j][:1] in (" ", "\t") and body[j].strip():
        indent = len(body[j]) - len(body[j].lstrip())
        cur = body[j].strip()
        if base_indent is None:
            base_indent = indent
        if indent > base_indent and last is not None:
            rec[last] = f"{rec[last]} {cur}".strip()
        elif indent == base_indent and ":" in cur:
            k2, _, v2 = cur.partition(":")
            last = k2.strip()
            rec[last] = _unquote(v2)
        else:
            break
        j += 1
    return rec, j


def parse_frontmatter(text: str) -> dict:
    """Top-level `key: value` pairs of a leading `---` block; empty dict when there is no block.

    Scalars come back as strings with surrounding quotes stripped and trailing comments removed;
    `[a, b]` and `- item` as lists; `{a: b}` and an indented `k: v` run as dicts; `|` and `>` as
    the folded or literal text beneath them. Nested structure below the first level is flattened —
    naming what that loses is `frontmatter_anomalies`' job, not this one's."""
    body = _frontmatter_body(text)
    if body is None:
        return {}
    fm: dict = {}
    j = 0
    while j < len(body):
        raw = body[j]
        if not raw.strip() or raw.lstrip().startswith("#") or raw[:1] in (" ", "\t"):
            j += 1
            continue
        if ":" not in raw:
            j += 1
            continue
        key, _, val = raw.partition(":")
        key = key.strip()
        val = val.strip()
        block = BLOCK_SCALAR_RE.match(val)
        if block:
            fm[key], j = _read_block_scalar(body, j + 1, block.group(1))
            continue
        val = _split_comment(val)[0]
        while (val.endswith(",") and j + 1 < len(body) and body[j + 1][:1] in (" ", "\t")
               and body[j + 1].strip()):
            # A comma-separated scalar continued on the indented line below it.
            j += 1
            val = f"{val} {_split_comment(body[j].strip())[0]}"
        if val == "":
            # A block list first: `- name: x` is a list item, not a record key, and testing the
            # record form first would read one as the other.
            items, k = _read_block_list(body, j + 1)
            if items:
                fm[key] = items
                j = k
                continue
            rec, k = _read_block_record(body, j + 1)
            if rec:
                fm[key] = rec
                j = k
                continue
            fm[key] = ""
            j += 1
            continue
        if val.startswith("[") and val.endswith("]"):
            fm[key] = [_unquote(x) for x in _list_elements(val[1:-1]) if x.strip()]
        elif val.startswith("{") and val.endswith("}"):
            # Flow splits on commas, so a record value that contains one — or is long enough to
            # wrap — can only be written as the indented form above.
            rec = {}
            for part in val[1:-1].split(","):
                if ":" in part:
                    k2, _, v2 = part.partition(":")
                    rec[k2.strip()] = _unquote(v2)
            fm[key] = rec
        else:
            fm[key] = _unquote(val)
        j += 1
    return fm


def frontmatter_block(text: str) -> tuple[bool, bool]:
    """Return (has_block, well_formed) — whether the file opens a `---` fence, and whether that
    fence ever closes.

    A SIDECAR, for the same reason `frontmatter_anomalies` is one: only the `knowledge` pillar acts
    on these two bits, and folding them back into a `(fm, has_block, well_formed)` tuple — which is
    what the pre-refactor OKF validator script returned — would put a three-way unpack at every call site in three
    pillars to carry a signal one of them reads.

    An unclosed fence is the case the parse cannot express on its own: `parse_frontmatter` returns
    an empty dict for it exactly as it does for a file with no frontmatter at all, and only this
    function separates the two."""
    if not text.startswith("---"):
        return False, True
    lines = text.splitlines()
    if lines[0].strip() != "---":
        return False, True
    return True, any(ln.strip() == "---" for ln in lines[1:])


def frontmatter_anomalies(text: str) -> list[dict]:
    """What the frontmatter parse could not represent faithfully.

    A SIDECAR: `parse_frontmatter` keeps returning a bare dict, so none of its call sites change —
    `status`, `next` and `triage` never have to decide mid-cycle what an un-understood frontmatter
    means, which would be a behaviour change in commands that today refuse nothing.

    Every entry is a suspicion the tool cannot resolve, never a proven violation: a stripped comment
    and lost prose are byte-identical, and nothing here guarantees Claude Code's own loader resolves
    a duplicate key the way this one does. Callers surface them at WARN — see
    `/docs/standards/quality/parse-honesty.md`."""
    body = _frontmatter_body(text)
    if body is None:
        return []
    fm = parse_frontmatter(text)
    out: list[dict] = []
    seen: set[str] = set()
    j = 0
    while j < len(body):
        raw = body[j]
        if (not raw.strip() or raw.lstrip().startswith("#")
                or raw[:1] in (" ", "\t") or ":" not in raw):
            j += 1
            continue
        key, _, val = raw.partition(":")
        key, val = key.strip(), val.strip()
        run, k = _indented_run(body, j + 1)

        if key in seen:
            out.append(_anomaly(key, "duplicate-key",
                                f"`{key}` is set more than once and the last one silently wins; "
                                "nothing guarantees another parser resolves it the same way"))
        seen.add(key)

        block = BLOCK_SCALAR_RE.match(val)
        if block:
            for offset, continuation in enumerate(run):
                swallowed = FRONTMATTER_KEY_RE.match(continuation.strip())
                if not swallowed or swallowed.group(1) not in KNOWN_FRONTMATTER_KEYS:
                    continue
                swallowed_key = swallowed.group(1)
                out.append(_anomaly(
                    swallowed_key, "swallowed-key",
                    f"`{swallowed_key}` is indented under folded `{key}` and was absorbed into "
                    "its value; align it with the top-level frontmatter keys",
                    line=j + 3 + offset, parent=key))
            j = k
            continue

        if not block:     # a block-scalar header takes no comment
            value, comment = _split_comment(val)
            if comment:
                out.append(_anomaly(key, "comment-stripped",
                                    f"`{comment}` was read as a comment and removed from `{key}` — "
                                    "a stripped comment and lost prose are byte-identical"))
            elif _quote_end(val) is None:
                out.append(_anomaly(key, "unterminated-quote",
                                    f"`{key}` opens a quote that never closes, so the scalar's "
                                    "extent is unknowable; nothing was stripped"))
            # The parse result is the test, not a re-derivation of it: every indented form this
            # parser reads comes back truthy, so what is left is exactly what it read as nothing.
            if value == "" and run and key not in ANOMALY_EXEMPT_KEYS and not fm.get(key):
                out.append(_anomaly(key, "indented-continuation",
                                    f"`{key}` has no inline value and the lines beneath it are in "
                                    "no form this parser reads — it was read as empty"))
        j = k
    return out


def _anomaly(key: str, kind: str, detail: str, **extra) -> dict:
    anomaly = {"key": key, "kind": kind, "detail": detail}
    anomaly.update(extra)
    return anomaly
