"""`cq git conventions` — the one read every command in the `git` pillar runs once before its
first commit, over BOTH layers a target may declare: the directives it addressed to the plugin in
`.agents/quenching.json`'s `shared.gitConventions`, and the standard it published under
`docs/standards/git/**`. `.agents/quenching/references/git/conventions.md` §The declared-directive layer and
§The read-if-present rule are the contract; this is their one mechanical reading, so five commands
stop repeating the same `ls`, frontmatter peek and config load.

Reports FACTS ONLY — which directives are declared, which files exist, and each file's own
`authority`. Whether a partial document covers commits but not merges is a reading a human (or the
citing command body) does over what comes back; this stays a report for the same reason the rest of
the `git` pillar carries no `doctor`.

`governs` and `declared` answer the DOCS layer and nothing else, unchanged. A single word over
three layers would have to lie in the ordinary case — the config names two artifacts and the
target's own doc covers the rest — so the config arrives as its own two fields instead."""
from __future__ import annotations

import os

from quenching.common.frontmatter import parse_frontmatter
from quenching.common.config import load_config, namespace
from quenching.common.io import read_text
from quenching.common.output import emit

STANDARDS_DIR = os.path.join("docs", "standards", "git")
GIT_CONVENTION_KEYS = ("commitSubject", "branchName", "prTitle", "prBody", "mergeSubject")


def load_git_conventions(root: str) -> dict:
    """Read and validate only the shared directives owned by the git pillar."""
    envelope = load_config(root, detect_provider_info=False)
    raw = namespace(envelope, "shared").get("gitConventions")
    conventions: dict[str, str] = {}
    unknown: list[str] = []
    bad: list[str] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            if not (isinstance(key, str) and key.strip() in GIT_CONVENTION_KEYS):
                if isinstance(key, str) and key.strip():
                    unknown.append(key.strip())
                continue
            if isinstance(value, str) and value.strip():
                conventions[key.strip()] = value.strip()
            else:
                bad.append(key.strip())
    return {"config": conventions, "configUnknown": sorted(unknown),
            "configBad": sorted(bad), "migrationRefusal": envelope.get("migrationRefusal")}


def _declared_docs(cwd: str) -> list[dict]:
    git_dir = os.path.join(cwd, STANDARDS_DIR)
    if not os.path.isdir(git_dir):
        return []
    docs = []
    for name in sorted(os.listdir(git_dir)):
        # `index.md` is the OKF bundle's own reserved layer listing, in every
        # `standards/<subject>/` folder — a convention ABOUT the folder, never one of its docs.
        if not name.endswith(".md") or name == "index.md":
            continue
        rel = f"{STANDARDS_DIR}/{name}"
        fm = parse_frontmatter(read_text(os.path.join(git_dir, name)) or "")
        docs.append({"path": rel, "authority": fm.get("authority")})
    return docs


def cmd_conventions(args) -> int:
    cwd = os.getcwd()
    docs = _declared_docs(cwd)
    # `detect_provider_info=False`: the config is read here for five prose directives, and the
    # provider probe behind that flag shells out to `git remote` to answer a question this verb
    # never asks.
    cfg = load_git_conventions(cwd)
    governs = "target" if docs else "defaults"
    lines = []
    if cfg["config"]:
        lines.append("declared in .agents/quenching.json:")
        lines += [f"  gitConventions.{key}: {value}"
                  for key, value in sorted(cfg["config"].items())]
    if docs:
        lines.append("governs: the target's own conventions")
        lines += [f"  {d['path']} (authority: {d['authority'] or 'unstated'})" for d in docs]
    else:
        lines.append("governs: the plugin's defaults — nothing declared under " + STANDARDS_DIR)
    emit(args.json, {"ok": True, "governs": governs, "declared": docs,
                     "config": cfg["config"], "configUnknown": cfg["configUnknown"],
                     "configBad": cfg["configBad"]}, "\n".join(lines))
    return 0
