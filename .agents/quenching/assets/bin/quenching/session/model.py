"""The model a transcript is narrowed into: one `Command` per attributed name.

Moved verbatim out of the pre-refactor session script. `read_window` sits here rather than in `parse`
because it is part of what a `Command` records, not part of reading a line."""
from __future__ import annotations

import re

PLUGIN_PREFIX = re.compile(r"^quenching(?:-[a-z]+)?:")

def normalize(name: str) -> str:
    """`/quenching-specs develop` and `quenching-specs:specs:develop` are one command."""
    return name.strip().lstrip("/").strip()


def bare_agent(agent_type: str) -> str:
    """`quenching-specs:spec-orchestrator`, `quenching:spec-orchestrator` and the OpenCode or deployed
    `quenching-spec-orchestrator` are one agent; the pre-rename names stay recognised."""
    return PLUGIN_PREFIX.sub("", agent_type).removeprefix("quenching-")


class Command:
    """One command, aggregated across every turn attributed to it."""

    __slots__ = ("name", "plugin", "invocations", "tools", "first", "last",
                 "first_ts", "last_ts", "reads", "touches", "shell", "closed", "may_include",
                 "runs", "direct")

    def __init__(self, name: str):
        self.name = name
        self.plugin = None
        self.invocations = []      # how it was reached, in order
        self.tools = {}            # tool name -> count, attributed turns only
        self.first = self.last = None
        self.first_ts = self.last_ts = None
        self.reads = {}            # file path -> [window, ...], one entry per Read
        self.touches = {}          # file path -> count of Edit/Write, which are not reads
        self.shell = {}            # bash command string -> count
        # True until proven otherwise: only a stage whose caller never resumed is unclosed.
        self.closed = True
        self.may_include = None
        # One (first, last) window per `Skill` invocation, filled by `close_attribution`;
        # empty for a command never invoked as a stage.
        self.runs = []
        # One (first, last) window per direct execution — typed, or a `Skill` with no caller —
        # also filled by `close_attribution`. Kept apart from `runs`, which is serialised.
        self.direct = []

    def touch(self, index: int, ts):
        if self.first is None:
            self.first, self.first_ts = index, ts
        self.last, self.last_ts = index, ts

    @property
    def tool_calls(self) -> int:
        return sum(self.tools.values())

    @property
    def entry_forms(self) -> list:
        return sorted({i["form"] for i in self.invocations})

    def as_dict(self) -> dict:
        return {
            "command": self.name,
            "plugin": self.plugin,
            "entryForms": self.entry_forms or ["attributed"],
            "invocations": self.invocations,
            "toolCalls": self.tool_calls,
            "tools": dict(sorted(self.tools.items(), key=lambda kv: (-kv[1], kv[0]))),
            # Named `attributedRun`, not `span`: these are the turns for which this command
            # was the most recently entered one, which is NOT proof it was still executing.
            "attributedRun": {
                "firstLine": self.first,
                "lastLine": self.last,
                "firstTimestamp": self.first_ts,
                "lastTimestamp": self.last_ts,
                "closed": self.closed,
                "mayIncludeTurnsFrom": self.may_include,
                "runs": [list(run) for run in self.runs],
            },
        }


def read_window(inp: dict) -> str:
    """`Read`'s slice of a file. Two looks at the same window are redundant; two looks at
    different windows are paging, and a digest that conflates them invents a finding."""
    offset, limit = inp.get("offset"), inp.get("limit")
    if offset is None and limit is None:
        return "full"
    return f"{offset or 0}+{limit if limit is not None else 'end'}"
