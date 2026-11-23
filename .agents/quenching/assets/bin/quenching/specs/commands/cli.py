"""The specs pillar's argument declarations, its subcommand map, and its entry point.

`build_parser` declares every argument, `DISPATCH` maps a subcommand name to the function that
answers it, and `main` is the pillar's whole entry: the UTF-8 reconfiguration, the repository
resolution, the invocation's `Emitter`, and the single place a `BackendRefusal` becomes an exit code.
`cq specs …` mounts on these three.

Every dispatched verb takes `(args, root, out)`. The third is the invocation's output layer,
carrying the resolution receipt: an object with this call's lifetime, handed down rather than a
module global reached for.

`selftest` is NOT here. `cmd_selftest` is a test and migrates to `tests/`; whether the verb
survives is task 6.3's to decide, and re-adding it is one subparser and one `DISPATCH` row."""
from __future__ import annotations

import argparse
import importlib
import os
import sys

from quenching.common.output import CQArgumentParser, OK, refuse
from quenching.common.version import VERSION
from quenching.specs.backends.base import BackendRefusal
from quenching.specs.parse.fields import UnclosedFence
from quenching.specs.commands.handoff import add_parser as add_handoff_parser
from quenching.specs.commands.output import Emitter
from quenching.specs.config import find_repo_root
from quenching.specs.parse import PHASES
from quenching.specs.schema import DEFAULT_VERIFICATION, OUTCOMES, VERIFICATION_POLICIES


def add_json(sp):
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    return sp


# Verbs that take the spec id as a positional as well as `--spec`; True = the id is required.
SPEC_POSITIONAL = {"status": False, "show": True, "parallel": True, "estimate": True,
                   "depends": True, "schedule": False}


def add_spec(sp, required: bool):
    """`--spec <id>` stays; the id is also accepted positionally, as `section` and `promote` take it."""
    sp.add_argument("spec_id", nargs="?", metavar="ID", help="the spec id (same as --spec)")
    sp.add_argument("--spec", help="one spec ID" if not required else "the spec ID")
    sp.set_defaults(id_required=required)  # read by the cq-call lint, which sees no `required=True`
    return sp


def _add_new(sub) -> None:
    sp = add_json(sub.add_parser("new", help="capture a spec into plans/"))
    sp.add_argument("name")
    sp.add_argument("--title")
    sp.add_argument("--verification", choices=list(VERIFICATION_POLICIES),
                    help=f"when the suite runs (default: {DEFAULT_VERIFICATION})")
    sp.add_argument("--subject",
                    help="a key from `azurePlacement.subjects` — applies its parent (where "
                         "the backend has one) and its fixed tags; omit to fall back to "
                         "`defaultSubject`")
    sp.add_argument("--type",
                    help="a key from `workItemTypes` — recorded as `workItemType:` in the "
                         "new spec's frontmatter; omit to resolve one later, at build time")
    sp.add_argument("--tags",
                    help="a comma-separated list — REPLACES the whole list, same as "
                         "`cq specs tags`; the subject's fixed tags are folded in "
                         "automatically and need not be repeated")
    # No `choices=`: argparse would turn a bad value into a usage error with no JSON, the same
    # reason `verification`'s `policy` argument below has none. The command-level refusal
    # carries `sp-bad-complexity` and the declared levels like every other refusal here.
    sp.add_argument("--complexity",
                    help="one of low/medium/high/xhigh — written into the new spec's "
                         "`priority.complexity` record")
    sp.add_argument("--card", type=int, metavar="N",
                    help="adopt issue / work item N as this spec's card: its text becomes "
                         "`## Problem` when none is supplied, N becomes the spec's ID and the "
                         "item is rewritten to the thin card (the branch store only)")


def _add_read(sub) -> None:
    sp = add_json(sub.add_parser("list", help="every spec, by folder and derived stage"))
    sp.add_argument("--phase", choices=list(PHASES),
                    help="cut the listing to one phase (default: every phase)")
    sp.add_argument("--lean", action="store_true",
                    help="use the provider's native index; omit document-derived fields")

    sp = add_json(sub.add_parser("status", help="one spec's sections, stage, tasks, gates — "
                                                "or its composition when it lists member specs"))
    add_spec(sp, False)
    sp.add_argument("--epic", help="force the composition view for a spec ID")
    sp.add_argument("--no-fetch", action="store_true",
                    help="read the local remote-tracking ref already present; never fetch "
                         "(git store only — other backends refuse)")

    sp = add_json(sub.add_parser("show", help="granular read: ONE task, the map by default, "
                                              "the document only with --full (section "
                                              "bodies are `section`'s)"))
    add_spec(sp, True)
    sp.add_argument("--task", action="append", metavar="ID",
                    help="one task's line and metadata, by id or index; repeatable")
    sp.add_argument("--full", action="store_true",
                    help="the WHOLE document — never the default, because every caller "
                         "that did not need it pays for it in context on every later turn")


def _add_section_and_fields(sub) -> None:
    sp = add_json(sub.add_parser("section", help="read or write N sections in ONE call"))
    sp.add_argument("spec")
    sp.add_argument("heading", nargs="?",
                    help="one canonical heading, or several comma-separated; returned in "
                         "the order asked. Omit when --moment resolves the list instead")
    sp.add_argument("--moment", choices=["decision", "build", "close"],
                    help="read every canonical section declared this moment, in canonical "
                         "order, instead of an enumerated heading list")
    sp.add_argument("--write", action="store_true",
                    help="replace one or more headings from stdin, creating each in "
                         "canonical position. Several: open the stream on `## <Heading>` "
                         "lines, one per body, and the set must match what was declared "
                         "here; a stream carrying no heading is one raw body under the one "
                         "heading declared. An empty body refuses (sp-write-empty-body) "
                         "unless --clear asks for it")
    sp.add_argument("--clear", action="store_true",
                    help="with --write: let an empty body through, putting the template's "
                         "guidance back under that heading. Without it an empty stdin — a "
                         "tty, a producer that failed upstream of the pipe — exits 2 and "
                         "writes nothing")
    sp.add_argument("--scope", choices=["global", "current"],
                    help="narrow ## Handoff to one block — the evergreen global one, or that "
                         "one plus the ### N. matching the next actionable task's section. "
                         "Reading, it composes with the other headings asked for (--moment "
                         "build included), which come back whole; writing, it replaces just "
                         "that block, leaves every other untouched, and takes ## Handoff alone")
    sp.add_argument("--fold", metavar="STRAY",
                    help="close an sp-stray-heading: demote `## <STRAY>` to `### <STRAY>` so "
                         "it folds into the canonical section that precedes it, text "
                         "preserved. The ONE path that admits a heading outside the thirteen "
                         "— every other way of naming one still exits 2 — and it takes no "
                         "heading, --moment, --write or --scope beside it")

    sp = add_json(sub.add_parser("verification",
                                 help="read or set ONE spec's verification policy"))
    sp.add_argument("spec")
    # No `choices=`: argparse would turn a bad value into a usage error with no JSON, and every
    # caller of this tool is told to branch on the exit code AND the `--json` payload. The
    # check lives in the command, where the refusal carries `sp-bad-verification` and the
    # declared set like every other refusal here.
    sp.add_argument("policy", nargs="?",
                    help="omit to read; one of " + ", ".join(VERIFICATION_POLICIES))

    sp = add_json(sub.add_parser("title",
                                 help="read or repair ONE spec's `title`, quoted when it needs it"))
    sp.add_argument("spec")
    sp.add_argument("title", nargs="?", help="omit to read; the new title to set")

    for field, value_help in (
        ("tags", "omit to read; a comma-separated list to SET (replaces, never appends)"),
        ("assignee", "omit to read; a name or identity to set"),
        ("start", "omit to read; YYYY-MM-DD to set"),
        ("target", "omit to read; YYYY-MM-DD to set"),
        ("end", "omit to read; YYYY-MM-DD to set (the finish the spec declares)"),
    ):
        sp = add_json(sub.add_parser(field, help=f"read or set ONE spec's `{field}` — "
                                                 f"stored, never projected"))
        sp.add_argument("spec")
        sp.add_argument("value", nargs="?", help=value_help)
        sp.set_defaults(field=field)


def _add_record_promote_task(sub) -> None:
    sp = add_json(sub.add_parser("record", help="read or merge ONE frontmatter record"))
    sp.add_argument("spec")
    sp.add_argument("name", help="one of the declared records")
    sp.add_argument("--set", action="append", metavar="FIELD=VALUE",
                    help="merge one field; repeatable. Fields not named survive, so a "
                         "re-stamp never drops what an earlier pass wrote")

    sp = add_json(sub.add_parser("promote", help="the gated close-out: plans/ → archive/"))
    sp.add_argument("spec")
    sp.add_argument("--to", choices=list(PHASES), help="force the destination phase")
    sp.add_argument("--outcome", choices=list(OUTCOMES),
                    help="archive hop only (default: done)")
    sp.add_argument("--force", action="store_true",
                    help="archive as done despite open tasks")
    sp.add_argument("--dry-run", action="store_true", dest="dry_run")

    sp = add_json(sub.add_parser("reconcile", help="close every merged spec: archive it after the merge"))
    sp.add_argument("--write", action="store_true")
    sp.add_argument("--remote", default="origin")

    sp = add_json(sub.add_parser("task", help="check, reopen or block a checkbox"))
    sp.add_argument("--spec", required=True)
    sp.add_argument("--check")
    sp.add_argument("--uncheck")
    sp.add_argument("--block", help="mark TASK blocked (requires --reason)")
    sp.add_argument("--descope", help="close TASK as deliberately out of scope (requires --reason)")
    sp.add_argument("--reason", help="why the task is blocked, reopened or descoped — written "
                                      "into the spec")
    sp.add_argument("--subject", help="the subject of the commit that implements the task, "
                                      "recorded as a `subject:` metadata line "
                                      "(goes with --check)")
    sp.add_argument("--commit", help="the sha of the commit that implements the task, "
                                     "recorded as a `commit:` metadata line — call it AFTER "
                                     "the commit exists (goes with --check; additive to "
                                     "--subject, not a replacement for it)")


def _add_next_and_epic(sub) -> None:
    sp = add_json(sub.add_parser("next", help="THE single next action, or --front for the "
                                              "ranked candidate list"))
    sp.add_argument("--spec", help="one spec's next action")
    sp.add_argument("--table", action="store_true",
                    help="with --front: the ranked table `spec-driven.md` §The spec table "
                         "declares, instead of one line per spec. Refuses with --json")
    sp.add_argument("--columns",
                    help="with --table: a comma-separated subset to print. Columns are "
                         "omitted, never reordered (default: all)")
    sp.add_argument("--order", choices=["rank", "priority"], default="rank",
                    help="with --front: `rank` is the four-factor ordering (executing, "
                         "closest to done, priority, age); `priority` is the human's "
                         "ranking alone")
    sp.add_argument("--front", action="store_true",
                    help="rank every active spec: executing, closest to done, priority, age")
    sp.add_argument("--epic", help="a spec ID: its member specs ready to run now — "
                                   "dependencies done, approved or ready, not blocked")
    sp.add_argument("--limit", type=int, help="with --epic: at most N ready specs")

    sp = sub.add_parser("epic", help="the historical alias of `link add`")
    esub = sp.add_subparsers(dest="epic_cmd")
    ep = add_json(esub.add_parser("add", help="list a spec as a member of another "
                                              "(alias of `link add`)"))
    ep.add_argument("epic", help="the composite spec ID")
    ep.add_argument("spec", help="the member spec ID")
    ep.add_argument("--after", help="comma-separated item labels this one waits for")
    ep.add_argument("--group", help="the `### N.` group (wave) to list it under; "
                                    "created when new")
    ep.add_argument("--label", help="the item label (default: the next S<n>)")
    ep.add_argument("--move", action="store_true",
                    help="accepted as a no-op: a spec may belong to several composites")

    sp = sub.add_parser("link", help="composition: list a spec as a member, or remove it")
    lsub = sp.add_subparsers(dest="link_cmd")
    lp = add_json(lsub.add_parser("add", help="list a spec as a member of another"))
    lp.add_argument("epic", help="the composite spec ID")
    lp.add_argument("spec", help="the member spec ID")
    lp.add_argument("--after", help="comma-separated item labels this one waits for")
    lp.add_argument("--group", help="the `### N.` group (wave) to list it under; "
                                    "created when new")
    lp.add_argument("--label", help="the item label (default: the next S<n>)")
    lp.add_argument("--move", action="store_true",
                    help="accepted as a no-op: a spec may belong to several composites")
    lp = add_json(lsub.add_parser("remove", help="take a spec out of a composite"))
    lp.add_argument("epic", help="the composite spec ID")
    lp.add_argument("spec", help="the member spec ID")


def _add_inspection(sub) -> None:
    sp = add_json(sub.add_parser("estimate", help="estimated declared build reading by task section"))
    add_spec(sp, True)
    sp.add_argument("--worker-budget", type=int, default=None,
                    help="positive size estimate in tokens (default: shared.contextBudgets.worker, else 200000); not a stop limit and not a model limit")
    sp.add_argument("--task", help="project ONE task's executor context and judge the small-tier "
                                   "conditions (ADR 0014) instead of the section reading")

    sp = add_json(sub.add_parser("scrub", help="list the specs the leak gate flags; --apply "
                                               "normalizes their paths only"))
    sp.add_argument("--apply", action="store_true",
                    help="write the path normalization (never a denylist or generic hit)")

    sp = add_json(sub.add_parser("parallel", help="prove a [P] group's files: are disjoint"))
    add_spec(sp, True)

    sp = add_json(sub.add_parser("find", help="resolve a branch, a commit, or a PR back to "
                                              "the spec that owns it"))
    sp.add_argument("--branch", help="an exact `branch.work` value")
    sp.add_argument("--commit", help="a commit sha, resolved to its subject and matched "
                                     "against each task's recorded `subject:`")
    sp.add_argument("--pr", help="a PR number or URL, matched against `pr`/`merge.pr`")

    sp = add_json(sub.add_parser("push", help="send the git store's pending local writes in "
                                              "one push; the store branch keeps one commit"))
    sp.add_argument("--drop", action="append", type=int, metavar="ID",
                    help="discard this spec's pending local writes before the push (repeatable)")

    sp = add_json(sub.add_parser("artifact", help="the text a spec carries beside its document, "
                                                 "on the store branch: list, read, write or rm"))
    asub = sp.add_subparsers(dest="artifact_cmd", metavar="VERB")
    pad = add_json(asub.add_parser("list", help="the artifacts of `--spec`, or the standalone "
                                                 "ones when it is omitted"))
    pad.add_argument("--spec")
    pad = add_json(asub.add_parser("read", help="print one artifact, or write it with --out"))
    pad.add_argument("--spec")
    pad.add_argument("--name", required=True)
    pad.add_argument("--out", help="write the text to this file instead of stdout")
    pad = add_json(asub.add_parser("write", help="store one artifact from stdin or --from"))
    pad.add_argument("--spec")
    pad.add_argument("--name", required=True)
    pad.add_argument("--from", dest="from_path", metavar="PATH",
                     help="read the text from this file (`-` is stdin, the default)")
    pad = add_json(asub.add_parser("rm", help="remove one artifact"))
    pad.add_argument("--spec")
    pad.add_argument("--name", required=True)

    add_handoff_parser(sub, add_json)

    sp = add_json(sub.add_parser("discover", help="append a line to ## Discoveries"))
    sp.add_argument("spec")
    sp.add_argument("text")

    sp = add_json(sub.add_parser("validate", help="the canonical set, the gates, the sp-* codes"))
    sp.add_argument("--spec", help="one spec ID (default: every spec)")
    sp.add_argument("--phase", choices=list(PHASES),
                    help="cut the sweep to one phase (default: every phase)")
    sp.add_argument("--by-code", action="store_true", dest="by_code",
                    help="one line per (code, severity) with the count and the specs, "
                         "instead of one line per finding")

    add_json(sub.add_parser("config", help="the workspace's declared parameters, as data"))

    sp = add_json(sub.add_parser("release", help="bump the plugin's version-carrying "
                                                  "artifacts and tag the commit — the plugin's "
                                                  "own repository only"))
    sp.add_argument("new_version", metavar="version", help="the new version, X.Y.Z")

    add_json(sub.add_parser("doctor", help="workspace shape; remedies declared"))


def _add_migrate_export_serve(sub) -> None:
    sp = add_json(sub.add_parser("migrate", help="move the specs of a tracker backend onto the "
                                                 "`quenching` branch (`--to git`), or the git "
                                                 "store onto Azure Boards (`--to azure`) or "
                                                 "GitHub Issues (`--to github`); dry run "
                                                 "unless --write"))
    sp.add_argument("--to", choices=["git", "azure", "github"], help="the destination store")
    sp.add_argument("--write", action="store_true",
                    help="commit the batch to the branch, or create the work items "
                         "(default: report only)")
    sp.add_argument("--thin-open-cards", action="store_true", dest="thin_open_cards",
                    help="with --write: rewrite every OPEN source issue / work item to the "
                         "thin card; closed ones are never touched")
    sp.add_argument("--dry-run", action="store_true", dest="dry_run", help=argparse.SUPPRESS)

    sp = add_json(sub.add_parser("export", help="dump the canonical markdown to disk — "
                                                "write-only, nothing reads it back"))
    grp = sp.add_mutually_exclusive_group(required=True)
    grp.add_argument("--spec", help="one spec ID")
    grp.add_argument("--all", action="store_true", help="every spec")
    sp.add_argument("--out", default="specs-export",
                    help="destination directory (default: ./specs-export)")

    sub.add_parser("mcp", help="the MCP server over stdio — a second port of this same "
                               "core: six read tools, seven write tools, human "
                               "confirmation on the gated moves, and read-only "
                               "resources (config, standards, glossary)")

    sp = sub.add_parser("sync", help="the local Azure Boards cache: status, reconcile, apply a "
                                     "service-hook event, or serve the loopback receiver")
    ssub = sp.add_subparsers(dest="sync_cmd")
    add_json(ssub.add_parser("status", help="the cache's path, items, stale count, last reconciliation"))
    add_json(ssub.add_parser("reconcile", help="compare every id and revision with Azure; "
                                               "refetch what changed"))
    add_json(ssub.add_parser("apply", help="record ONE service-hook event read from stdin"))
    sv = add_json(ssub.add_parser("serve", help="the service-hook receiver, loopback only; the "
                                                "secret comes from QUENCHING_SYNC_HOOK_SECRET"))
    sv.add_argument("--port", type=int, default=0, help="port (default: a free one)")
    sv.add_argument("--host", default="127.0.0.1", help="loopback address only")

    sp = add_json(sub.add_parser("serve", help="the local spec portal — a browser board over "
                                                "the same backend, loopback only"))
    sp.add_argument("--port", type=int, default=0, help="port (default: a free one)")
    sp.add_argument("--host", default="127.0.0.1", help="loopback address only")
    sp.add_argument("--read-only", action="store_true", dest="read_only",
                    help="disable every write route on the server")
    sp.add_argument("--open", action="store_true", help="open the URL in the browser")
    sp.add_argument("--sessions", action="store_true",
                    help="serve GET /api/sessions: the live Claude Code sessions of this "
                         "repository's worktrees, fixed fields only (off by default)")
    sp.add_argument("--chat", action="store_true",
                    help="serve POST /api/chat: questions answered by a read-only `claude -p` "
                         "(sonnet, Read/Grep/Glob only) in a worktree, polled (off by default)")
    sp.add_argument("--chat-budget-usd", type=float, default=None, dest="chat_budget_usd",
                    help="the dollar ceiling of one chat question (default: 0.50)")


def build_parser() -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:
    p = CQArgumentParser(prog="cq specs",
                         description="deterministic trail for the specs front")
    p.add_argument("--root", help="the repository directory (default: current repository)")
    p.add_argument("--version", action="store_true", help="print the version and exit")
    sub = p.add_subparsers(dest="cmd")

    sp = add_json(sub.add_parser("depends", help="read or edit ONE spec's dependencies"))
    add_spec(sp, True)
    sp.add_argument("--add", help="comma-separated dependency ids to add")
    sp.add_argument("--remove", help="comma-separated dependency ids to remove")

    sp = add_json(sub.add_parser("schedule", help="derive dates and the critical path"))
    add_spec(sp, False)
    sp.add_argument("--all", action="store_true",
                    help="schedule every spec in the store, archived and edge-less included "
                         "(the default selects the open specs that declare an edge plus their "
                         "transitive dependencies); refuses with --spec")

    _add_new(sub)
    _add_read(sub)
    _add_section_and_fields(sub)
    _add_record_promote_task(sub)
    _add_next_and_epic(sub)
    _add_inspection(sub)
    _add_migrate_export_serve(sub)
    for verb in ("status", "show", "section"):
        sub.choices[verb].add_argument(
            "--root", metavar="PATH", default=argparse.SUPPRESS,
            help="read this repository instead of the one the shell starts in; wins over "
                 "the global --root (a flag after the verb matches the verb's grants)")
    return p, sub


class _LazyDispatch(dict):
    """`DISPATCH`, with each value a `"module:function"` string resolved on first use.

    A pillar that imports every subcommand at the top pays for all of them on every call (the
    portal's `http.server` stack, the migrate and sync backends). Keys, `in`, `len` and iteration
    cost nothing; a lookup, `items()` or `values()` imports only the module asked for."""

    def __getitem__(self, verb):
        target = dict.__getitem__(self, verb)
        if isinstance(target, str):
            module, _, name = target.partition(":")
            target = getattr(importlib.import_module(module), name)
            dict.__setitem__(self, verb, target)
        return target

    def get(self, verb, default=None):
        return self[verb] if verb in self else default

    def items(self):
        return [(verb, self[verb]) for verb in self]

    def values(self):
        return [self[verb] for verb in self]


DISPATCH: dict = _LazyDispatch({
    "depends": "quenching.specs.commands.deps:cmd_depends",
    "schedule": "quenching.specs.commands.deps:cmd_schedule",
    "estimate": "quenching.specs.commands.estimate:cmd_estimate",
    "scrub": "quenching.specs.commands.scrub:cmd_scrub",
    "new": "quenching.specs.commands.create:cmd_new",
    "list": "quenching.specs.commands.read:cmd_list",
    "status": "quenching.specs.commands.read:cmd_status",
    "show": "quenching.specs.commands.granular:cmd_show",
    "section": "quenching.specs.commands.granular:cmd_section",
    "verification": "quenching.specs.commands.fields:cmd_verification",
    "title": "quenching.specs.commands.fields:cmd_title",
    "tags": "quenching.specs.commands.fields:cmd_field",
    "assignee": "quenching.specs.commands.fields:cmd_field",
    "start": "quenching.specs.commands.fields:cmd_field",
    "target": "quenching.specs.commands.fields:cmd_field",
    "end": "quenching.specs.commands.fields:cmd_field",
    "record": "quenching.specs.commands.fields:cmd_record",
    "promote": "quenching.specs.commands.promote:cmd_promote",
    "reconcile": "quenching.specs.commands.reconcile:cmd_reconcile",
    "task": "quenching.specs.commands.task:cmd_task",
    "next": "quenching.specs.commands.next:cmd_next",
    "parallel": "quenching.specs.commands.parallel:cmd_parallel",
    "find": "quenching.specs.commands.find:cmd_find",
    "discover": "quenching.specs.commands.task:cmd_discover",
    "push": "quenching.specs.commands.push:cmd_push",
    "artifact": "quenching.specs.commands.artifacts:cmd_artifact",
    "handoff": "quenching.specs.commands.handoff:cmd_handoff",
    "epic": "quenching.specs.commands.epic:cmd_epic",
    "link": "quenching.specs.commands.epic:cmd_epic",
    "validate": "quenching.specs.commands.validate:cmd_validate",
    "config": "quenching.specs.commands.doctor:cmd_config",
    "release": "quenching.specs.commands.release:cmd_release",
    "doctor": "quenching.specs.commands.doctor:cmd_doctor",
    "migrate": "quenching.specs.commands.migrate:cmd_migrate",
    "export": "quenching.specs.commands.read:cmd_export",
    "serve": "quenching.specs.portal:cmd_serve",
    "mcp": "quenching.specs.mcp:cmd_mcp",
    "sync": "quenching.specs.commands.sync:cmd_sync",
})


def _force_utf8_output() -> None:
    """Spec files are prose — em-dashes, arrows, accented words — and a Windows console
    defaults to cp1252, where printing one raises UnicodeEncodeError AFTER the write
    already landed. That turns a successful `task --check` into a traceback and a nonzero
    exit, which the exit-code contract (0 ok / 1 findings / 2 refusal) reads as a finding.
    Encode output as UTF-8 and never let a glyph decide the exit code."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def main(argv: list[str]) -> int:
    _force_utf8_output()
    parser, _ = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(f"cq specs {VERSION}")
        return OK
    if not getattr(args, "cmd", None):
        return refuse({"code": "sp-no-command", "message":
                        "choose a specs command"}, False)
    if not hasattr(args, "json"):
        args.json = False
    if args.cmd in SPEC_POSITIONAL:
        pos = getattr(args, "spec_id", None)
        if pos and args.spec and str(pos) != str(args.spec):
            parser.error(f"{args.cmd}: the id was given twice and differs "
                         f"(positional {pos}, --spec {args.spec})")
        args.spec = args.spec or pos
        if SPEC_POSITIONAL[args.cmd] and not args.spec:
            parser.error(f"{args.cmd}: the spec id is required: pass <id> or --spec <id>")
    if getattr(args, "no_fetch", False):
        os.environ["QUENCHING_SPECS_NO_FETCH"] = "1"        # before the config load shares the backend
    root = find_repo_root(os.path.abspath(args.root or os.getcwd()))
    # One command, one resolution — held by the emitter's lifetime. It is built here, handed to
    # the verb, and dropped when the call returns, so a process that dispatches more than once
    # cannot let one command's receipt ride out on the next one's payload.
    out = Emitter()
    try:
        return DISPATCH[args.cmd](args, root, out)
    except BackendRefusal as e:
        # THE ONE PLACE A TRANSPORT FAILURE BECOMES AN EXIT CODE. An external backend can
        # fail in the middle of a primitive that has no error channel, and the contract is
        # a legible refusal and never a traceback — so the failure is raised where it
        # happens, carrying the message already built, and converted exactly once here.
        return out.emit_err(args.json, e.err)
    except UnclosedFence as e:
        return out.emit_err(args.json, {"ok": False, "code": "sp-unclosed-fence", "exit": 2,
                                        "key": e.key, "message": str(e)})
