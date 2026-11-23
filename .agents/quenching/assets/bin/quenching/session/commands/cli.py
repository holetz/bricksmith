"""The session pillar's argument declarations, its subcommand map, and its entry point.

`add_subcommands` declares the verbs onto ANY `argparse` subparsers action, `DISPATCH`
maps a subcommand name to the function that answers it, `build_parser` wraps the verbs
in this pillar's own standalone parser, and `main` is the standalone entry point.
Mounting is therefore the CALLER's choice and not this module's: `cq session …` and
`cq components session …` both mount by handing `add_subcommands` their own subparsers
action, and neither path is named anywhere below.

`selftest` is NOT here. `cmd_selftest` and the three fixtures are tests and migrate to
`tests/`; re-adding the verb is one subparser and one `DISPATCH` row.

Moved verbatim out of the pre-refactor session script, whose module docstring carried the two sections
below unchanged. Two adjustments to what they say: `VERSION` is now IMPORTED from
`quenching.common.version` rather than declared "below" — the same constant at the same
value, declared once for the five pillars — and `--json` is now proved by `tests/`
rather than by the `selftest` verb that left with the fixtures.

WHY IT IS NOT INSTALLED
-----------------------
The target-facing tools read the repo being aligned, so each target needs its own copy
and each align upgrades that copy by comparing `--version`. This tool's input is
`~/.claude/projects/**` — the *operator's machine*, not the repo. There is nothing for a
target repo to hold, so nothing installs it.

Two consequences follow, and both are deliberate:

  * It is **outside the seven-artifact lockstep** of `/docs/standards/ci-cd/versioning-release.md`.
    That lockstep exists because an align must decide whether an installed copy is stale;
    with no installed copy there is no such decision. `VERSION` below tracks the plugin for
    a legible `--version`, and no align compares it against anything.
  * The pre-refactor components script's stated contract — it reads `commands/**` and nothing
    else — stays intact.
    Folding a transcript reader into it would have broken that sentence.


THE TOOL CONTRACT
-----------------
Same shape as its three siblings, so a command body branches on DATA and never on prose:
every subcommand takes `--json`, and the exit code is the whole decision.

    0   ok            read clean: every record parsed and every stage's attribution closed
    1   findings      read and reported, with an anomaly against it — an unparsable record,
                      or a stage whose counts are an upper bound. The numbers are real; the
                      coverage is not provably complete
    2   refusal       nothing usable: no transcript, an empty one, a non-empty one that
                      yielded no command, or a `--command` that is not in it

For `context`, 0 means a reliable recorded peak within its role budget, 1 means above
budget, and 2 means inconclusive usage, iteration coverage, identity or role. Command
attribution is not required. The current request is absent from the transcript snapshot.

Exit 1 exists so a caller can tell "clean read" from "read with holes" without parsing the
anomaly list. It is not a failure — the findings are printed either way.
"""
from __future__ import annotations

import argparse

from quenching.common.output import OK, NoAbbrevParser
from quenching.common.version import VERSION
from quenching.session.commands.cache import cmd_cache
from quenching.session.commands.context import cmd_context
from quenching.session.commands.cost import cmd_cost
from quenching.session.commands.digest import cmd_digest
from quenching.session.commands.live import cmd_live
from quenching.session.commands.read import cmd_list


def add_subcommands(sub: argparse._SubParsersAction) -> argparse._SubParsersAction:
    """Declare this pillar's verbs onto a subparsers action the caller owns."""
    sp = sub.add_parser("list", help="every command a session ran, with its tool-call span")
    sp.add_argument("transcript", nargs="?",
                    help="path to a .jsonl transcript, or a session id; "
                         "default: the most recent session for this cwd")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_list)

    dg = sub.add_parser("digest", help="one command's evidence: counts, redundant reads, corrections")
    dg.add_argument("transcript", nargs="?", help="path to a .jsonl transcript, or a session id")
    dg.add_argument("--command", help="digest only this command (default: all of them)")
    dg.add_argument("--json", action="store_true", help="machine-readable output")
    dg.add_argument("--max-quote", type=int, default=200, metavar="N",
                    help="clip every quoted string to N characters (default: 200)")
    dg.add_argument("--cap", type=int, default=20, metavar="N",
                    help="at most N items per evidence list (default: 20)")
    dg.set_defaults(func=cmd_digest)

    co = sub.add_parser("cost", help="what a session's subagents spent, per agent type, model and window")
    co.add_argument("transcript", nargs="?",
                    help="path to a .jsonl transcript, a session id, or a subagents/ directory; "
                         "default: the most recent session for this cwd")
    scope = co.add_mutually_exclusive_group()
    scope.add_argument("--descendants-of", metavar="AGENT_ID",
                       help="count only agents launched, directly or not, by this agent (parentAgentId)")
    scope.add_argument("--nested", action="store_true",
                       help="count only agents that have a parentAgentId (launched by a subagent)")
    scope.add_argument("--latest-orchestrator", action="store_true",
                       help="count only the descendants of the most recently started orchestrator "
                            "(they run one at a time): the window is resolved here, no clock needed")
    scope.add_argument("--self", dest="self_marker", metavar="MARKER",
                       help="count only the descendants of the calling orchestrator: the subagent "
                            "transcript whose latest call carries `session cost --self MARKER`")
    co.add_argument("--since", metavar="ISO8601",
                    help="count only requests at or after this instant (an orchestrator's own window "
                         "when several share the session)")
    co.add_argument("--usd", action="store_true",
                    help="add each row's list-price estimate from quenching's price table")
    co.add_argument("--json", action="store_true", help="machine-readable output")
    co.set_defaults(func=cmd_cost)
    ca = sub.add_parser("cache", help="the prompt cache Claude Code and OpenCode delivered, in one shape")
    ca.add_argument("--harness", choices=("all", "claude", "opencode"), default="all",
                    help="which store to read (default: every store present)")
    where = ca.add_mutually_exclusive_group()
    where.add_argument("--directory", metavar="PATH", help="project to measure (default: this cwd)")
    where.add_argument("--all", action="store_true", help="every project in each store")
    ca.add_argument("--opencode-db", metavar="PATH",
                    help="OpenCode database (default: QUENCHING_OPENCODE_DB, else the XDG data dir)")
    ca.add_argument("--since", metavar="ISO8601", help="count only requests at or after this instant")
    ca.add_argument("--session", metavar="ID", help="count only this session's requests (its id)")
    ca.add_argument("--usd", action="store_true",
                    help="add each row's list-price estimate from quenching's price table")
    ca.add_argument("--json", action="store_true", help="machine-readable output")
    ca.set_defaults(func=cmd_cache)
    cx = sub.add_parser("context", help="measure a live transcript's context and role budget")
    cx.add_argument("transcript", nargs="?", help="transcript path or session id; default: OPENCODE_SESSION_ID, "
                                                  "then CLAUDE_CODE_SESSION_ID")
    who = cx.add_mutually_exclusive_group()
    who.add_argument("--agent", metavar="AGENT_ID", help="select this session's subagent transcript; on OpenCode, the child session id")
    who.add_argument("--self", dest="self_marker", metavar="MARKER",
                     help="select the caller's own transcript: the one whose latest call carries "
                          "`session context --self MARKER`")
    cx.add_argument("--json", action="store_true", help="machine-readable output")
    cx.set_defaults(func=cmd_context)
    lv = sub.add_parser("live", help="live sessions and sub-agents, fixed fields only")
    lv.add_argument("--cwd", metavar="PATH",
                    help="keep only sessions inside this repository's worktrees")
    lv.add_argument("--json", action="store_true", help="machine-readable output")
    lv.set_defaults(func=cmd_live)
    return sub


DISPATCH: dict = {
    "list": cmd_list,
    "digest": cmd_digest,
    "cost": cmd_cost,
    "cache": cmd_cache,
    "context": cmd_context,
    "live": cmd_live,
}


def build_parser(prog: str = "cq components session") -> argparse.ArgumentParser:
    p = NoAbbrevParser(prog=prog,
                                description="read a session transcript as evidence")
    p.add_argument("--version", action="store_true", help="print the version and exit")
    sub = p.add_subparsers(dest="cmd")
    add_subcommands(sub)
    return p


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(f"session {VERSION}")
        return OK
    if not getattr(args, "func", None):
        parser.print_help()
        return OK
    return args.func(args)
