"""`cq git commit` — the one deterministic commit boundary of `/quenching-specs execute`.

Commits the index AS STAGED with the subject the caller passes: it never stages, never amends,
never passes `--no-verify`, so the repository's hooks run. The caller resolves the subject
(`.agents/quenching/references/git/commit.md` §Commit messages); this verb refuses a blank one rather than
derive a generic one, refuses an empty index, and reports the subject git actually recorded so the
caller can compare it with what it will write onto the spec task.

Exit 0 committed · 1 git refused the commit (a hook, a failing identity) with HEAD unchanged ·
2 refusal: blank subject or nothing staged."""
from __future__ import annotations

import subprocess

from quenching.common.output import FINDINGS, OK, REFUSAL, emit, emit_err


def _git(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *argv], capture_output=True, text=True)


def cmd_commit(args) -> int:
    subject = (args.subject or "").strip()
    if not subject:
        return emit_err(args.json, {"message": "--subject is required and may not be blank",
                                    "exit": REFUSAL})
    if _git("diff", "--cached", "--quiet").returncode == 0:
        return emit_err(args.json, {"message": "nothing is staged; this verb commits the "
                                               "existing index and never stages",
                                    "exit": REFUSAL})
    done = _git("commit", "-m", subject)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()
        return emit_err(args.json, {"message": f"git commit failed: {detail}",
                                    "exit": FINDINGS})
    sha = _git("rev-parse", "HEAD").stdout.strip()
    recorded = _git("log", "-1", "--format=%s").stdout.strip()
    emit(args.json, {"ok": True, "sha": sha, "subject": recorded,
                     "subjectMatches": recorded == subject},
         f"committed {sha[:7]} — {recorded}")
    return OK
