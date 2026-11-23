"""Let Codex write one eligible spec task inside a worktree — and prove by code it touched nothing else.

The task is judged again by `eligibility` (the quota included), so a caller cannot dispatch what
`cq codex eligible` would refuse. The argv is fixed here: `--dangerously-bypass-approvals-and-sandbox
--ignore-user-config --ignore-rules -m <model> -c model_reasoning_effort=<effort> -C <worktree>
--json`. Codex's native sandbox is never asked for; the bounds are the plugin's. The environment
loses `GH_TOKEN`, `GITHUB_TOKEN` and every `AWS_*`, `AZURE_*` and `GOOGLE_*` variable; the prompt
forbids git and any write outside the task's `files:`.

The git guard is read before and after: the worktree's own `HEAD`, branch, reflog length
and stash ref (never another worktree's `HEAD`), the base checkout's `HEAD` and the primary branch
ref (a fast-forward is accepted; a rewind, a branch switch, a detached base `HEAD` or a removed
ref is a divergence), and `git status` contained in `files:`. A divergence is
exit 2 with the offending paths. With `outside` non-empty (paths `files:` never declared) Claude
may not clean them: it stops the task as `needs-human` naming them. A `git status` that fails,
before or after the run, is exit 2 `codex-git-guard` with `statusUnreadable: true` and no
`discard`: what Codex wrote is unknown, so the task stops as `needs-human` too. A read of the
worktree's refs that fails, before the run (Codex never starts) or after it, is the same exit 2
with `refsUnreadable: true` and no `discard`. A failing `git rev-parse --show-toplevel` is the
`codex-worktree-not-toplevel` refusal. With `outside` empty (a moved
`HEAD`, branch, reflog or stash) Claude discards the diff inside `files:` (never `reset`, `stash`
or `checkout`) and writes the task itself.

The quota is probed again afterwards. A primary window that moved by more than
`stopLossPercent` points sets `stopLoss`: the diff stands, and the executor stays off for the rest
of the execution.

    exit 0  Codex finished and its diff stays inside `files:` — Claude verifies, reviews, commits
    exit 1  fall back to Claude: ineligible, Codex failed or timed out
    exit 2  refusal (missing worktree, prompt, spec, task or `codex`, a dirty tree outside
            `files:`), or `codex-git-guard`: the guard saw a divergence or could not read it
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time

from quenching.codex.eligible import eligibility, repo_path
from quenching.codex.guard import (BYPASS, GuardError, base_moves, base_state, clean_env,
                                   git_strict, refs, touched)
from quenching.codex.probe import group_kwargs, probe, stop
from quenching.specs.backends import open_backend
from quenching.specs.commands.estimate import estimate_task

RUN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
NOT_MEASURED = "not measured"

RULE = (
    "Regras do executor: escreva apenas nos arquivos listados abaixo, dentro deste diretório. "
    "Não rode nenhum comando git (nem commit, stash, reset, checkout, branch ou push) e não "
    "escreva fora do diretório de trabalho. Não rode testes que gastem cota de agentes. "
    "O lado Claude verifica, revisa e faz o commit.")
WAY_OUT = ("Claude may not clean these paths (no reset, stash or checkout) and files: never "
           "declared them: stop the task as needs-human naming them, so the human cleans the tree.")
STATUS_WAY_OUT = ("git status failed, so what Codex wrote cannot be proved inside files: discard "
                  "nothing and stop the task as needs-human, so the human repairs and cleans the "
                  "tree.")
REFS_WAY_OUT = ("the worktree refs cannot be read, so whether Codex moved HEAD, the branch, the "
                "reflog or the stash cannot be proved: discard nothing and stop the task as "
                "needs-human, so the human repairs the checkout.")


def build_argv(executor: dict, model: str, worktree: str, message_path: str) -> list[str]:
    return ["exec", BYPASS, "--ignore-user-config", "--ignore-rules", "-m", model,
            "-c", f"model_reasoning_effort={executor['reasoningEffort']}", "-C", worktree,
            "--json", "-o", message_path, "-"]


def _last_usage(node, found: list) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "total_token_usage" and isinstance(value, dict):
                found.append(value)
            else:
                _last_usage(value, found)
    elif isinstance(node, list):
        for item in node:
            _last_usage(item, found)


def _add_turn(totals: dict, usage: dict) -> None:
    for key, value in usage.items():
        if isinstance(value, int) and not isinstance(value, bool):
            totals[key] = totals.get(key, 0) + value


def measured_tokens(events_path: str):
    """The usage of every `turn.completed` that `codex exec --json` printed, summed.

    Without one, the last `total_token_usage` of the rollout format; without either,
    `not measured`. `total_tokens` is input plus output, as the event does not carry it.
    """
    turns: dict = {}
    found: list = []
    try:
        with open(events_path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("type") == "turn.completed" \
                        and isinstance(event.get("usage"), dict):
                    _add_turn(turns, event["usage"])
                else:
                    _last_usage(event, found)
    except OSError:
        return NOT_MEASURED
    if turns:
        turns["total_tokens"] = turns.get("input_tokens", 0) + turns.get("output_tokens", 0)
        return turns
    return found[-1] if found else NOT_MEASURED


def refusal(code: str, message: str) -> tuple[dict, int]:
    return {"ok": False, "code": code, "message": message}, 2


def primary_used(quota: dict | None):
    window = ((quota or {}).get("rateLimits") or {}).get("primary") or {}
    return window.get("usedPercent")


def read_task(root: str, spec: str, task_id: str
              ) -> tuple[dict | None, dict | None, tuple[dict, int] | None]:
    """The task, the spec it was read from (for `estimate_task`), or the refusal."""
    backend, err = open_backend(root)
    if err:
        return None, None, refusal("codex-specs-unreadable",
                                   err.get("message", "the spec store could not be opened"))
    info, err = backend.read_spec(spec)
    if info is None:
        return None, None, refusal("codex-spec-missing",
                                   (err or {}).get("message", f"no spec {spec}"))
    task = next((t for t in info.get("tasks", []) if t["id"] == task_id), None)
    if task is None:
        return None, None, refusal("codex-task-missing", f"spec {spec} has no task {task_id}")
    return task, info, None


def run_implement(root: str, settings: dict, spec: str, task_id: str, worktree: str,
                  prompt: str, run: str | None, env: dict | None = None,
                  quota=None) -> tuple[dict, int]:
    """`quota` replaces the probe before and after, so tests never reach Codex."""
    env = dict(os.environ if env is None else env)
    quota = quota or probe
    worktree, prompt = os.path.realpath(worktree), os.path.abspath(prompt)
    if not os.path.isdir(worktree):
        return refusal("codex-worktree-missing", f"worktree does not exist: {worktree}")
    try:
        toplevel = git_strict(worktree, "rev-parse", "--show-toplevel").strip()
    except GuardError:
        toplevel = ""
    if not toplevel or os.path.realpath(toplevel) != worktree:
        return refusal("codex-worktree-not-toplevel",
                       f"worktree is not the top level of a git checkout: {worktree}")
    if not os.path.isfile(prompt):
        return refusal("codex-prompt-missing", f"prompt file does not exist: {prompt}")
    task, info, refused = read_task(root, spec, task_id)
    if refused:
        return refused
    files = sorted(repo_path(f) for f in task["files"])
    try:
        dirty = sorted(touched(worktree) - set(files))
    except GuardError as error:
        return {"ok": False, "fallback": False, "code": "codex-git-guard",
                "statusUnreadable": True,
                "message": f"the guard cannot read git status before the run: {error}. "
                           f"{STATUS_WAY_OUT}",
                "reason": f"git guard: {error}"}, 2
    if dirty:
        return refusal("codex-tree-dirty", "the tree is dirty outside files: "
                       + ", ".join(dirty) + f". {WAY_OUT}")
    binary = shutil.which("codex", path=env.get("PATH"))
    if binary is None:
        return refusal("codex-missing", "codex is not on PATH")
    run = run or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    if not RUN_NAME.match(run):
        return refusal("codex-run-name", f"run name must match {RUN_NAME.pattern}: {run}")
    run_dir = os.path.join(root, ".quenching", "codex", run)
    if os.path.exists(run_dir):
        return refusal("codex-run-exists", f"run directory already exists: {run_dir}")

    judged, code = eligibility(task, settings, worktree, lambda s: quota(s, env=env),
                               estimate_task(info, task_id, worktree))
    executor = settings["executor"]
    out = {"ok": False, "fallback": True, "spec": str(spec), "task": task_id, "files": files,
           "class": judged["class"], "model": judged["model"],
           "run": run, "runDir": None, "argv": None, "exitCode": None, "timedOut": False,
           "tokens": None, "quotaBefore": primary_used(judged["quota"]), "quotaAfter": None,
           "quotaDelta": None, "stopLoss": False, "guard": None, "outside": []}
    if code != 0:
        return {**out, "reason": judged["reason"]}, 1

    os.makedirs(run_dir)
    prompt_path = os.path.join(run_dir, "prompt.md")
    message_path = os.path.join(run_dir, "last-message.md")
    events_path = os.path.join(run_dir, "events.jsonl")
    with open(prompt, encoding="utf-8") as handle:
        text = handle.read().rstrip("\n")
    with open(prompt_path, "w", encoding="utf-8") as handle:
        handle.write(f"{text}\n\n{RULE}\n\nArquivos: {', '.join(files)}\n")
    argv = build_argv(executor, judged["model"], worktree, message_path)
    try:
        before = refs(worktree)
    except GuardError as error:
        shutil.rmtree(run_dir, ignore_errors=True)
        return {**out, "fallback": False, "code": "codex-git-guard", "refsUnreadable": True,
                "message": f"the guard cannot read the worktree refs before the run: {error}. "
                           f"{REFS_WAY_OUT}",
                "reason": f"git guard: {error}"}, 2
    try:
        base_before = base_state(worktree)
    except GuardError as error:
        shutil.rmtree(run_dir, ignore_errors=True)
        return {**out, "fallback": False, "code": "codex-git-guard",
                "message": f"the guard cannot read the base checkout before the run: {error}",
                "reason": f"git guard: {error}"}, 2
    with open(prompt_path, encoding="utf-8") as stdin, \
            open(events_path, "w", encoding="utf-8") as stdout, \
            open(os.path.join(run_dir, "stderr.log"), "w", encoding="utf-8") as stderr:
        process = subprocess.Popen([binary, *argv], stdin=stdin, stdout=stdout, stderr=stderr,
                                   cwd=worktree, env=clean_env(env), **group_kwargs())
        try:
            exit_code = process.wait(timeout=settings["timeoutSeconds"])
        except subprocess.TimeoutExpired:
            stop(process)
            exit_code, out["timedOut"] = process.returncode, True
    refs_error = None
    try:
        after = refs(worktree)
    except GuardError as error:
        after, refs_error = {}, error
    base_after = base_before
    try:
        base_after = base_state(worktree, base_before["primary"])
        base_bad = base_moves(worktree, base_before, base_after)
    except GuardError as error:
        base_bad = [f"base unreadable ({error})"]
    out.update(runDir=run_dir, argv=["codex", *argv], exitCode=exit_code,
               tokens=measured_tokens(events_path))
    out["quotaAfter"] = primary_used(quota(settings, env=env))
    if out["quotaBefore"] is not None and out["quotaAfter"] is not None:
        out["quotaDelta"] = out["quotaAfter"] - out["quotaBefore"]
        out["stopLoss"] = out["quotaDelta"] > executor["stopLossPercent"]

    if refs_error is not None:
        out["guard"] = {"moved": [f"worktree refs unreadable ({refs_error})"],
                        "before": {**before, "base": base_before},
                        "after": {"base": base_after}}
        return {**out, "fallback": False, "code": "codex-git-guard", "refsUnreadable": True,
                "message": f"the git guard cannot read the worktree refs after the run: "
                           f"{refs_error}. {REFS_WAY_OUT}",
                "reason": f"git guard: {refs_error}"}, 2
    try:
        out["outside"] = sorted(touched(worktree) - set(files))
    except GuardError as error:
        out["guard"] = {"moved": [f"worktree status unreadable ({error})"],
                        "before": {**before, "base": base_before},
                        "after": {**after, "base": base_after}}
        return {**out, "fallback": False, "code": "codex-git-guard", "statusUnreadable": True,
                "message": f"the git guard cannot read git status after the run: {error}. "
                           f"{STATUS_WAY_OUT}",
                "reason": f"git guard: {error}"}, 2
    moved = sorted(key for key in before if before[key] != after[key]) + base_bad
    if moved or out["outside"]:
        out["guard"] = {"moved": moved, "before": {**before, "base": base_before},
                        "after": {**after, "base": base_after}}
        detail = "; ".join(filter(None, [moved and "moved: " + ", ".join(moved),
                                         out["outside"] and "outside files: "
                                         + ", ".join(out["outside"])]))
        way_out = f" {WAY_OUT}" if out["outside"] else ""
        return {**out, "fallback": False, "code": "codex-git-guard",
                "discard": files,
                "message": f"the git guard saw a divergence — {detail}.{way_out}",
                "reason": f"git guard: {detail}"}, 2
    if out["timedOut"]:
        reason = f"codex ran past {settings['timeoutSeconds']}s and was stopped"
    elif exit_code != 0:
        reason = f"codex exited {exit_code}"
    else:
        reason = "codex wrote the task"
    ok = not out["timedOut"] and exit_code == 0
    return {**out, "ok": ok, "fallback": not ok, "reason": reason}, 0 if ok else 1
