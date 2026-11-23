"""`promote` — the one remaining transition, `plans/` -> `archive/`."""
from __future__ import annotations

import subprocess

from quenching.specs.backends import open_backend
from quenching.specs.backends.base import BackendRefusal
from quenching.specs.commands import epic
from quenching.specs.commands.output import Emitter, read_one
from quenching.specs.commands.push import card_failures, card_warnings
from quenching.specs.commands.read import _next_phase
from quenching.specs.parse import PHASES
from quenching.specs.commands.epic import pr_states
from quenching.specs.members import read_members
from quenching.specs.parse.epics import open_items, parse_items
from quenching.specs.parse.fields import set_frontmatter_key
from quenching.specs.parse.text import body_after_frontmatter
from quenching.specs.parse.sections import duplicate_headings, gate_report, section_state
from quenching.specs.schema import OUTCOMES, canonical_headings


def isolated(info: dict) -> bool:
    """Whether the spec's `branch` record names a work ref distinct from its base."""
    rec = info["frontmatter"].get("branch")
    if not isinstance(rec, dict):
        return False
    work = str(rec.get("work") or "").strip()
    return bool(work) and work != str(rec.get("base") or "").strip()


def merge_evidence(info: dict, root: str, remote: str = "origin", *, fetch: bool = True,
                   ask_pr: bool = True) -> tuple[bool, str]:
    """The store is the quenching ref: an archive is visible at once while its PR can still stall.
    `fetch=False` and `ask_pr=False` keep the answer offline, for a caller that already fetched."""
    if not isolated(info):
        return True, "no isolated work ref"
    rec = info["frontmatter"]["branch"]
    work = str(rec["work"]).strip()
    base = str(rec.get("base") or "").strip()
    merge = info["frontmatter"].get("merge")
    if isinstance(merge, dict) and str(merge.get("strategy") or "").strip():
        return True, "merge record"

    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=root, capture_output=True,
                                  text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None

    if fetch:
        git("fetch", "--quiet", remote, base)
    work_tip = work_ref = None
    for ref in (f"refs/heads/{work}", f"refs/remotes/{remote}/{work}"):
        resolved = git("rev-parse", "--verify", f"{ref}^{{commit}}")
        if resolved is not None and resolved.returncode == 0:
            work_tip, work_ref = resolved.stdout.strip(), ref
            break
    unmoved = False
    if work_ref == f"refs/heads/{work}":
        # A branch just cut, or one that only fast-forwarded, is an ancestor of the base like a
        # merged one; only a reflog entry that committed on it tells them apart — the slot
        # reclaim's own rule. A remote-only ref was pushed, so it is judged by ancestry alone.
        from quenching.git.slots import has_own_work
        unmoved = not has_own_work(root, work)
    base_ref = None
    for ref in (f"refs/remotes/{remote}/{base}", f"refs/heads/{base}"):
        resolved = git("rev-parse", "--verify", f"{ref}^{{commit}}")
        if resolved is not None and resolved.returncode == 0:
            base_ref = ref
            break
    if work_tip and base_ref and not unmoved:
        ancestor = git("merge-base", "--is-ancestor", work_tip, base_ref)
        if ancestor is not None and ancestor.returncode == 0:
            return True, f"work ref is in {base_ref}"
    number = epic.pr_number(info) if ask_pr else None
    if number:
        pr = epic._pr_view(info, epic._provider(root), root, 15)
        if pr and pr[0] == "MERGED":
            return True, f"PR #{number} merged"
    if unmoved:
        return False, f"{work} has no commits of its own and no merged PR is recorded"
    return False, f"{work} is not in {remote}/{base} and no merged PR is recorded"


def cmd_promote(args, root: str, out: Emitter) -> int:
    """The one remaining transition: `plans/` -> `archive/`, closing a spec out.

    v3 has a single hop. The `backlog/` -> `ready/` promote is gone with the folders — it
    recorded that a human said go, and that is now `approved:` in frontmatter — with `by:`
    naming whose authority it carries — which no file move is needed to express. What is left refuses (exit 2) with the missing list
    rather than warning, because a gate that warns is not a gate.

    The file is MOVED, never renamed: the date prefix was stamped at capture and the
    basename is the spec's identity for its whole lifecycle. Git detects the rename by
    content, so `git log --follow` reads as one history without this tool shelling out."""
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    info, err = read_one(backend, args.spec, out)
    if err:
        return out.emit_err(args.json, err)
    dest = args.to or _next_phase(info["phase"])
    if not dest:
        out.emit(args.json,
                 {"ok": False, "code": "sp-terminal-phase", "id": info["id"],
                  "phase": info["phase"],
                  "message": f"'{info['id']}' is already in {info['phase']}/ — "
                             f"nowhere to promote"},
                 f"refused: '{info['id']}' is already in {info['phase']}/")
        return 2
    if dest not in PHASES:
        out.emit(args.json, {"ok": False, "code": "sp-unknown-phase", "phase": dest,
                             "message": f"'{dest}' is not a phase folder"},
                 f"error: '{dest}' is not a phase folder")
        return 2
    if dest == info["phase"]:
        out.emit(args.json,
                 {"ok": False, "code": "sp-same-phase", "id": info["id"], "phase": dest,
                  "message": f"'{info['id']}' is already in phase {dest}"},
                 f"refused: '{info['id']}' is already in phase {dest}")
        return 2

    gates = gate_report(info, dest)
    # A present-but-empty section is malformed in ANY phase (`validate` reports it as an error
    # everywhere), so the entry gate's own list is not the whole of it: an archived spec must not
    # carry a heading that is neither an answer nor a not-yet, whichever section it is.
    for h in canonical_headings():
        if section_state(info["sections"], h) == "empty" and h not in gates["malformed"]:
            gates["malformed"].append(h)
            gates["ok"] = False
    # A heading written twice is read as its FIRST copy only, so the gate cannot vouch for the
    # second; `--force` does not reach it either (it only waives open tasks, below).
    dups = duplicate_headings(body_after_frontmatter(info["text"]))
    if dups:
        out.emit(args.json,
                 {"ok": False, "code": "sp-duplicate-heading", "id": info["id"],
                  "duplicates": dups,
                  "message": f"cannot promote '{info['id']}' to {dest}/ — "
                             f"{', '.join('## ' + h for h in dups)} appears more than once; "
                             "rewrite each with `cq specs section` (the rewrite drops the copies)"},
                 f"refused: cannot promote '{info['id']}' to {dest}/ — duplicate heading "
                 f"{', '.join('## ' + h for h in dups)}")
        return 2
    if not gates["ok"]:
        obj = {"ok": False, "code": "sp-gate-unmet", "id": info["id"],
               "from": info["phase"], "to": dest,
               "missing": gates["missing"], "malformed": gates["malformed"],
               "message": f"cannot promote '{info['id']}' to {dest}/ — "
                          f"{len(gates['missing'])} missing, {len(gates['malformed'])} empty"}
        human = [f"refused: cannot promote '{info['id']}' to {dest}/"]
        if gates["missing"]:
            human += [f"  missing:   ## {h}" for h in gates["missing"]]
        if gates["malformed"]:
            human += [f"  empty:     ## {h}  (write `- none — <reason>` or fill it)"
                      for h in gates["malformed"]]
        out.emit(args.json, obj, "\n".join(human))
        return 2

    outcome = None
    if dest == "archive":
        outcome = args.outcome or "done"
        if outcome not in OUTCOMES:
            out.emit(args.json, {"ok": False, "code": "sp-bad-outcome", "outcome": outcome,
                                 "message": f"--outcome must be one of {', '.join(OUTCOMES)}"},
                     f"error: --outcome must be one of {', '.join(OUTCOMES)}")
            return 2
        # An abandoned spec is EXPECTED to have open tasks — refusing there would make
        # every abandonment a forced promote. Only `done` has to be true.
        # `task --descope` deliberately closes its box as `[x] … — descoped: …` and writes
        # the reason into `## Outcome`; it is therefore a truthful closed task here, unlike
        # `[!]`, which remains open and still refuses a `done` archive.
        open_tasks = [t for t in info["tasks"] if not t["checked"]]
        ids = [i["spec"] for i in parse_items(info) if i["spec"]]
        total_items = len(info["tasks"]) + len(ids)
        if ids:
            # A composite's spec items are never ticked: what is open is what the members have
            # not finished. Ordinary tasks in the same document stay in the count.
            members, unreadable = read_members(backend, ids, root)
            open_tasks += [{"id": i["label"], "text": f"{i['label']} {i['title']}",
                            "state": i["status"]}
                           for i in open_items(info, members, pr_states(members, root),
                                               unreadable)]
        if outcome == "done" and open_tasks and not args.force:
            out.emit(args.json,
                     {"ok": False, "code": "sp-open-tasks", "id": info["id"],
                      "open": len(open_tasks), "total": total_items,
                      "openTasks": [{"id": t["id"], "text": t["text"], "state": t["state"]}
                                    for t in open_tasks],
                      "message": f"{len(open_tasks)} of {total_items} tasks still open — "
                             f"pass --force, or --outcome abandoned"},
                     f"refused: {len(open_tasks)} of {total_items} tasks still open in "
                     f"'{info['id']}'\n" +
                     "\n".join(f"  [{t['state']}] {t['text'][:70]}" for t in open_tasks[:8]) +
                     "\n  pass --force to archive anyway, or --outcome abandoned")
            return 2

        if outcome == "done":
            merged, reason = merge_evidence(info, root)
            if not merged:
                branch = info["frontmatter"]["branch"]
                message = (f"cannot archive '{info['id']}' as done before its work is merged — "
                           f"{reason}; write ## Outcome, open the PR, and archive after the "
                           "merge (cq specs reconcile)")
                out.emit(args.json,
                         {"ok": False, "code": "sp-not-merged", "id": info["id"],
                          "work": branch.get("work"), "base": branch.get("base"),
                          "reason": reason, "message": message},
                         f"refused: {message}")
                return 2

    if args.dry_run:
        out.emit(args.json,
                 {"ok": True, "dryRun": True, "id": info["id"], "from": info["folder"],
                  "to": dest, "outcome": outcome, "warn": gates["warn"]},
                 f"dry-run: would promote {info['id']}: {info['folder']} → {dest}" +
                 (f"  (outcome: {outcome})" if outcome else ""))
        return 0
    # NO OCCUPIED-DESTINATION CHECK HERE ANY MORE. This verb used to join the DECLARED root
    # with `dest` and call `os.path.exists` on it — a question only a filesystem can answer,
    # asked by shared code that does not know whether one is involved. Under `github` or
    # `azure-boards` the path never exists and the check always passed; under `files` with the
    # specs worktree in use it measured a directory the backend does not write to. "Destination
    # taken" belongs to the backend to answer, and it raises `BackendRefusal` —
    # carried to exit 2 by `main`'s single conversion, like every other backend refusal, since
    # no command body ever branched on the old `sp-dest-exists` exit 1.
    #
    # The outcome is stamped BEFORE the hop, so the document that moves already carries it —
    # a backend whose move is not atomic must never be able to land an archived spec with no
    # outcome on it.
    if outcome:
        backend.write_spec(info, set_frontmatter_key(info["text"], "outcome", outcome))
    try:
        backend.move_spec(info, dest)
    except BackendRefusal as refusal:
        if outcome:
            # The first step is already on the spec; saying so keeps the retry honest.
            refusal.err["outcomeWritten"] = True
            refusal.err["message"] = (f"{refusal.err.get('message', 'the move failed')} — the "
                                      f"outcome '{outcome}' was ALREADY written to "
                                      f"'{info['id']}' and the spec did not move; re-run promote")
        raise
    failures = card_failures(backend)
    out.emit(args.json,
             {"ok": True, "id": info["id"], "from": info["folder"], "to": dest,
              "outcome": outcome, "warn": gates["warn"],
              **({"cardFailures": failures} if failures else {})},
             f"promoted {info['id']}: {info['folder']} → {dest}" +
             (f"  (outcome: {outcome})" if outcome else "") +
             ("".join(f"\n  warning: ## {h} is empty" for h in gates["warn"])) +
             card_warnings(failures))
    return 0
