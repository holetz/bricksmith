"""`release` — the mechanical half of a release: bump, commit, tag."""
from __future__ import annotations

import os
from pathlib import Path

from quenching.common.git import _git, _git_run, origin_head_branch
from quenching.common.config import load_config
from quenching.common.modules import distribution_findings, load_manifest
from quenching.specs.commands.output import Emitter
from quenching.specs.config import infer_base_branch
from quenching.specs.release import (
    SEMVER_RE, bump_release_artifacts, dirty_paths, regenerate_copies, repo_release_artifacts,
)


def cmd_release(args, root: str, out: Emitter) -> int:
    """Move the plugin's version-carrying artifacts to ONE new version, commit them,
    and tag that commit — the MECHANICAL half of a release. Judging what the number should
    be, whether a lone PR on the primary branch is a release or a habit, and the
    publication itself all belong to the command that calls this; see
    /docs/standards/git/branching.md.

    Refuses (exit 2) rather than guessing: a version not shaped X.Y.Z, a repository that is
    not this plugin's own checkout, a lockstep already disagreeing with itself, or a
    version that changes nothing."""
    new_version = args.new_version
    if not SEMVER_RE.match(new_version):
        return out.emit_err(args.json, {"code": "sp-release-bad-version", "exit": 2,
                                        "version": new_version,
                                        "message": f"'{new_version}' is not shaped X.Y.Z"})

    repo = _git(os.getcwd(), "rev-parse", "--show-toplevel").strip()
    if not repo:
        return out.emit_err(args.json, {"code": "sp-release-no-repo", "exit": 2,
                                        "message": "not inside a git repository"})

    try:
        artifacts = repo_release_artifacts(repo)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return out.emit_err(args.json, {"code": "sp-release-not-plugin-repo", "exit": 2,
                                      "message": f"module manifest refused: {exc}"})
    missing = [rel for rel, _ in artifacts
              if not os.path.isfile(os.path.join(repo, rel))]
    if missing:
        return out.emit_err(args.json, {"code": "sp-release-not-plugin-repo", "exit": 2,
                                        "missing": missing,
                                        "message": "this is not the plugin's own repository — "
                                                   "missing: " + ", ".join(missing)})

    # The bump must land on the PRIMARY branch: the release is a deliberate local act that
    # publishes from the branch the repository publishes to, so committing the bump from
    # any other checkout would tag a number the primary branch never received. The repo
    # above came from `os.getcwd()`; this guards which branch that checkout is on. The
    # primary branch resolves the same chain `infer_base_branch` declares: `origin/HEAD`,
    # else `init.defaultBranch`, else `main`.
    cfg = load_config(root)
    init_default = _git(repo, "config", "init.defaultBranch").strip() or None
    primary = infer_base_branch(cfg, origin_head_branch(repo), init_default)
    current = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if current != primary:
        return out.emit_err(args.json, {"code": "sp-release-wrong-branch", "exit": 2,
                                        "branch": current, "primaryBranch": primary,
                                        "message": f"this checkout is on '{current}', not the "
                                               f"primary branch '{primary}' — the "
                                               f"bump must be committed on the primary "
                                               f"branch so the release tag carries it"})

    dirty = [p for p in dirty_paths(repo) if p != "CHANGELOG.md"]
    if dirty:
        return out.emit_err(args.json, {"code": "sp-release-dirty", "exit": 2, "paths": dirty,
                                        "message": "the tree has changes besides CHANGELOG.md "
                                                   "— the release commit carries only the "
                                                   "release: " + ", ".join(dirty)})

    result = bump_release_artifacts(repo, new_version)
    if not result["ok"]:
        return out.emit_err(args.json, {"code": "sp-release-drift", "exit": 2,
                                        "message": result["error"]})

    failed = regenerate_copies(repo)
    if failed:
        return out.emit_err(args.json, {"code": "sp-release-regenerate-failed", "exit": 2,
                                        "message": f"regeneration failed at {failed} — the bump "
                                                   "is uncommitted in the tree; no commit, no tag"})

    # Generators must not remove a package's delivery route between the preflight and the tag.
    try:
        findings = distribution_findings(Path(repo), load_manifest(Path(repo)))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        findings = [str(exc)]
    if findings:
        return out.emit_err(args.json, {"code": "sp-release-distribution-failed", "exit": 2,
                                        "message": "; ".join(findings)
                                        + " — the bump is uncommitted; no commit, no tag"})

    # The tree was clean but for the CHANGELOG, so everything dirty now belongs to the release.
    code, _, err = _git_run(repo, "add", "-A")
    if code != 0:
        return out.emit_err(args.json, {"code": "sp-release-git-failed", "exit": 2,
                                        "step": "add", "message": f"git add failed: {err}"})
    subject = f"release: {result['oldVersion']} -> {new_version}"
    code, _, err = _git_run(repo, "commit", "-m", subject)
    if code != 0:
        return out.emit_err(args.json, {"code": "sp-release-git-failed", "exit": 2,
                                        "step": "commit", "message": f"git commit failed: {err}"})
    # `git commit`'s own output is the summary, not a hash — report HEAD's bare hash so the
    # release command's self-check can compare it to `rev-parse <version>^{commit}`.
    commit_hash = _git(repo, "rev-parse", "HEAD").strip()
    code, _, err = _git_run(repo, "tag", "-a", new_version, "-m", subject)
    if code != 0:
        return out.emit_err(args.json, {"code": "sp-release-git-failed", "exit": 2,
                                        "step": "tag", "message": f"git tag failed: {err}"})

    out.emit(args.json,
             {"ok": True, "oldVersion": result["oldVersion"], "newVersion": new_version,
              "branch": primary, "commit": commit_hash,
              "artifacts": result["artifacts"], "tag": new_version, "subject": subject},
             f"release: {result['oldVersion']} -> {new_version}, tagged {new_version}")
    return 0
