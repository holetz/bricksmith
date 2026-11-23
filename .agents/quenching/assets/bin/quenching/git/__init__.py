"""The `git` pillar's own entry point — deterministic subcommands over a repository's git
facts. No `doctor` and no `validate`: there is no artifact here for either to check, only
questions git (and, where declared, a host CLI) already has answers for — `## Design` of the
`pilar-git-e-specs-agnosticas-ao-git` spec says why the column stays 1x5 with this row
undoctored.

`base` is minted first (task 3.1). `specs`, `stale` and `conventions` (tasks 3.2-3.4) each add
one import and one `DISPATCH` row — the same shape `specs.commands.cli` and `knowledge.cli`
already use for their own pillars, kept flat here because five leaf subcommands need no
`commands/` package of their own."""
from __future__ import annotations

import argparse
import os
import sys

from quenching.common.output import REFUSAL, NoAbbrevParser
from quenching.common.version import VERSION
from quenching.git.audit import cmd_audit
from quenching.git.base import cmd_base
from quenching.git.commit import cmd_commit
from quenching.git.conventions import cmd_conventions
from quenching.git.history import cmd_history
from quenching.git.identity import cmd_identity
from quenching.git.preflight import cmd_preflight
from quenching.git.prune import cmd_prune
from quenching.git.pull import cmd_pr
from quenching.git.push import cmd_push
from quenching.git.refresh import cmd_refresh
from quenching.git.slugs import cmd_specs
from quenching.git.stale import cmd_stale
from quenching.git.state import cmd_state
from quenching.git.worktree import cmd_worktree

DISPATCH = {"base": cmd_base, "specs": cmd_specs, "stale": cmd_stale,
            "conventions": cmd_conventions, "worktree": cmd_worktree,
            "commit": cmd_commit, "audit": cmd_audit, "state": cmd_state,
            "push": cmd_push, "prune": cmd_prune, "pr": cmd_pr,
            "history": cmd_history, "preflight": cmd_preflight, "identity": cmd_identity,
            "refresh": cmd_refresh}


READ_VERBS = ("base", "stale", "conventions", "state", "history", "preflight", "audit",
              "identity")


def build_parser() -> argparse.ArgumentParser:
    p = NoAbbrevParser(
        prog="cq git", description="a repository's git facts — deterministic, nothing to validate")
    p.add_argument("--version", action="store_true", help="print the version and exit")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("base", help="the base branch a spec merges into, and whether it is "
                                      "the host's own default")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("specs", help="read, add or remove native spec IDs in a branch's own "
                                       "`quenching-specs:` marking")
    sp.add_argument("branch")
    change = sp.add_mutually_exclusive_group()
    change.add_argument("--add", metavar="ID", help="merge one native spec ID into the marking")
    change.add_argument("--remove", metavar="ID", help="remove one native spec ID from the marking")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("stale", help="branches merged or gone, and worktrees git still "
                                       "registers with no directory on disk — a report only")
    sp.add_argument("--remote", default="origin", metavar="REMOTE",
                    help="remote whose fetched branches are reported (default: origin)")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("conventions", help="read-if-present: whether the target declares "
                                             "its own docs/standards/git/**, or the "
                                             "plugin's defaults govern")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("worktree", help="take or give back a reusable worktree slot, materialise "
                                          "declared shared paths, or cut and retire a worktree")
    sp.add_argument("action", choices=("acquire", "release", "slots", "link", "add", "retire"),
                    help="acquire: lock a free slot (or a new one under shared.worktreeSlots.max), "
                         "switch it to --branch, link and run `worktreeSetup`; release: detach a "
                         "clean slot at the remote base and unlock it; slots: list the slots and "
                         "who holds each; link: link each declared shared path; add: fetch, cut, "
                         "link and run the declared `worktreeSetup` in a one-off worktree; "
                         "retire: remove a merged spec's worktree and `branch -d` its branch")
    sp.add_argument("--path", metavar="PATH", help="add: the new worktree's directory; "
                                                    "retire/release: the worktree to give back")
    sp.add_argument("--branch", metavar="BRANCH", help="acquire/add: the branch to switch to or "
                                                        "create; retire/release: the branch it holds")
    sp.add_argument("--base", metavar="BASE", help="acquire/add: the base branch to cut from; "
                                                    "retire/release: the base it must be merged into "
                                                    "(release: default the remote's HEAD)")
    sp.add_argument("--remote", default="origin", metavar="REMOTE",
                    help="add/retire: the remote whose base is fetched (default: origin); a remote "
                         "that does not exist falls back to the local base")
    sp.add_argument("--spec", metavar="ID",
                    help="add: stamp this spec's `branch` record (base, work) once the worktree "
                         "is cut; refused before any write when the spec does not own --branch")
    sp.add_argument("--wait", action="store_true",
                    help="acquire: at the limit, retry every shared.worktreeSlots.pollSeconds "
                         "until a slot frees or the wait times out (then needs-human)")
    sp.add_argument("--timeout-minutes", type=int, dest="timeout_minutes", metavar="N",
                    help="acquire --wait: give up after N minutes (default "
                         "shared.worktreeSlots.waitMinutes, 30)")
    sp.add_argument("--session", metavar="ID",
                    help="acquire: the session named in the lock reason (default "
                         "$OPENCODE_SESSION_ID or $CLAUDE_SESSION_ID)")
    sp.add_argument("--no-setup", action="store_true",
                    help="acquire/add: do not run the declared `worktreeSetup` in the new worktree "
                         "(it runs by default, once, after the link)")
    sp.add_argument("--discard-ignored", action="store_true",
                    help="retire: remove the worktree even though it holds ignored files "
                         "that are not symlinks")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("commit", help="commit the existing index with the given subject — "
                                        "never stages, amends or skips hooks")
    sp.add_argument("--subject", required=True, metavar="SUBJECT",
                    help="the commit subject, resolved by the caller")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("state", help="the checkout's branch, porcelain status, staged paths, stash "
                                       "entries and remotes — read with a fixed argv")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("history", help="read-only: a range's log, one commit's show, or a diff — "
                                         "a fixed argv with no output-to-file option")
    sp.add_argument("kind", choices=("log", "show", "diff"))
    sp.add_argument("revs", nargs="+", metavar="REV", help="log: a range or ref; show: one sha; "
                                                           "diff: one or two revisions")
    sp.add_argument("--path", action="append", default=[], metavar="PATH",
                    help="limit to this path; repeat for each")
    sp.add_argument("--max-count", type=int, default=50, metavar="N", help="log: commits shown")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("push", help="push one local branch to one remote under its own name, "
                                      "with upstream, never forced; the spec store syncs after it")
    sp.add_argument("--branch", required=True, metavar="BRANCH", help="the local branch to push")
    sp.add_argument("--remote", default="origin", metavar="REMOTE",
                    help="a configured remote (default: origin)")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("preflight", help="read-only: store writable, type catalog, push reach, "
                                           "gh auth and base CI availability — never skips a hook")
    sp.add_argument("--remote", default="origin", metavar="REMOTE",
                    help="the remote probed (default: origin)")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("prune", help="act on one item `cq git stale` reports — `branch -d`, "
                                       "`worktree remove`, a reported remote branch, or the "
                                       "release of a reclaimable slot")
    target = sp.add_mutually_exclusive_group(required=True)
    target.add_argument("--branch", metavar="BRANCH", help="a reported stale local branch")
    target.add_argument("--worktree", metavar="PATH", help="a reported orphan worktree")
    target.add_argument("--remote-branch", metavar="BRANCH",
                        help="a branch reported under remoteBranches for --remote")
    target.add_argument("--slot", metavar="PATH",
                        help="a held slot reported as a `kind: slot` candidate; released, "
                             "never removed")
    sp.add_argument("--remote", default="origin", metavar="REMOTE",
                    help="the remote the report reads (default: origin)")
    sp.add_argument("--expect-tip", metavar="SHA",
                    help="the tip the human approved; a moved tip is refused")
    sp.add_argument("--id", metavar="ID", help="the manifest id the human approved")
    sp.add_argument("--confirm-inactive", action="store_true",
                    help="the owner confirms the live worktree is inactive")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("pr", help="the pull-request writes, with a fixed argv")
    pr = sp.add_subparsers(dest="action", required=True)
    pp = pr.add_parser("create", help="open a PR from --head into --base; prints its number "
                                      "and URL")
    pp.add_argument("--base", required=True, metavar="BRANCH", help="the branch it merges into")
    pp.add_argument("--head", required=True, metavar="BRANCH", help="the branch it publishes")
    pp.add_argument("--title", required=True, metavar="TITLE", help="the PR title")
    pp.add_argument("--body", default="", metavar="BODY", help="the PR body, sent on stdin; `-` reads it "
                                                     "from this command's stdin")
    pp.add_argument("--spec", metavar="ID", help="stamp this spec's `pr` record (number, url, "
                                                 "date) with what the PR just opened")
    pp.add_argument("--work-item", type=int, metavar="N",
                    help="Azure only: link this work item to the PR")
    pp.add_argument("--transition-work-items", action="store_true",
                    help="Azure only: transition the linked work items when the PR completes")
    pp.add_argument("--delete-source-branch", action="store_true",
                    help="Azure only: delete the source branch when the PR completes")
    pp.add_argument("--json", action="store_true", help="machine-readable output")
    pp = pr.add_parser("probe", help="read-only: is there an authenticated PR route for the "
                                     "configured provider?")
    pp.add_argument("--json", action="store_true", help="machine-readable output")
    pp = pr.add_parser("view", help="read-only: one PR's number, state, base and head as the "
                                    "configured provider reports them")
    pp.add_argument("--number", required=True, type=int, metavar="N", help="the PR's id")
    pp.add_argument("--json", action="store_true", help="machine-readable output")
    pp = pr.add_parser("record", help="stamp a spec's `pr` record with a PR opened elsewhere"
                                      "; checks --spec owns --head and the "
                                      "PR really publishes it")
    pp.add_argument("--spec", required=True, metavar="ID", help="the spec whose record is stamped")
    pp.add_argument("--head", required=True, metavar="BRANCH", help="the branch the PR publishes")
    pp.add_argument("--number", required=True, type=int, metavar="N", help="the PR's id")
    pp.add_argument("--url", required=True, metavar="URL", help="the PR's webUrl")
    pp.add_argument("--json", action="store_true", help="machine-readable output")
    pp = pr.add_parser("merge", help="merge on GitHub or Azure with a merge commit, only on green CI")
    pp.add_argument("--url", required=True, metavar="URL", help="the PR's URL")
    pp.add_argument("--wait", type=int, default=0, metavar="SECONDS",
                    help="re-read pending checks every 20s for up to this long (max 540); "
                         "progress goes to stderr — keep it under the caller's Bash timeout")
    pp.add_argument("--spec", metavar="ID",
                    help="stamp this spec's `merge` record once the PR is merged")
    pp.add_argument("--allow-no-checks", action="store_true",
                    help="accept a PR with no CI check or build policy at all (a repository "
                         "without a PR gate); red, pending, skipped and unreadable still refuse")
    pp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("refresh", help="fast-forward the local base branch to <remote>/<base> — "
                                         "never discards a commit or an uncommitted change, "
                                         "never switches a branch")
    sp.add_argument("--base", metavar="BRANCH", help="the base branch (default: the resolved base)")
    sp.add_argument("--remote", default="origin", metavar="REMOTE",
                    help="the remote read (default: origin)")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("audit", help="the verifier's facts inside one registered worktree — "
                                       "status, stash, commits, scope, ancestry, reflog; "
                                       "runs no code of the audited branch")
    sp.add_argument("--worktree", required=True, metavar="PATH",
                    help="a worktree this repository registers")
    sp.add_argument("--base", required=True, metavar="REF", help="the base the branch merges into")
    sp.add_argument("--branch", required=True, metavar="BRANCH", help="the spec's work branch")
    sp.add_argument("--sha", action="append", default=[], metavar="SHA",
                    help="a sha the worker reported; repeat for each")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("identity", help="read-only: refuse an author or committer email outside "
                                          "shared.gitIdentity.allowedEmails (the shipped hooks)")
    mode = sp.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true",
                      help="the identity the next commit will carry (pre-commit)")
    mode.add_argument("--pre-push", action="store_true",
                      help="every commit named on stdin by the pre-push hook")
    sp.add_argument("--remote", metavar="NAME",
                    help="--pre-push: the destination remote (the hook's first argument)")
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    for verb in READ_VERBS:
        sub.choices[verb].add_argument(
            "--root", metavar="PATH",
            help="read this repository instead of the one the shell starts in; a relative "
                 "path in another option resolves against it")
    return p


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print(f"cq git {VERSION}")
        return 0
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_usage(sys.stderr)
        return REFUSAL
    root = getattr(args, "root", None)
    if root is not None:
        if not os.path.isdir(root):
            print(f"cq git: --root {root!r} is not a directory", file=sys.stderr)
            return REFUSAL
        os.chdir(root)
    return DISPATCH[args.cmd](args)
