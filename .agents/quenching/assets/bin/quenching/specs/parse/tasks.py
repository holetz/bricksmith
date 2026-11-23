"""The `## Tasks` grammar — the checkbox line, its indented metadata, and the block
each task owns.

Moved verbatim out of the pre-refactor specs script."""
from __future__ import annotations

import re

from quenching.specs.parse.refs import member_key
from quenching.specs.parse.sections import parse_sections
from quenching.specs.parse.text import HEADING_RE, mask_comments


# `- [ ]` / `- [x]` / `- [!]` — the third is a BLOCKED task, visible and human-legible,
# which is what replaced v1's hidden five-attempt counter in `.specs.json`.
CHECKBOX_RE = re.compile(r"^(\s*)-\s\[( |x|X|!)\]\s+(.*)$")
# THE ONE ITEM GRAMMAR. A checkbox whose text carries `— spec: #<id>` (or
# `— spec: <owner>/<repo>#<id>`) is a SPEC ITEM: it names another document instead of
# describing work to do here. Everything else is a TASK item. `after:` is read on both:
# on a task it orders work inside this document, on a spec item it orders the member.
SPEC_REF_RE = re.compile(r"(?:^|\s)—\s*spec\s*:\s*#?([^\s—]+)", re.IGNORECASE)
AFTER_RE = re.compile(r"(?:^|\s)—\s*after\s*:\s*([^—]*?)\s*(?=—|$)", re.IGNORECASE)
# Everything from the first reserved tail (`— spec:`, `— after:`, `— blocked:`,
# `— descoped:`) on, which is metadata rather than the item's title.
TAIL_RE = re.compile(r"\s—\s*(?:spec|after|blocked|descoped)\s*:.*$", re.IGNORECASE)
CHECKBOX_LOOSE_RE = re.compile(r"^\s*-\s*\[.*?\]")   # looks like a checkbox (malformed detection)
TASK_ID_RE = re.compile(r"^(\d+(?:\.\d+)*)\b")
TASK_META_KEYS = ("files", "pattern", "cwd", "verify", "constraint", "subject", "commit")
TASK_META_RE = re.compile(rf"^\s+(-\s+)?({'|'.join(TASK_META_KEYS)})\s*:\s*(.+?)\s*$",
                          re.IGNORECASE)
# An optional `- ` bullet before the key is the same line: a `  - files:` that the parser
# dropped turned the verifier's scope check into a silent `n/a`. The bullet form is the
# lowercase key only and never overrides a bare line of the same key: `  - Verify: by hand`
# is prose, and must not replace the real `verify:`.
# `constraint:` is INERT by design: the grammar admits it and `next` hands it through, but no
# command reads it. Its only consumer would be an executor sub-agent briefing itself, and the
# decision to dispatch one belongs to another spec — so the field lands first and the decision
# stays untouched. A field nobody reads costs one alternation and moves no turn.
#
# The anchor is written into a one-line grammar and read back, so the only hard requirement
# is that it holds text and stays on its line. `commit:` is both the legacy form of the same
# field — still READ from specs written before the anchor became the subject — AND, since
# `task --commit`, a form written again on purpose: a real git sha, upserted by the CLI once
# the commit that implements the task already exists. `subject:` stays the default `--check`
# writes; `commit:` is opt-in via the new flag. See `task --commit`'s help and `## Design`
# ("Decisão: o anchor task→commit é o sha") for why both anchors coexist for now.
SUBJECT_RE = re.compile(r"^[^\r\n]+$")
# A git object id, short or full — hex only. Loose on purpose (git accepts abbreviations down
# to a repo-dependent minimum well below 7), but tight enough to catch an obvious mistake like
# passing a commit MESSAGE where a sha was meant.
COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
DEFAULT_META_INDENT = "      "
PARALLEL_RE = re.compile(r"^\[P\](?:\s|$)")
BLOCKED_REASON_RE = re.compile(r"—\s*blocked\s*:\s*(.+?)\s*$", re.IGNORECASE)


def _split_files(val: str) -> list[str]:
    """Comma-split that respects parentheses: a comma inside `(…)` belongs to the same
    entry, so a comment like `a.md (descartável, revertido ao fim)` reads as ONE path
    instead of two invented ones the executor could not tell from real paths. Depth is
    tracked, never counted: an unbalanced `(` simply keeps the rest of the line one
    entry. Stdlib-only."""
    out: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(val):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            piece = val[start:i].strip()
            if piece:
                out.append(piece)
            start = i + 1
    piece = val[start:].strip()
    if piece:
        out.append(piece)
    return out


FILES_ANNOTATION_RE = re.compile(r"\s*\(([^()]*)\)\s*$")

# THE RESERVED `files:` ANNOTATIONS — a closed grammar owned HERE and nowhere else, so no
# consumer re-derives the set. `new` marks a path about to be created, which `repo_path`
# (`quenching.common.paths`) strips for comparisons. `operational` declares a task that writes no repository file at
# all: its proof is the `verify:`. Read the pair through `is_operational` and
# `operational_entries` rather than spelling either name again.
FILES_ANNOTATIONS = ("new", "operational")
OPERATIONAL_ANNOTATION = "operational"


def _annotation(entry: str) -> str | None:
    """The trailing parenthetical of one entry, trimmed; `None` when the entry carries
    none. The reserved names are matched exactly — the grammar is closed, not a wording."""
    m = FILES_ANNOTATION_RE.search(entry)
    return m.group(1).strip() if m else None


def _files_bad_annotation(entry: str) -> str | None:
    """Any trailing parenthetical that is NOT reserved is a human comment; kept inside
    the entry, the executor receives a path that exists nowhere with the same confidence
    as a real one — the silent failure this rule closes. Return the annotation text so
    the caller can refuse the entry."""
    note = _annotation(entry)
    return None if note in FILES_ANNOTATIONS else note


def operational_entries(files: list[str]) -> list[str]:
    """Every entry that carries `(operational)` — non-empty beside a path means a MIXED
    declaration, which `validate` refuses (`sp-files-operational`)."""
    return [e for e in files if _annotation(e) == OPERATIONAL_ANNOTATION]


def is_operational(files: list[str]) -> bool:
    """A task that writes no repository file: `(operational)` IS its whole `files:`.

    Exclusive on purpose. Beside a path the marker would hide that path from the
    verifier's scope union — the one guard that makes the annotation safe — so the
    declaration is either the annotation alone or plain paths."""
    return len(files) == 1 and bool(operational_entries(files))


def parse_tasks(text: str) -> list[dict]:
    """Every checkbox under `## Tasks`, in order: index, explicit id, state, text, line
    number, the `[P]` marker, the optional indented metadata (`files`/`pattern`/`verify`),
    and — for a blocked task — the reason written right in the line.

    Each row also carries `kind`: `task` for a checkbox that describes work, `link` for one
    that names another spec (`— spec: #<id>`). A link carries its `label` (`S1`), its clean
    `title`, the member key in `spec` and its `after:` labels; both kinds carry `after`.

    `state` is `" "` (open), `"x"` (done) or `"!"` (blocked). The blocked marker is
    deliberately IN THE FILE rather than in sidecar state: it is what a human reads when
    they come to unblock it, and v1's hidden attempt counter was read by nobody."""
    sections = parse_sections(mask_comments(text))
    if "Tasks" not in sections:
        return []
    base = sections["Tasks"]["lineno"] + 1
    lines = sections["Tasks"]["lines"]
    out = []
    idx = 0
    section = 0
    for i, line in enumerate(lines):
        hm = HEADING_RE.match(line)
        if hm and len(hm.group(1)) == 3:
            section += 1          # a `### N.` heading starts a new task section
            continue
        m = CHECKBOX_RE.match(line)
        if not m:
            continue
        idx += 1
        state = m.group(2).lower()
        body = m.group(3).strip()
        idm = TASK_ID_RE.match(body)
        rest = body[idm.end():].lstrip() if idm else body
        parallel = bool(PARALLEL_RE.match(rest))
        blocked = BLOCKED_REASON_RE.search(body)
        spec_m = SPEC_REF_RE.search(body)
        kind = "link" if spec_m else "task"
        label = title = None
        spec = None
        after: list[str] = []
        if kind == "link":
            head = TAIL_RE.sub("", body).strip()
            label, _, title = head.partition(" ")
            spec = member_key(spec_m.group(1))
        am = AFTER_RE.search(body)
        if am:
            after = [a.strip() for a in am.group(1).split(",") if a.strip()]
        files: list[str] = []
        pattern = cwd = verify = subject = commit = None
        subject_off = commit_off = last_meta_off = None
        meta_indent = None
        bare_keys: set[str] = set()
        block_end = i + 1
        for off, cont in enumerate(lines[i + 1:], start=i + 1):
            # the task's block ends at a blank line, a non-indented line, or another
            # checkbox; anything else indented is scanned, so a wrapped prose line between
            # the checkbox and its `verify:` does not hide it.
            if not cont.strip() or cont[:1] not in (" ", "\t") or CHECKBOX_RE.match(cont):
                break
            block_end = off + 1
            mm = TASK_META_RE.match(cont)
            if not mm:
                continue
            bullet, raw_key = mm.group(1), mm.group(2)
            key, val = raw_key.lower(), mm.group(3).strip()
            if bullet and (raw_key != key or key in bare_keys):
                continue
            if not bullet:
                bare_keys.add(key)
            if meta_indent is None:
                meta_indent = cont[:len(cont) - len(cont.lstrip())]
            last_meta_off = off
            if key == "files":
                files = _split_files(val)
            elif key == "pattern":
                pattern = val
            elif key == "cwd":
                cwd = val
            elif key == "subject":
                subject, subject_off = val, off
            elif key == "commit":
                commit, commit_off = val, off
            elif key == "verify":
                verify = val
            # `constraint:` is admitted by the grammar and read by nothing — the inert field.
            # This arm is why the dispatch is exhaustive rather than an `else: verify = val`:
            # under the open form, a `constraint:` line following `verify:` overwrote it, and
            # the loop would have run that prose as the task's shell command.
        operational = is_operational(files)
        out.append({
            "index": idx,
            "id": idm.group(1) if idm else None,
            "state": state,
            "checked": state == "x",
            "blocked": state == "!",
            "reason": blocked.group(1) if blocked else None,
            "text": body,
            # THE SPLIT THE REST OF THE TOOL READS. `kind` is `link` for an item that names
            # another spec and `task` for one that describes work; `derive_info` puts the
            # first in `info["links"]` and the second in `info["tasks"]`, so a composite's
            # progress can count both without any caller re-parsing the line.
            "kind": kind,
            "label": label,
            "title": title,
            "spec": spec,
            "after": after,
            "lineno": base + i,
            "section": section,
            "parallel": parallel,
            # THE DECLARATION IS NORMALISED HERE, ONCE. An operational task declares no
            # repository file, so every consumer receives `files: []` and the flag beside
            # it — the `files:` line keeps its grammar, the chain "no files: → tick with no
            # commit" keeps its single path, and no reader has to know the annotation.
            "files": [] if operational else files,
            "operational": operational,
            "pattern": pattern,
            "cwd": cwd,
            "verify": verify,
            "subject": subject,
            "commit": commit,
            # ONE PAST the task's own last line — checkbox plus every indented metadata
            # line under it, exactly the span `block_end` already tracked to find the
            # metadata. Exists so a caller can slice `lines[lineno:blockEndLineno]` and get
            # the task's literal source text, verbatim, with nothing re-rendered. The
            # `github` backend is the first reader: a task's raw block IS what a sub-issue
            # stores, so `checked`/`blocked` round-trip by re-parsing the same text through
            # THIS function again on read, rather than the backend inventing its own
            # encoding of state.
            "blockEndLineno": base + block_end,
            # where the anchor line is, and where one would go — so `task` upserts it
            # mechanically instead of the caller doing string surgery on the file. Both
            # forms are tracked because both are read AND, as of `task --commit`, both can be
            # written: `subject:` by `--check --subject`, `commit:` by `--check --commit` once
            # the real sha exists. A spec written before either form applied to it carries
            # only whichever one it has, and `--uncheck` must still be able to drop it.
            "subjectLineno": (base + subject_off) if subject_off is not None else None,
            "commitLineno": (base + commit_off) if commit_off is not None else None,
            # After the last metadata line when there is one; otherwise after the WHOLE
            # block, not under the checkbox's first physical line. A task with no `files:`
            # or `verify:` whose text wraps was being cut in half by its own `subject:`.
            "metaInsertAt": base + ((last_meta_off + 1) if last_meta_off is not None
                                    else block_end),
            "metaIndent": meta_indent or DEFAULT_META_INDENT,
        })
    return out


def task_progress(tasks: list[dict]) -> tuple[int, int, int]:
    """(checked, blocked, total)."""
    return (sum(1 for t in tasks if t["checked"]),
            sum(1 for t in tasks if t["blocked"]),
            len(tasks))
