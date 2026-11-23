"""Azure PR completion behind the steward's fixed `cq git pr merge` grant.

An approved build validation policy certifies CI; approvals alone do not. The PATCH pins the
source commit and replaces completion options, so an old squash/bypass setting cannot leak in.
Unlike `az repos pr update`, it never re-reads and silently substitutes a newer source commit.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import unquote

from quenching.common.output import FINDINGS, OK, emit
from quenching.git.pull import POLL_S, _az
from quenching.git.push import _specs_note
from quenching.specs.commands.push import sync_store
from quenching.specs.config import find_repo_root


AZURE_PR_URL = re.compile(
    r"https://(?:(?P<modern>dev\.azure\.com/[\w.-]+)|"
    r"(?P<legacy>[\w.-]+\.visualstudio\.com)(?:/DefaultCollection)?)/"
    r"(?P<project>[\w.%~-]+)/_git/(?P<repo>[\w.%~-]+)/pullrequest/(?P<number>[1-9]\d*)")
BUILD_POLICY = "0609b952-1397-4640-95ec-e00a01b2c241"
#: The Azure DevOps application id `az rest` asks a token for.
AZURE_DEVOPS_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"


def _read(argv: list[str], kind: type):
    code, out, err = _az(*argv)
    try:
        data = json.loads(out) if code == 0 else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, kind):
        return None, (err or out).strip() or "the provider returned no readable evidence"
    return data, ""


def _checks(number: int, org: str) -> tuple[str, list[str], str]:
    policies, message = _read(["repos", "pr", "policy", "list", f"--id={number}",
                               f"--org={org}", "--output=json"], list)
    if policies is None:
        return "checks-unreadable", [], message
    pending, failed, skipped = [], [], []
    certified = False
    for policy in policies:
        if not isinstance(policy, dict) or not isinstance(policy.get("configuration"), dict):
            return "checks-unreadable", [], "malformed policy evaluation"
        config = policy["configuration"]
        if config.get("isEnabled") is False:
            continue
        policy_type = config.get("type")
        if (not isinstance(policy_type, dict) or config.get("isEnabled") is not True
                or not isinstance(config.get("isBlocking"), bool)):
            return "checks-unreadable", [], "unreadable policy configuration"
        build = policy_type.get("id") == BUILD_POLICY
        if not build and config.get("isBlocking") is False:
            continue
        name = str(policy_type.get("displayName") or config.get("id") or "policy")
        status = policy.get("status")
        context = policy.get("context", {})
        if context is None:
            context = {}
        if not isinstance(context, dict):
            return "checks-unreadable", [], "malformed policy context"
        if status == "approved" and build and context.get("isExpired") is True:
            pending.append(name)
        elif status == "approved":
            certified |= build
        elif status == "rejected":
            failed.append(name)
        elif status in {"queued", "running"}:
            pending.append(name)
        elif status == "notApplicable":
            if build and config.get("isBlocking") is not False:
                skipped.append(name)
        else:
            return "checks-unreadable", [name], f"unreadable policy status: {status}"
    if failed:
        return "failed", sorted(failed), ""
    if skipped:
        return "skipped-required", sorted(skipped), ""
    if pending:
        return "pending", sorted(pending), ""
    return ("green" if certified else "no-checks"), [], ""


def merge_azure(args, match: re.Match) -> int:
    number = int(match["number"])
    org = "https://" + (match["modern"] or match["legacy"])
    # URL segments remain encoded in the route; decode them once for az's URL builder.
    route = ["devops", "invoke", f"--org={org}", "--area=git", "--resource=pullRequests",
             "--route-parameters", f"project={unquote(match['project'])}",
             f"repositoryId={unquote(match['repo'])}", f"pullRequestId={number}",
             "--api-version=7.1", "--output=json"]
    payload = {"ok": False, "provider": "azure-boards", "url": args.url, "number": number}

    def finish(reason: str | None = None, message: str = "", **extra) -> int:
        payload.update(extra)
        if reason:
            payload.update(reason=reason)
            if message:
                payload["message"] = message
        else:
            payload["ok"] = True
            spec = getattr(args, "spec", None)
            if spec:
                from quenching.git.pull import _stamp_merge

                branch = str(pr["sourceRefName"]).removeprefix("refs/heads/")
                target = str(pr["targetRefName"]).removeprefix("refs/heads/")
                payload["merge"] = stamp = _stamp_merge(
                    spec, number, args.url,
                    subject=f"Merge pull request {number} from {branch} into {target}")
                if not stamp["ok"]:
                    payload.update(ok=False, reason="stamp-failed", message=stamp["message"])
                    emit(args.json, payload, f"merged {args.url}; `merge` record not stamped: "
                         f"{stamp['message']}")
                    return FINDINGS
            payload["specs"] = sync_store(find_repo_root(os.getcwd()))
        emit(args.json, payload, f"not merged: {reason} {message}".rstrip() if reason
             else f"merged {args.url} (noFastForward)" + _specs_note(payload["specs"]))
        return FINDINGS if reason else OK

    pr, message = _read(route + ["--http-method=GET"], dict)
    if pr is None:
        return finish("merge-state", message)
    if pr.get("pullRequestId") != number:
        return finish("merge-state", "the provider returned another pull request")
    if pr.get("status") == "completed":
        return finish(alreadyMerged=True)
    source = pr.get("lastMergeSourceCommit") or {}
    head = source.get("commitId") if isinstance(source, dict) else None
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", head):
        return finish("merge-state", "source commit unreadable")
    if not all(isinstance(pr.get(key), str) and pr[key].startswith("refs/heads/")
               for key in ("sourceRefName", "targetRefName")):
        return finish("merge-state", "source or target branch unreadable")
    deadline = time.monotonic() + args.wait
    while True:
        state, names, message = _checks(number, org)
        if state != "pending" or time.monotonic() + POLL_S > deadline:
            break
        print(f"checks pending; re-reading in {POLL_S}s", file=sys.stderr, flush=True)
        time.sleep(POLL_S)
    payload.update(checks=state, notGreen=names)
    if state == "no-checks" and getattr(args, "allow_no_checks", False):
        payload["noChecksAccepted"] = True
    elif state != "green":
        return finish(state, message)
    now, message = _read(route + ["--http-method=GET"], dict)
    if now is None:
        return finish("merge-state", message)
    if now.get("pullRequestId") == number and now.get("status") == "completed":
        return finish(alreadyMerged=True)
    if (now.get("pullRequestId") != number or now.get("status") != "active"
            or now.get("isDraft") is not False or now.get("mergeStatus") != "succeeded"
            or now.get("sourceRefName") != pr.get("sourceRefName")
            or now.get("targetRefName") != pr.get("targetRefName")
            or now.get("lastMergeSourceCommit") != source):
        return finish("merge-state", "head, target, draft or merge status changed or is unclean")
    body = {"status": "completed", "lastMergeSourceCommit": {"commitId": head},
            "completionOptions": {"mergeStrategy": "noFastForward", "squashMerge": False,
                                  "bypassPolicy": False, "deleteSourceBranch": False,
                                  "transitionWorkItems": False}}
    with tempfile.TemporaryDirectory(prefix="cq-pr-merge-") as scratch:
        path = Path(scratch) / "completion.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        # `az devops invoke` resolves `pullRequests` to the project-scoped location, which is
        # read-only (405 on PATCH); completion lives under the repository, reached by `az rest`.
        update = (f"{org}/{match['project']}/_apis/git/repositories/{match['repo']}"
                  f"/pullrequests/{number}?api-version=7.1")
        code, out, err = _az("rest", "--method=patch", f"--url={update}",
                             f"--resource={AZURE_DEVOPS_RESOURCE}", f"--body=@{path}",
                             "--output=json")
    if code != 0:
        late, _ = _read(route + ["--http-method=GET"], dict)
        if (late is not None and late.get("pullRequestId") == number
                and late.get("status") == "completed"):
            return finish(alreadyMerged=True)
        return finish("merge-refused", (err or out).strip())
    # Completion is asynchronous. Never retire a branch merely because PATCH was accepted.
    while True:
        done, message = _read(route + ["--http-method=GET"], dict)
        if done is None or done.get("pullRequestId") != number:
            return finish("merge-state", message or "completion unreadable")
        if (done.get("lastMergeSourceCommit") != source
                or done.get("targetRefName") != pr.get("targetRefName")):
            return finish("merge-state", "source commit or target changed during completion")
        if done.get("status") == "completed":
            return finish()
        if done.get("status") != "active" or done.get("mergeStatus") not in {
                "queued", "notSet", "succeeded"}:
            return finish("merge-refused", str(done.get("mergeFailureMessage") or
                                               "provider did not complete the pull request"))
        if time.monotonic() + POLL_S > deadline:
            return finish("pending", "completion queued; confirm MERGED before cleanup")
        time.sleep(POLL_S)
