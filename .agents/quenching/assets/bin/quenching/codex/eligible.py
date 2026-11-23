"""Whether one spec task may be written by Codex, decided by code — never by the caller's taste.

A task is eligible when the executor is enabled, it declares `files:`, none of them lies under
`docs/` or `knowledge/`, and it is either **small** (the bytes of its existing `files:` over four
stay within `maxTaskTokens`) or **verified** (it carries a `verify:` command). Small wins when both
hold, and the class picks the model (`modelSmall` or `modelVerified`). A verified task whose
`cq specs estimate --task` reports `oracleProtected` and `smallTierEligible` takes the small tier
(`modelSmallTier`, ADR 0014) instead.

The quota is asked last, because it is the only check that starts a process: the probe must
answer `available` under every ceiling lowered by `reservePercent`, so a dispatch never spends the
margin the human kept.

    exit 0  eligible: `class`, `model`, `reasoningEffort` in the payload
    exit 1  not eligible: `reason` names the first rule that failed — Claude writes the task
    exit 2  refusal: the spec or the task does not exist
"""
from __future__ import annotations

import os

from quenching.codex.probe import probe
from quenching.common.paths import repo_path
from quenching.specs.backends import open_backend
from quenching.specs.commands.estimate import estimate_task

BYTES_PER_TOKEN = 4
WRITTEN_KNOWLEDGE = ("docs", "knowledge")


def task_tokens(root: str, files: list[str]) -> int:
    """Bytes over four of every declared file that exists; a file still to be created costs 0."""
    total = 0
    for entry in files:
        path = os.path.join(root, repo_path(entry))
        if os.path.isfile(path):
            total += os.path.getsize(path)
    return total // BYTES_PER_TOKEN


def reserved(settings: dict) -> dict:
    """The settings the probe judges against: every ceiling lowered by `reservePercent`."""
    reserve = settings["executor"]["reservePercent"]
    return {**settings,
            "maxUsedPercent": {window: max(0, ceiling - reserve)
                               for window, ceiling in settings["maxUsedPercent"].items()}}


def eligibility(task: dict, settings: dict, root: str, quota=None,
                small_tier: dict | None = None) -> tuple[dict, int]:
    """Judge one parsed task; `quota` replaces the probe, so tests never reach Codex.

    `small_tier` is the task's `estimate_task` row: a verified task it reports protected and
    eligible runs on `modelSmallTier`; anything else keeps `modelVerified`.
    """
    executor = settings["executor"]
    out = {"ok": False, "task": task["id"], "class": None, "model": None,
           "reasoningEffort": executor["reasoningEffort"], "tokens": None, "maxTaskTokens": executor["maxTaskTokens"], "quota": None}

    def no(reason: str) -> tuple[dict, int]:
        out["reason"] = reason
        return out, 1

    if not executor["enabled"]:
        return no("shared.codex.executor.enabled is false")
    if task["checked"] or task["blocked"]:
        return no(f"task {task['id']} is not open")
    if task.get("operational"):
        return no("files: (operational) — the task writes no repository file, so there is "
                  "nothing for Codex to write")
    files = [repo_path(f) for f in task["files"]]
    if not files:
        return no("the task declares no files:")
    knowledge = [f for f in files if f.split("/", 1)[0] in WRITTEN_KNOWLEDGE]
    if knowledge:
        return no(f"the task writes docs/ or knowledge/: {', '.join(knowledge)}")
    out["tokens"] = task_tokens(root, files)
    if out["tokens"] <= executor["maxTaskTokens"]:
        out["class"], out["model"] = "small", executor["modelSmall"]
    elif task["verify"]:
        protected = bool(small_tier and small_tier["oracleProtected"]
                         and small_tier["smallTierEligible"])
        out["class"] = "verified"
        out["model"] = executor["modelSmallTier" if protected else "modelVerified"]
    else:
        return no(f"the task is large ({out['tokens']} > {executor['maxTaskTokens']} tokens) "
                  "and has no verify:")
    quota_state = (quota or probe)(reserved(settings))
    out["quota"] = quota_state
    if quota_state["state"] != "available":
        return no(f"codex quota {quota_state['state']} under the "
                  f"{executor['reservePercent']:g}% reserve: {quota_state['reason']}")
    out.update(ok=True, reason=f"{out['class']} task; {quota_state['reason']}")
    return out, 0


def run_eligible(root: str, settings: dict, spec: str, task_id: str,
                 quota=None) -> tuple[dict, int]:
    backend, err = open_backend(root)
    if err:
        return {"ok": False, "code": "codex-specs-unreadable",
                "message": err.get("message", "the spec store could not be opened")}, 2
    info, err = backend.read_spec(spec)
    if info is None:
        return {"ok": False, "code": "codex-spec-missing",
                "message": (err or {}).get("message", f"no spec {spec}")}, 2
    task = next((t for t in info.get("tasks", []) if t["id"] == task_id), None)
    if task is None:
        return {"ok": False, "code": "codex-task-missing",
                "message": f"spec {spec} has no task {task_id}"}, 2
    payload, code = eligibility(task, settings, root, quota, estimate_task(info, task_id, root))
    payload["spec"] = str(spec)
    return payload, code
