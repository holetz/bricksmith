"""Resource activity — the one measurement that asks git a question, and the two calls that ask it.

Moved verbatim out of the pre-refactor OKF validator script; only `parse_frontmatter`'s call
shape changed, from the `(fm, has_block, well_formed)` tuple to the bare dict
`quenching.common.frontmatter` returns. It emitted a `stale-doc` WARN until 2026-08-27; see
`validate.py`, which records the retirement where the call used to be.

**A FIGURE, NOT A VERDICT — and the difference is what this module exists to hold.** The
measurement is honest: the doc's `generated.at` (OKF v0.2) — or the legacy `timestamp` — against
the last commit touching each entry of its `resource:`. The verdict built on top of it was not.
A `resource:` glob is deliberately wide
(`assets/**`, three named scripts), so what the comparison detects is ACTIVITY IN THE RADIUS OF
THE GLOB — never drift of the content the doc describes. Code moves under a rule that did not
change, and the doc is then reported as possibly wrong for being correct about something stable.

Measured on this repo, 2026-08-27: **50 of 95 docs** carried the warning. A signal more than half
the corpus raises is not read as a signal; it is scrolled past, and it takes the findings printed
beside it along.

So the number is published **per `resource:` entry**, with the interval, and no code beside it. A
reader who wants to know which docs sit next to the most movement gets a ranking; nobody is told a
doc is wrong on evidence that cannot say so.

The two subprocess calls are this pillar's own and NOT `quenching.common.git._git`: `_git`
falls back to `cwd="."` for a directory that does not exist, where these must answer
"no git facts" for that path, and `_is_git_checkout` reads the exit code and the stdout
together. Left as they were rather than folded into the shared helper.
"""
from __future__ import annotations

import os
import subprocess
from datetime import date

from quenching.common.frontmatter import parse_frontmatter
from quenching.knowledge.resource import (_is_bundle_aggregate, _project_root,
                                          _rel_to_project, parse_resource)
from quenching.knowledge.schema import GIT_TIMEOUT_S, ISO_DATE, RESOLVABLE_KINDS, _nonempty, recency

_GIT_CHECKOUT: dict[str, bool] = {}


def _is_git_checkout(project_root: str) -> bool:
    """Whether `project_root` sits inside a git work tree. Cached per process, so a
    whole-tree sweep asks once per bundle rather than once per doc — a repo that is
    not a checkout would otherwise pay one failed subprocess per concept doc."""
    key = os.path.abspath(project_root)
    if key not in _GIT_CHECKOUT:
        try:
            proc = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                                  cwd=key, capture_output=True, text=True,
                                  timeout=GIT_TIMEOUT_S)
            _GIT_CHECKOUT[key] = proc.returncode == 0 and proc.stdout.strip() == "true"
        except (OSError, subprocess.SubprocessError):
            _GIT_CHECKOUT[key] = False
    return _GIT_CHECKOUT[key]


def _git_last_commit_date(entries: list[str], project_root: str) -> str | None:
    """Newest committer date (`YYYY-MM-DD`) across the entry globs, or None when
    the answer cannot be trusted — not a git checkout, git absent, no commit
    touching the scope, or the call timed out.

    Pathspecs carry explicit **`:(glob)`** magic. Git's default wildmatch lets a
    single `*` cross a slash, so a naive `src/*` silently widens to everything
    under `src/` and reports a perfectly current doc as stale; `:(glob)` gives the
    same segment-wise semantics `_glob_contains` implements for `resource-self`,
    so one glob means one thing across the whole checker. The fixture's
    `shallow.md` pins this: it is silent under `:(glob)` and a false positive
    without it.
    """
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--"] + [f":(glob){e}" for e in entries],
            cwd=project_root, capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()[:10] or None


def _days_between(earlier: str, later: str) -> int | None:
    """Whole days from `earlier` to `later`, both `YYYY-MM-DD`, or None if either is unreadable.

    `date.fromisoformat` rather than a subtraction on strings: the interval is the column a
    reader sorts by, and a lexicographic difference is not a number of days."""
    try:
        return (date.fromisoformat(later) - date.fromisoformat(earlier)).days
    except (TypeError, ValueError):
        return None


def resource_activity(text: str, bundle_root: str) -> dict | None:
    """One doc's `resource:` activity, entry by entry — or None when there is nothing to ask.

    THE MEASUREMENT IS PER ENTRY, and that is the whole point of the shape. A `resource:` naming
    three scripts and a tree used to collapse into ONE `git log` over all four pathspecs, so the
    answer was the newest commit anywhere in the union — a reader could not tell which entry moved,
    and the widest glob always won. One call per entry costs more subprocesses and buys the only
    reading that is actionable.

    `scopeMeasured` is False when every entry is a `uri`, an `unknown`, or a bundle aggregate:
    such a doc has no measurable scope, and the earlier shape returned an empty list for it —
    indistinguishable, downstream, from a doc whose scope was measured and found quiet. Reporting
    it as unmeasured is the same rule `parse-honesty.md` applies to a parse failure: name the
    state, never let silence read as a clean result.
    """
    fm = parse_frontmatter(text)
    stamp = recency(fm)
    if not _nonempty(fm, "resource") or stamp is None:
        return None
    stamped = stamp[:10]
    if not ISO_DATE.match(stamped):
        return None        # a non-ISO stamp is `missing-generated`'s business, not ours
    project_root = _project_root(bundle_root)
    if not _is_git_checkout(project_root):
        return None        # no history to compare against — measure nothing, report nothing
    bundle_rel = _rel_to_project(bundle_root, project_root)
    parsed = parse_resource(fm["resource"])
    entries = [(e, kind) for e, kind in parsed
               if kind in RESOLVABLE_KINDS and not _is_bundle_aggregate(e, kind, bundle_rel)]
    if not entries:
        return {"timestamp": stamped, "scopeMeasured": False, "entries": []}

    rows = []
    for entry, kind in entries:
        last = _git_last_commit_date([entry], project_root)
        rows.append({"entry": entry, "kind": kind, "lastCommit": last,
                     "days": _days_between(stamped, last) if last else None})
    return {"timestamp": stamped, "scopeMeasured": True, "entries": rows}
