"""The streaming parser — one pass over a transcript, and the honesty it owes.

Moved verbatim out of the pre-refactor session script, whose module docstring carried the four sections
below unchanged.

WHAT A TRANSCRIPT HOLDS
-----------------------
One JSONL file per session at `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`, one
JSON object per line. The fields this tool reads, all verified against live sessions in
this repo rather than assumed:

  type              "user" | "assistant" | "attachment" | "system" | ...
  uuid              stable per record; the dedup key
  timestamp         ISO-8601
  sessionId, cwd, gitBranch, version
  attributionSkill  THE command a turn belongs to, e.g. "quenching-specs:specs:develop"
  attributionPlugin the plugin that owns it, e.g. "quenching"
  message.content   str, or a list of blocks: text | thinking | tool_use | tool_result

THE TWO ENTRY FORMS, AND WHY ATTRIBUTION BEATS BOTH
---------------------------------------------------
A command is reached two ways, and each leaves a *different* mark:

  typed   a user turn whose content string carries a `<command-name>` block
  skill   an assistant `tool_use` block named `Skill`, whose input names the command

Detecting only the first makes every conducted stage invisible. But neither mark tells you
what the command then *did* — they are both single points, and the work is a span.

`attributionSkill` covers both: every turn a command drives carries the command's name,
including turns inside a stage a conductor invoked. The invoking `Skill` call is attributed
to the **caller** and the stage's own turns to the **callee**, so a command is found even
when its entry mark fell outside a compacted window.

WHAT ATTRIBUTION IS NOT: A SPAN
-------------------------------
It is a *most-recently-entered* pointer. It is set on entry and **never cleared on return**
— measured across all 43 Skill stages in an 86-transcript corpus: attribution returns to
the conductor **once**, never returns to any command 36 times, and jumps to a third command
6 times.

The consequence is not academic. In the session that answered this spec's own go/no-go,
`/quenching-specs develop` invoked `/specs:isolate` as a stage; isolate finished at its "Isolated."
turn, and the conductor's next five question rounds (`AskUserQuestion` calls or text decision
sheets with `### D<n>` headings) — its own spec-shape bank — are
still stamped with the stage label `specs:isolate`. Reported naively that is "the isolation stage asked
the human five questions", which is false and entirely plausible.

There is no end marker in the transcript to fix this with. Inventing one would manufacture
findings, so this tool does the other thing: it **detects and names the misread**. A command
entered as a stage whose caller never regained attribution is marked `closed: false`, and
every count on it is reported as an upper bound that may include the caller's own work. That
is `/docs/standards/quality/parse-honesty.md` applied to a pointer instead of a parser — name
the *misread*, never the *consequence*.

So the entry marks are read for *how a command was reached and with what arguments*, and
attribution for *what it plausibly cost*, with the honesty flag attached.

PARSE HONESTY
-------------
Per `/docs/standards/quality/parse-honesty.md`: this tool narrows a transcript into a
command model, so it must be able to say what it could not read. A line that fails to
parse is counted and reported as an anomaly ahead of the content, because "no commands
found" and "I could not read this file" look identical in a report and lead to opposite
actions.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from quenching.session.model import Command, normalize, read_window

COMMAND_NAME_RE = re.compile(r"<command-name>([^<]+)</command-name>")
COMMAND_ARGS_RE = re.compile(r"<command-args>([^<]*)</command-args>")

INTERRUPT = "[Request interrupted by user"
COMPACTION = "This session is being continued from a previous conversation"


def classify_user_turn(text: str) -> str | None:
    """What a human turn is evidence OF, or None when it is not evidence at all.

    An interrupt is the strongest signal a command misfired — the human stopped it
    mid-flight. An interjection is weaker but is still the human supplying something the
    command should have known. A compaction notice is neither; it is the harness talking,
    and counting it as a correction would blame the command for running long.
    """
    stripped = text.strip()
    if not stripped:
        return None
    if INTERRUPT in stripped:
        return "interrupt"
    if stripped.startswith(COMPACTION):
        return "compaction"
    if stripped.startswith("<"):        # a command invocation or a harness-injected block
        return None
    return "interjection"


def blocks(record: dict):
    """The content blocks of a record, as a list — a bare string yields none."""
    content = (record.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def close_attribution(ordered: list, timeline: list, anomalies: list) -> None:
    """Mark every stage whose caller never took attribution back.

    A stage invoked by a conductor at line N is `closed` only if the conductor appears in
    the attribution timeline again after N. When it does not, attribution stayed pointing at
    the stage while the conductor carried on working, and every count on that stage is an
    upper bound that silently includes the conductor's own turns.

    This is the only claim the transcript actually supports. Guessing where the stage really
    stopped would turn an unknown into a fabricated finding, which is the failure the whole
    tool exists to avoid.
    """
    for cmd in ordered:
        mine = [line for line, name in timeline if name == cmd.name]
        starts = [inv["line"] for inv in cmd.invocations]
        for inv in cmd.invocations:
            caller = inv.get("invokedBy")
            if inv["form"] != "skill" or not caller:
                # A direct execution ends at this command's last attributed line before its
                # next invocation: the same timeline evidence that closes a stage.
                nxt = min((s for s in starts if s > inv["line"]), default=None)
                inside = [line for line in mine
                          if line >= inv["line"] and (nxt is None or line < nxt)]
                cmd.direct.append((inv["line"], max(inside, default=inv["line"])))
                continue
            resumed = next((line for line, name in timeline
                            if line > inv["line"] and name == caller), None)
            if resumed is None:
                cmd.closed = False
                cmd.may_include = caller
                anomalies.append({
                    "code": "se-attribution-unclosed", "line": inv["line"],
                    "message": f"{cmd.name} was invoked as a stage by {caller}, which never "
                               f"regained attribution — counts for {cmd.name} are an upper "
                               f"bound and may include {caller}'s own turns"})
                cmd.runs.append((inv["line"], cmd.last))
            else:
                # The conductor came back, so this run genuinely ends before it.
                cmd.runs.append((inv["line"], min(cmd.last, resumed - 1)))
        if cmd.runs:
            # The attributed run ends where the LAST execution ends, never at the first resume:
            # the counts already include every execution, direct ones too, and the window must
            # agree with them.
            cmd.last = max(hi for _, hi in cmd.runs + cmd.direct)


def read_session(path: Path) -> dict:
    """One streaming pass. Never holds the transcript, only the model built from it."""
    with path.open(encoding="utf-8", errors="replace") as fh:
        return read_lines(fh, str(path))


def read_lines(source, label: str) -> dict:
    """The same pass over any iterable of JSON lines — a file, or the records another harness
    store was reshaped into (`session/opencode_transcript.py`). `label` names where they came from."""
    commands: dict[str, Command] = {}
    anomalies, seen_uuids, corrections = [], set(), []
    timeline = []                  # (line, command) for every attributed record, in order
    lines = unparsed = unattributed_tools = 0
    session = {"sessionId": None, "cwd": None, "gitBranch": None, "version": None}

    def command(name: str) -> Command:
        return commands.setdefault(name, Command(name))

    for index, raw in enumerate(source, start=1):
        raw = raw.strip()
        if not raw:
            continue
        lines += 1
        try:
            rec = json.loads(raw)
        except (ValueError, TypeError):
            rec = None
        if not isinstance(rec, dict):
            # Both arms are the same fact — this line contributed no evidence — so both
            # are counted and reported. Silence here is what makes an unreadable file
            # look like a clean one.
            unparsed += 1
            if len(anomalies) < 10:
                anomalies.append({"code": "se-unparsed-line", "line": index,
                                  "message": "line is not a JSON object and was skipped"})
            continue

        for key in session:
            if session[key] is None and rec.get(key) is not None:
                session[key] = rec[key]

        # A compact summary re-embeds earlier turns; counting them doubles the run.
        uuid = rec.get("uuid")
        if uuid is not None:
            if uuid in seen_uuids:
                continue
            seen_uuids.add(uuid)
        if rec.get("isCompactSummary"):
            continue

        ts = rec.get("timestamp")
        attributed = rec.get("attributionSkill")
        if attributed:
            cmd = command(normalize(attributed))
            if cmd.plugin is None:
                cmd.plugin = rec.get("attributionPlugin")
            cmd.touch(index, ts)
            timeline.append((index, cmd.name))

        if rec.get("type") == "user":
            content = (rec.get("message") or {}).get("content")
            # A human turn reaches the transcript BOTH ways: as a bare string, and as
            # `text` blocks in a list beside the tool_results the harness also files
            # under "user". Reading only the string form loses every interjection and
            # every interrupt — measured, not assumed: the string form in this repo's
            # largest transcript holds nothing but compaction notices.
            if isinstance(content, str):
                text = content
            else:
                text = "\n".join(b.get("text") or "" for b in blocks(rec)
                                 if b.get("type") == "text").strip()

            # Entry form 1 — a typed `/` invocation.
            if "<command-name>" in text:
                for name in COMMAND_NAME_RE.findall(text):
                    args = COMMAND_ARGS_RE.search(text)
                    cmd = command(normalize(name))
                    cmd.touch(index, ts)
                    cmd.invocations.append({
                        "form": "typed", "line": index, "timestamp": ts,
                        "args": (args.group(1).strip() if args else "") or None,
                        "invokedBy": None,
                    })
            elif text and not rec.get("isMeta"):
                # isMeta marks a turn the HARNESS wrote into the user slot — chiefly the
                # expanded body of the command just invoked. It reads like a long human
                # message and is the opposite of one; counting it would score every
                # command as having been corrected at its own first turn.
                kind = classify_user_turn(text)
                if kind:
                    corrections.append({"line": index, "timestamp": ts, "kind": kind,
                                        "text": " ".join(text.split())})

        # Entry form 2 — a conductor's stage, as a `Skill` tool_use.
        for block in blocks(rec):
            if block.get("type") != "tool_use":
                continue
            name = block.get("name")
            inp_ = block.get("input") or {}
            if attributed:
                cmd = command(normalize(attributed))
                cmd.tools[name] = cmd.tools.get(name, 0) + 1
                # Only what identifies a target is kept — never the payload. `Edit` and
                # `Write` carry whole file bodies in `new_string`/`content`, so touching
                # those inputs at all is how a digest quietly becomes the transcript.
                target = inp_.get("file_path")
                if target and name == "Read":
                    cmd.reads.setdefault(target, []).append(read_window(inp_))
                elif target and name in ("Edit", "Write", "NotebookEdit"):
                    # A write is NOT a read. Lumping them together reported 51 edits to
                    # one file as a "redundant read x72" — a confident, plausible,
                    # entirely wrong finding of exactly the kind this tool must not make.
                    cmd.touches[target] = cmd.touches.get(target, 0) + 1
                elif name == "Bash":
                    shell = (inp_.get("command") or "").strip()
                    if shell:
                        cmd.shell[shell] = cmd.shell.get(shell, 0) + 1
            else:
                unattributed_tools += 1
            if name == "Skill":
                target = inp_.get("skill")
                if target:
                    callee = command(normalize(target))
                    callee.touch(index, ts)
                    callee.invocations.append({
                        "form": "skill", "line": index, "timestamp": ts,
                        "args": (inp_.get("args") or None),
                        "invokedBy": normalize(attributed) if attributed else None,
                    })

    ordered = sorted(commands.values(), key=lambda c: (c.first if c.first is not None else 0))
    close_attribution(ordered, timeline, anomalies)
    return {
        "transcript": label,
        "session": session,
        "lines": lines,
        "unparsed": unparsed,
        "anomalies": anomalies,
        "unattributedToolCalls": unattributed_tools,
        "commands": [c.as_dict() for c in ordered],
        "_commands": ordered,          # the objects, for digest; never serialised
        "corrections": corrections,
    }


def silence_refusal(model: dict) -> dict | None:
    """Why a zero-command parse must refuse rather than report nothing.

    "No commands found" and "I could not read this file" produce the same empty report and
    call for opposite actions — rerun against another session, or fix the parser. A format
    drift in Claude Code's undocumented JSONL would otherwise land as a clean run forever,
    which is the failure mode `## Risks` names first. So a transcript that HELD something
    and yielded nothing is an exit-2 refusal carrying its reason.
    """
    if model["commands"]:
        return None
    if model["lines"] == 0:
        return {"code": "se-empty-transcript",
                "message": f"{model['transcript']} holds no records — nothing to report, "
                           f"and an empty report would not have said so"}
    unparsed = model["unparsed"]
    detail = (f", and {unparsed} of them could not be parsed — the transcript format has "
              f"likely drifted" if unparsed else
              ", all of which parsed — this session ran no command, or the attribution "
              "and entry-form marks have changed shape")
    return {"code": "se-no-command-parsed",
            "message": f"read {model['lines']} record(s) from {model['transcript']}{detail}"}
