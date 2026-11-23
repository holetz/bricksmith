"""`cq git audit` — the facts the `spec-verifier` agent checks inside a spec's worktree, read with a
fixed argv so the agent needs no `Bash(git …)` grant at all.

A wildcard grant such as `Bash(git -C * status:*)` admits any git command that merely ends in
`status` — `branch -D`, `clean -fdx`, `-c core.fsmonitor=<cmd>` — and `git log:*` admits
`--output=<file>`. Here the agent names a worktree and refs; every git invocation is built in this
module, refs are resolved to commits before use, and the repository's own config cannot run a
command during the read: the `filter.<x>` drivers it declares are blanked before `status` through
`GIT_CONFIG_COUNT`, which carries a key containing `=` that `-c key=value` would split, and `status`
does not descend into submodules (their own config is not ours to blank); every diff and show
read carries `--no-ext-diff --no-textconv`, so neither `diff.external` nor a `diff.<x>.textconv`
driver runs; `log.showSignature` is forced off so `gpg.program` never runs; and a partial clone's lazy fetch is off
(`GIT_NO_LAZY_FETCH`, and `GIT_ALLOW_PROTOCOL` empty refuses every transport), so a missing blob
or an absent `--sha` never runs `core.sshCommand` or any other transport the config names.
`rewrites` and `reflog` are evidence of what the worker did not erase, not proof against a worker with
`Bash`: `git reflog delete` or `expire` removes the entries and the payload then reads empty with
`complete: true`. The audit, like check 9 of the spec-verifier, catches the widening a worker that follows the
protocol makes by mistake; it is not a sandbox against one who evades it on purpose (a worker with
free `Bash` writes outside any diff anyway). A REPORT only, exit 0 on any facts, and a read that failed is
an entry of the payload's `errors` (`complete: false`), never an empty list; judging them is the spec-verifier's.
An agent, command or skill is read under `.claude/`, under `quenching/` and `integrations/<one segment>/`, and under every
plugin root the base or the tip declares (a `.claude-plugin/plugin.json`, a relative `source` of a
`.claude-plugin/marketplace.json`, and the component paths either names), never by an `agents/` at
any depth, so an OKF standard filed under `standards/agents/` is no grant. A file a CI root of the
base or the tip includes (GitLab `include`, top level or under a job's `trigger:`, Azure `template:`,
`@self` included, GitHub `uses: ./`, where `./` is the whole repository), transitively, is
surface like the root itself. `generated` is the part of `changed` that is a byte-for-byte copy of its
source file under a module the BASE's `quenching/assets/modules.json` declares (same relative
path, same blob at the tip, or absent from both); derived outputs that are no copy are never in it.
Nothing here executes the
audited branch's code: the gate is certified by CI before the merge, never by this verb."""
from __future__ import annotations

import fnmatch
import json
import os
import posixpath
import re
import shlex
from quenching.common.git import _git_run
from quenching.common.output import emit, refuse

FILTER_KEY = re.compile(r"^filter\.(.+)\.(clean|smudge|process)$", re.S)
SAFE = ("--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
        "-c", "log.showSignature=false")
NO_FETCH = {"GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": ""}


def _run(cwd: str, *argv: str, env: dict[str, str] | None = None) -> tuple[int, str]:
    """One read. With no `env` the lazy fetch is still off: a caller that forgot to pass the inert
    environment (`cq git worktree`, `push`, through `_commit`) never reaches a transport."""
    code, out, _err = _git_run(cwd, *SAFE, *argv, env=env or {**os.environ, **NO_FETCH})
    return code, out


def _lines(cwd: str, *argv: str, env: dict[str, str] | None = None,
           errors: list[dict] | None = None) -> list[str]:
    """The non-empty stdout lines of one read. A read that FAILS (non-zero exit, a timeout, no git)
    is not an empty list: it is appended to `errors` as `{read, code, message}` so the payload says
    the fact is unknown. Without `errors` the caller opted out of that and gets `[]`."""
    code, out, err = _git_run(cwd, *SAFE, *argv, env=env)
    if code != 0:
        if errors is not None:
            errors.append(_error(argv, code, err))
        return []
    return [line for line in out.splitlines() if line]


def _paths(cwd: str, rng: str, env: dict[str, str], errors: list[dict]) -> list[str]:
    """The paths a range changes, read with `-z` so a non-ASCII path arrives raw, never quoted."""
    code, out, err = _git_run(cwd, *SAFE, "diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z", "--no-renames", rng, env=env)
    if code != 0:
        errors.append(_error(("diff", "--name-only"), code, err))
        return []
    return [p for p in out.split("\0") if p]


def _error(argv: tuple[str, ...], code: int, err: str) -> dict:
    lines = [line for line in (err or "").strip().splitlines() if line.strip()]
    return {"read": " ".join(argv[:2]), "code": code, "message": lines[0] if lines else ""}


def _reflog(cwd: str, ref: str, env: dict[str, str] | None = None,
            errors: list[dict] | None = None) -> list[str]:
    """The reflog entries of `ref` that MOVED it, newest first. The check judges movement, not
    message: an entry whose sha equals the next older entry's moved nothing and is dropped whatever
    it says (`git merge --abort` logs `reset: moving to HEAD`); every other entry stays, and one
    with an empty message (`git update-ref` without `-m`) reads `(no reflog message) <old>..<new>`,
    so a rewrite made silently is still seen. The oldest entry is the ref's creation and stays."""
    entries = [(line.split(" ", 1) + [""])[:2]
               for line in _lines(cwd, "reflog", "show", "--format=%H %gs", ref, env=env, errors=errors)]
    if not entries and errors is not None:   # a live ref always has its creation entry: none means expired or never logged
        errors.append(_error(("reflog", "show", ref), 0, "reflog empty or expired: the ref's history is unknown, not clean"))
    kept = []
    for i, (sha, subject) in enumerate(entries):
        older = entries[i + 1][0] if i + 1 < len(entries) else None
        if older == sha:
            continue
        kept.append(subject or f"(no reflog message) {(older or '')[:12]}..{sha[:12]}")
    return kept


def _since_switch(entries: list[list[str]], branch: str, held: set[str]) -> list[list[str]]:
    """The `HEAD` reflog entries (newest first) that belong to the audited branch's life: those newer
    than the last `checkout: moving from <x> to <branch>` whose new sha the branch ref itself held
    (`held`, the shas of its own reflog). A slot is reused across specs, so what came before that
    switch is another branch's history. A forged subject whose sha the branch never held is no
    boundary, so a hand-made move cannot hide what precedes it. No such switch keeps every entry."""
    want = "checkout: moving from "
    for i, (sha, subject) in enumerate(entries):
        if subject.startswith(want) and subject.endswith(f" to {branch}") and sha in held:
            return entries[:i]
    return entries


def _own_commits(entries: list[list[str]], branch: str) -> set[str]:
    """The shas `HEAD` committed while it sat on `branch`, read from the `HEAD` reflog (newest first)
    by following the `checkout: moving from <x> to <y>` subjects. Before the first checkout the
    current branch is that checkout's `<x>`, or `branch` itself when there is none: a worktree
    starts on the branch it was cut for."""
    ordered = entries[::-1]
    current = branch
    for _sha, subject in ordered:
        m = re.match(r"checkout: moving from (.+) to .+$", subject)
        if m:
            current = m.group(1)
            break
    own = set()
    for sha, subject in ordered:
        m = re.match(r"checkout: moving from .+ to (.+)$", subject)
        if m:
            current = m.group(1)
        elif subject.startswith("commit") and current == branch:
            own.add(sha)
    return own


def _discarded_by_switch(cwd: str, entries: list[list[str]], i: int, branch: str,
                         env: dict[str, str] | None, errors: list[dict] | None) -> str:
    """The switch onto `branch` at entry `i` is a slot's boundary, but one that throws the branch's own
    commits away (`checkout --detach; branch -D X; checkout -b X <older>`) is a rewrite: where `HEAD`
    stood before the switch descends strictly from where it lands and some commit between them was
    made on `branch`. A previous branch ahead of the landing point owns its commits, so it is no
    finding. Returns the item for `rewrites.head`, or `""`."""
    if i + 1 >= len(entries):
        return ""
    new, old = entries[i][0], entries[i + 1][0]
    if new == old:
        return ""
    code, out, err = _git_run(cwd, *SAFE, "rev-list", f"{new}..{old}", env=env)
    if code != 0:
        if errors is not None:
            errors.append(_error(("rev-list",), code, err))
        return ""
    between = set(out.split())
    if not between:
        return ""
    dropped = between & _own_commits(entries, branch)
    if not dropped:
        return ""
    return (f"{entries[i][1]} {old[:12]}..{new[:12]} (discards {len(dropped)} commit(s) of {branch})")


def _rewrites(cwd: str, ref: str, env: dict[str, str] | None = None,
              errors: list[dict] | None = None, branch: str | None = None) -> list[str]:
    """The reflog moves of `ref` that are not fast-forwards: the older sha is not an ancestor of the
    newer one. The verdict is the graph's, so a rewrite made with no message or a forged one
    (`update-ref -m "commit: tidy"`) is still listed. The oldest entry is the creation and has no
    older sha, so it is never one. Each item reads `<subject or (no reflog message)> <old>..<new>`.
    With `branch`, `ref` is `HEAD` and only the moves since its last switch onto that branch count
    (`_since_switch`): the switch itself moves `HEAD` between unrelated tips and is no rewrite of the work."""
    entries = [(line.split(" ", 1) + [""])[:2]
               for line in _lines(cwd, "reflog", "show", "--format=%H %gs", ref, env=env)]
    if branch is not None:
        held = {line.split(" ", 1)[0] for line in
                _lines(cwd, "reflog", "show", "--format=%H %gs", f"refs/heads/{branch}", env=env)}
        kept = _since_switch(entries, branch, held)
        discarded = ""
        if len(kept) < len(entries):
            discarded = _discarded_by_switch(cwd, entries, len(kept), branch, env, errors)
            entries = entries[:len(kept) + 1]          # the switch entry stays as the oldest anchor; the loop never lists it
    found = []
    if branch is not None and discarded:
        found.append(discarded)
    for i, (sha, subject) in enumerate(entries[:-1]):
        older = entries[i + 1][0]
        if older == sha:
            continue
        code, _out, err = _git_run(cwd, *SAFE, "merge-base", "--is-ancestor", older, sha, env=env)
        if code > 1:
            if errors is not None:
                errors.append(_error(("merge-base", "--is-ancestor"), code, err))
            continue
        if code == 1:
            found.append(f"{subject or '(no reflog message)'} {older[:12]}..{sha[:12]}")
    return found


def _registered(cwd: str, path: str) -> bool:
    code, out = _run(cwd, "worktree", "list", "--porcelain")
    if code != 0:
        return False
    want = os.path.realpath(path)
    return any(os.path.realpath(line[len("worktree "):]) == want
               for line in out.splitlines() if line.startswith("worktree "))


def _commit(cwd: str, ref: str, env: dict[str, str] | None = None) -> str | None:
    if not ref or ref.startswith("-"):
        return None
    code, out = _run(cwd, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}",
                     env=env)
    return out.strip() if code == 0 and out.strip() else None


def _branch_ok(cwd: str, branch: str) -> bool:
    if branch.startswith("-"):
        return False
    return _run(cwd, "check-ref-format", "--branch", branch)[0] == 0


def _inert_env(cwd: str, errors: list[dict] | None = None) -> dict[str, str]:
    """The environment every read of this verb runs under: the lazy fetch off, and every
    `filter.<x>` driver the repository config declares blanked as a `GIT_CONFIG_COUNT` pair, so the
    status cannot run one against the branch's `.gitattributes` whatever `<x>` contains.
    `required` is cleared too: a required filter with no command would die instead of being
    skipped. The pairs are appended after any the caller already passes."""
    env = {**os.environ, **NO_FETCH}
    code, out = _run(cwd, "config", "-z", "--name-only", "--get-regexp",
                     r"^filter\..*\.(clean|smudge|process)$", env=env)   # 1: no such key, a fact
    if code not in (0, 1) and errors is not None:   # the filter list is unknown, not empty
        errors.append(_error(("config", "--get-regexp"), code, "filter config unreadable: the drivers could not be blanked"))
    try:
        n = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        n = 0
    for key in out.split("\0") if code == 0 else []:
        m = FILTER_KEY.match(key)
        if m:
            for k, v in ((key, ""), (f"filter.{m.group(1)}.required", "false")):
                env[f"GIT_CONFIG_KEY_{n}"], env[f"GIT_CONFIG_VALUE_{n}"] = k, v
                n += 1
    env["GIT_CONFIG_COUNT"] = str(n)
    return env


LOADED_ROOT = r"^(?:(?:.*/)?(?:\.claude|\.agents)/|(?:.*/)?(?:quenching|integrations/[^/]+)/)?"   # where the host loads agents, commands and skills from
FRONTMATTER_GRANT = re.compile(LOADED_ROOT + r"(?:(?:agents|commands)/.+\.md|skills/.+/skill\.md)$", re.I)
AGENT = re.compile(LOADED_ROOT + r"agents/.+\.md$", re.I)
MANIFEST = re.compile(r"(?:^|/)\.(?:claude|codex)-plugin/(plugin|marketplace)\.json$")
COMPONENT_KEYS = ("agents", "commands", "skills")
SURFACE_KEYS = ("hooks", "mcpServers", "lspServers")   # a path a manifest declares here is surface
SYMLINK = "120000"
SETTINGS_AT = r"(?:^|/)(?:\.claude|\.agents)/settings[^/]*\.json$"   # an alternation, never the literal: the Codex translation rewrites it
SETTINGS = re.compile(SETTINGS_AT)
# the one azure pipeline template the plugin distributes, split so it is not read as a path citation
DOCS_TEMPLATE = re.escape("/".join(("quenching", "assets", "zensical", "azure-pipelines-docs.yml")))
SURFACE = re.compile(r"^\.github/(?:workflows|actions)/|" + SETTINGS_AT + r"|(^|/)hooks/"
                     r"|(^|/)\.(?:claude|codex)-plugin/[^/]+\.json$|(^|/)\.(mcp|lsp)\.json$"
                     r"|(^|/)(?:\.gitlab-ci|bitbucket-pipelines)\.ya?ml$"
                     r"|^(?!" + DOCS_TEMPLATE + r"$)(?:.*/)?azure-pipelines[^/]*\.ya?ml$"   # the one template the plugin distributes is not this repo's CI
                     r"|(^|/)\.(?:circleci|buildkite)/")
GRANT_KEYS = ("tools", "allowed-tools")
DENY_KEYS = ("disallowedTools",)
DOCS = re.compile(r"^docs/")


def _declared(base: str, value) -> list[str]:
    """The relative paths a manifest field names (a string or a list of strings), joined to `base`
    and normalized; one that leaves the repository is dropped."""
    items = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    out = []
    for item in items:
        if not isinstance(item, str) or not item.strip() or item.startswith("/"):
            continue
        path = posixpath.normpath(posixpath.join(base, item))
        if path != ".." and not path.startswith("../"):
            out.append("" if path == "." else path)
    return out


def _plugin_components(root: str, manifest) -> list[tuple[str, str]]:
    """`(kind, prefix)` for one plugin root: its default `agents/`, `commands/`, `skills/` and every
    component path its manifest entry declares (a `.md` path is one file, anything else a folder),
    plus `("surface", path)` for every path it declares under `SURFACE_KEYS` (the path itself and
    anything under it)."""
    prefix = f"{root}/" if root else ""
    found = [(kind, prefix + kind + "/") for kind in COMPONENT_KEYS]
    if isinstance(manifest, dict):
        for kind in COMPONENT_KEYS:
            for path in _declared(root, manifest.get(kind)):
                found.append((kind, path if path.lower().endswith(".md") else (f"{path}/" if path else "")))
        for key in SURFACE_KEYS:
            found += [("surface", path) for path in _declared(root, manifest.get(key)) if path]
    return found


def _plugin_roots(cwd: str, revs: tuple[str, ...], env: dict[str, str],
                  errors: list[dict]) -> list[tuple[str, str]]:
    """Every `(kind, prefix)` a plugin of the base or the tip loads agents, commands and skills from,
    whatever the path's shape: a folder holding `.claude-plugin/plugin.json` is a root even when its
    JSON does not parse (only the declared component paths are lost then), and so is every relative
    `source` of a `.claude-plugin/marketplace.json` (under `metadata.pluginRoot` when set). A tree
    that cannot be listed is an `errors` entry: the roots are unknown, never empty."""
    found: list[tuple[str, str]] = []
    for rev in revs:
        code, out, err = _git_run(cwd, *SAFE, "ls-tree", "-r", "--name-only", "-z", rev, env=env)
        if code != 0:
            errors.append(_error(("ls-tree", rev), code, err))
            continue
        for path in out.split("\0"):
            m = MANIFEST.search(path)
            if not m:
                continue
            here = posixpath.dirname(posixpath.dirname(path))
            code, text, _e = _git_run(cwd, *SAFE, "show", "--no-textconv", f"{rev}:{path}", env=env)
            try:
                data = json.loads(text) if code == 0 else None
            except ValueError:
                data = None
            if m.group(1) == "plugin":
                found += _plugin_components(here, data)
                continue
            if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
                continue
            meta = data.get("metadata")
            prefix = (_declared(here, meta.get("pluginRoot")) if isinstance(meta, dict) else []) or [here]
            for entry in data["plugins"]:
                source = entry.get("source") if isinstance(entry, dict) else None
                for root in _declared(prefix[0], source) if isinstance(source, str) else []:
                    found += _plugin_components(root, entry)
    return found


CI_ROOT = re.compile(r"(?:^|/)(?P<gitlab>\.gitlab-ci\.ya?ml)$|^(?!" + DOCS_TEMPLATE + r"$)(?:.*/)?(?P<azure>azure-pipelines[^/]*\.ya?ml)$"
                     r"|^(?P<github>\.github/(?:workflows|actions)/)")
CI_KEY = re.compile(r"^\s*(?:-\s+)?(local|template|uses):\s*(.+)$")
GITLAB_INCLUDE = re.compile(r"^\s*include:\s*(.*)$")
TRIGGER = re.compile(r"^\s*trigger:\s*(?:#.*)?$")
MAPPING = re.compile(r"^[\w-]+:(?:\s|$)")
CI_READS = 256   # files read per revision: a cycle or a huge tree stops here, as an `errors` entry


def _ci_value(value: str) -> str:
    """A YAML scalar of one line, without its trailing comment and its quotes."""
    return re.sub(r"\s+#.*$", "", value.strip()).strip().strip("'\"")


def _under(path: str, ref: str) -> bool:
    """Whether `path` is `ref`, lies under it, or matches it as a glob; `.` is the whole repository."""
    return ref == "." or path == ref or path.startswith(ref + "/") or fnmatch.fnmatchcase(path, ref)


def _ci_refs(kind: str, path: str, text: str) -> list[str]:
    """The repository paths one CI file of `kind` includes, read by line: GitLab `include:` at the
    top level or under a job's `trigger:` (a string, a list of strings, `local:`; only the list's
    first level is a path, never what `rules:`/`exists:`/`inputs:` nest under an item;
    `remote:`/`project:` leave the repository), Azure `template:` (relative to the file, `/…` to the
    root, `…@self` this repository, `…@repo` another one) and GitHub `uses: ./…` (`./` the whole
    repository). A path may be a glob (GitLab) or a folder (GitHub)."""
    found = []
    block = item = trigger = None   # indents: the open `include:`, its first list item, the last bare `trigger:`
    for line in text.split("\n"):
        body = line.strip()
        if not body or body.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        if block is not None and (indent < block or indent == block and not body.startswith("-")):
            block = item = None
        if trigger is not None and indent <= trigger:
            trigger = None
        m = GITLAB_INCLUDE.match(line) if kind == "gitlab" else None
        if m and (indent == 0 or trigger is not None):
            block, item = indent, None
            inline = _ci_value(m.group(1))
            if inline.startswith("[") and inline.endswith("]"):
                found += [_ci_value(v) for v in inline[1:-1].split(",")]
            elif inline and not MAPPING.match(inline):
                found.append(inline)
            continue
        if kind == "gitlab" and TRIGGER.match(line):
            trigger = indent
            continue
        m = CI_KEY.match(line)
        key, value = (m.group(1), _ci_value(m.group(2))) if m else (None, "")
        if kind == "gitlab" and block is not None and item is None:
            item = indent if body.startswith("- ") else -1   # a map's first line: no list, only `local:`
        if kind == "gitlab" and key == "local":
            found.append(value)
        elif kind == "gitlab" and block is not None and body.startswith("- ") and indent == item:
            entry = _ci_value(body[2:])
            if entry and not MAPPING.match(entry):
                found.append(entry)
        elif kind == "azure" and key == "template" and value:
            value = value[:-len("@self")] if value.endswith("@self") else value
            if value and "@" not in value:
                found.append(value if value.startswith("/") else posixpath.join(posixpath.dirname(path), value))
        elif kind == "github" and key == "uses" and value.startswith("./"):
            found.append(value)
    out = []
    for ref in found:
        ref = posixpath.normpath(ref.lstrip("/")) if ref.strip("/") else ""
        if ref and ref != ".." and not ref.startswith("../"):
            out.append(ref)
    return out


def _ci_includes(cwd: str, revs: tuple[str, ...], env: dict[str, str],
                 errors: list[dict]) -> list[tuple[str, str]]:
    """`("surface", path)` for every file a CI root of the base or the tip includes, transitively
    (an included YAML file is read in turn, as the kind of the root that reached it; of a folder a
    GitHub `uses:` names, only its `action.yml`/`action.yaml`). A path is the file, a folder
    (anything under it), a glob, or `.` (the whole repository). A tree that cannot be listed, a file that cannot
    be read, or more than `CI_READS` files is an `errors` entry: the inclusions are unknown."""
    found: list[tuple[str, str]] = []
    for rev in revs:
        code, out, err = _git_run(cwd, *SAFE, "ls-tree", "-r", "--name-only", "-z", rev, env=env)
        if code != 0:
            errors.append(_error(("ls-tree", rev), code, err))
            continue
        tree = [p for p in out.split("\0") if p]
        queue = [(p, next(k for k, v in m.groupdict().items() if v))
                 for p in tree for m in [CI_ROOT.search(p)] if m]
        seen = {p for p, _k in queue}
        reads = 0
        while queue:
            path, kind = queue.pop()
            if not path.endswith((".yml", ".yaml")):
                continue
            reads += 1
            if reads > CI_READS:
                errors.append(_error(("show", rev), 0, f"more than {CI_READS} CI files: the inclusions are unknown"))
                break
            code, text, err = _git_run(cwd, *SAFE, "show", "--no-textconv", f"{rev}:{path}", env=env)
            if code != 0:
                errors.append(_error(("show", path), code, err))
                continue
            for ref in _ci_refs(kind, path, text):
                found.append(("surface", ref))
                hits = [p for p in tree if _under(p, ref)]
                if kind == "github":
                    folder = "" if ref == "." else ref
                    hits = [p for p in hits if p == ref or p in (posixpath.join(folder, "action.yml"),
                                                                  posixpath.join(folder, "action.yaml"))]
                for hit in hits:
                    if hit not in seen:
                        seen.add(hit)
                        queue.append((hit, kind))
    return found


def _component(path: str, roots: list[tuple[str, str]]) -> tuple[bool, bool]:
    """`(grant-bearing frontmatter, agent)` for a path under one of `roots`: an agent or command
    `.md`, or a skill's `SKILL.md`, case-insensitively."""
    low, frontmatter, agent = path.lower(), False, False
    for kind, prefix in roots:
        if kind == "surface":
            continue
        pre = prefix.lower()
        if pre.endswith(".md"):
            hit = low == pre
        elif not low.startswith(pre):
            continue
        elif kind == "skills":
            rel = low[len(pre):]
            hit = rel == "skill.md" or rel.endswith("/skill.md")
        else:
            hit = low.endswith(".md")
        frontmatter = frontmatter or hit
        agent = agent or (hit and kind == "agents")
    return frontmatter, agent


def _split_entries(value: str) -> list[str]:
    """Comma-separated entries, a comma inside parentheses never separating."""
    out, depth, cur = [], 0, ""
    for ch in value:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return [e.strip("'\"") for e in out if e]


KNOWN_KEYS = GRANT_KEYS + DENY_KEYS
INERT_KEYS = ("name", "description", "argument-hint")   # grant, deny and change nothing: never `unknown`
TOP_KEY = re.compile(r"^([A-Za-z_-]+):(?:\s+(.*))?$")
LIST_ITEM = re.compile(r"^\s*-\s+")
SCALAR_HEAD = re.compile(r"^[>|][+-]?[0-9]?\s*$")   # a folded (`>-`) or literal (`|`) scalar indicator


PLAIN_BREAK = re.compile(r": |:$| #")   # inside a plain scalar these end it or start a comment/mapping


def _plain(body: list[str]) -> bool:
    """Whether a key's value is a plain scalar continued on the next lines (YAML folds them with a
    space): text inline, no flow list or quote, and every continuation an indented line that
    carries no `: ` or ` #`."""
    inline = (TOP_KEY.match(body[0]).group(2) or "").strip()
    return bool(inline and body[1:] and inline[0] not in "[\"'>|&*!%@`{"
                and not PLAIN_BREAK.search(inline)
                and all(ln[:1] in (" ", "\t") and not LIST_ITEM.match(ln) and not PLAIN_BREAK.search(ln)
                        for ln in body[1:]))


def _scalar(body: list[str]) -> bool:
    """Whether a key's value is a folded/literal scalar (the indicator alone on the key's line) or
    a plain scalar continued on the following lines."""
    return bool(SCALAR_HEAD.match((TOP_KEY.match(body[0]).group(2) or "").strip())) or _plain(body)


def _block(text: str | None) -> tuple[dict[str, list[str]], bool, tuple[str, ...]] | None:
    """The leading frontmatter block as `{key: [its lines]}`, whether every line was recognized,
    and the block's raw lines (an unrecognized line is in no key, so only these show it changed);
    `None` when there is no block. A leading BOM is dropped and lines split on `\\n` only, as the
    Claude Code reads them (`str.splitlines` also breaks on FF/VT/U+2028, hiding a grant behind a
    fake comment line). A top-level `key:` opens a key, the indented and
    `- item` lines after it belong to it, blank and `#` lines are skipped. Any other top-level line
    (a quoted key, a line with no `key:`), a repeated key, or a known key whose value is inline AND
    continued on the next line is not recognized: the audit computes only what it reads whole."""
    if text is None:
        return None
    lines = [ln.removesuffix("\r") for ln in text.removeprefix("\ufeff").split("\n")]
    if not lines or lines[0].strip() != "---":
        return None
    keys: dict[str, list[str]] = {}
    ok, cur, raw = True, None, []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        raw.append(line)
        if not line.strip() or line.lstrip().startswith("#"):
            if line.lstrip().startswith("#") and cur in keys and _scalar(keys[cur]):
                ok = False   # inside a scalar a `#` line is content, which this reader would skip
            continue
        m = TOP_KEY.match(line)
        if m:
            cur = m.group(1)
            if cur in keys:
                ok = False
            keys[cur] = [line]
        elif cur is not None and (line[:1] in (" ", "\t") or LIST_ITEM.match(line)):
            keys[cur].append(line)
        else:
            ok = False
    for body in filter(None, map(keys.get, KNOWN_KEYS)):
        inline = (TOP_KEY.match(body[0]).group(2) or "").strip()
        if _scalar(body):
            if (not body[1:] and not _plain(body)) or any(LIST_ITEM.match(ln) for ln in body[1:]):
                ok = False
        elif body[1:] and (inline or not all(LIST_ITEM.match(ln) for ln in body[1:])):
            ok = False
    return keys, ok, tuple(raw)


def _entries(keys: dict[str, list[str]], wanted: tuple[str, ...]) -> set[str] | None:
    """The entries of `wanted` in a recognized block, inline or as a block list; `None` when none
    of `wanted` appears."""
    found: set[str] | None = None
    for key in wanted:
        if key not in keys:
            continue
        found = found or set()
        head, *items = keys[key]
        value = (TOP_KEY.match(head).group(2) or "").strip()
        if _scalar(keys[key]):
            found.update(_split_entries(" ".join(([value] if _plain(keys[key]) else [])
                                                 + [i.strip() for i in items])))
            continue
        if value.startswith("[") and value.endswith("]"):
            value = value[1:-1]
        found.update(_split_entries(value))
        for item in items:
            found.update(_split_entries(LIST_ITEM.sub("", item)))
    return found


def _grant_entries(text: str | None, parsed: tuple | None, agent: bool) -> set[str]:
    """What `tools:` / `allowed-tools:` grant. An agent whose frontmatter has no `tools:` holds
    every tool, read as `*`; an absent file grants nothing."""
    if text is None:
        return set()
    found = _entries(parsed[0], GRANT_KEYS) if parsed else None
    if found is None:
        return {"*"} if agent else set()
    return found


def _unknown_keys(old: dict[str, list[str]], new: dict[str, list[str]]) -> list[str]:
    """Every key outside the known and the inert ones that the branch adds, removes or changes."""
    return sorted(k for k in set(old) | set(new)
                  if k not in KNOWN_KEYS + INERT_KEYS and old.get(k) != new.get(k))


def _deny_entries(path: str, text: str | None, key: str = "deny") -> set[str]:
    """The rules of a file: `permissions.<key>` (`deny` or `ask`) of a settings JSON,
    `disallowedTools` of a frontmatter (deny only). Raises ValueError on a settings file that is
    not a JSON object, whose `permissions` is not an object or whose `<key>` is not a list."""
    if text is None:
        return set()
    if SETTINGS.search(path):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        permissions = data.get("permissions", {})
        if not isinstance(permissions, dict):
            raise ValueError("permissions is not an object")
        rules = permissions.get(key, [])
        if not isinstance(rules, list):
            raise ValueError(f"permissions.{key} is not a list")
        return {str(e) for e in rules}
    if key != "deny":
        return set()
    parsed = _block(text)
    return (_entries(parsed[0], DENY_KEYS) if parsed else None) or set()


MODULES = "quenching/assets/modules.json"
NAMESPACED = re.compile(r"(?<![\w-])quenching:([a-z][a-z-]*)")


def _modules(cwd: str, base: str, env: dict[str, str],
             errors: list[dict]) -> tuple[str, list[str], dict[str, str]] | None:
    """The module manifest of the BASE, never the branch's: `(source, module roots, namespace map)`.
    The map sends each `quenching:<head>` head (an owned command front or root command, and every
    agent of the source when the module owns agents) to its owning module, as `namespace_map` of the
    generator does. Absent manifest -> None (nothing is declared); unreadable or a duplicate head ->
    an `errors` entry and None, so no relocation is ever accepted on a manifest the audit cannot read."""
    code, out, _err = _git_run(cwd, *SAFE, "show", f"{base}:{MODULES}", env=env)
    if code != 0:
        return None
    try:
        manifest = json.loads(out)
        source = manifest["source"].strip("/")
        # A schema-1 manifest (a base before spec 2160) still names published module trees.
        roots = [m["path"].strip("/") for m in manifest["modules"]
                 if m.get("published") and m.get("path") and ("generated" in m or "owned" in m)]
        agents: list[str] | None = None
        mapping: dict[str, str] = {}
        for module in manifest["modules"]:
            owned = module.get("owned")
            if not owned:
                continue
            heads = [rel.removesuffix(".md") for rel in owned.get("commands", [])]
            if owned.get("agents"):
                if agents is None:
                    c, listing, _e = _git_run(cwd, *SAFE, "ls-tree", "--name-only", base,
                                              f"{source}/agents/", env=env)
                    agents = [posixpath.basename(n)[:-3] for n in listing.splitlines()
                              if n.endswith(".md")] if c == 0 else []
                heads += sorted(agents)
            for head in heads:
                if head in mapping:
                    raise ValueError(f"duplicate namespace head: {head}")
                mapping[head] = module["name"]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append(_error(("show", MODULES), 0, f"unreadable module manifest: {exc}"))
        return None
    return source, roots, mapping


def _swap(text: str, mapping: dict[str, str]) -> str:
    """`quenching:<head>` -> `<module>:<head>` for the heads the BASE manifest assigns to a module;
    a head it does not know is left alone, so it can never be matched by a rewrite."""
    return NAMESPACED.sub(lambda m: f"{mapping[m.group(1)]}:{m.group(1)}"
                          if m.group(1) in mapping else m.group(0), text)


def _origin(path: str, view: tuple[str, list[str], dict[str, str]] | None) -> str | None:
    """The same relative path under the source, for a path inside a declared module root."""
    if view:
        source, roots, _map = view
        for root in roots:
            if root != source and path.startswith(root + "/"):
                return f"{source}/{path[len(root) + 1:]}"
    return None


def _grants(cwd: str, base: str, tip: str, changed: list[str], env: dict[str, str],
            errors: list[dict], generated: frozenset[str] | set[str] = frozenset()) -> list[dict]:
    """Every entry the branch ADDS to the grant surface against its merge-base with `base`:
    `tools:`/`allowed-tools:` of an agent, command or skill (an agent with no `tools:` is `*`), a
    deny rule removed (`kind: deny`), and the added lines of a settings file under `.claude/` or
    `.agents/` at any depth, a hook, a CI workflow (GitHub, GitLab, Azure Pipelines, Bitbucket,
    CircleCI, Buildkite), a local GitHub action under `.github/actions/`, a file a CI root includes
    (`_ci_includes`), a `.claude-plugin/*.json` or `.codex-plugin/*.json` manifest, a
    `.mcp.json`/`.lsp.json` server config, or a path a manifest declares under `hooks`, `mcpServers`
    or `lspServers`. The agents, commands and skills are the ones under `LOADED_ROOT` or a root
    `_plugin_roots` derives. What it does not
    understand fails closed as `kind: unknown`: any other frontmatter key added, removed or changed
    (its name), a line removed or rewritten in any of those surfaces — settings, hook, CI workflow,
    manifest or server config (`(removed or rewritten lines)`), a surface path changed with no
    `+`/`-` line read (`(no line read)`), a changed block it cannot read whole (`(unparsed frontmatter)`), and a sensitive
    file deleted (`(deleted)`), and every symlink the branch adds or repoints, at any path
    (`(symlink)`), its target never read. Unchanged never appears, nor a narrowing that removes no surface line.
    Two relocations are not a widening, and only on proof: a path in `generated` (a byte-for-byte copy of
    its source that exists at the tip) adds nothing the source does not hold, and the source is audited
    itself when it changed; and a `tools:` entry that is exactly the namespace rewrite
    (`quenching:<head>` -> `<module>:<head>`, by the BASE manifest) of an entry the old file held is not
    added. The old file of a new path in a module is its same relative path under the source at the fork."""
    code, out, err = _git_run(cwd, *SAFE, "merge-base", base, tip, env=env)
    if code != 0 or not out.strip():
        errors.append(_error(("merge-base", base), code, err or "no common ancestor"))
        return []
    fork = out.strip()
    code, out, err = _git_run(cwd, *SAFE, "diff", "--no-ext-diff", "--no-textconv", "--raw", "-z",
                              "--no-renames", f"{fork}..{tip}", env=env)
    if code != 0:
        errors.append(_error(("diff", "--raw"), code, err))
    fields = out.split("\0") if code == 0 else []
    symlinks = {fields[i + 1] for i in range(0, len(fields) - 1, 2)
                if fields[i].split(" ")[1:2] == [SYMLINK]}

    def show(rev: str, path: str) -> str | None:
        code, text, _e = _git_run(cwd, *SAFE, "show", "--no-textconv", f"{rev}:{path}", env=env)
        return text if code == 0 else None

    roots = _plugin_roots(cwd, (fork, tip), env, errors) + _ci_includes(cwd, (fork, tip), env, errors)
    view = _modules(cwd, base, env, errors)
    mapping = view[2] if view else {}
    found = []
    for path in changed:
        if path in symlinks:   # its target is never read: a link into the tree or a directory fails closed
            found.append({"path": path, "kind": "unknown", "added": ["(symlink)"]})
            continue
        if path in generated and show(tip, path) is not None:
            continue           # a reproduced copy: its source is audited by its own change
        by_root, agent_by_root = _component(path, roots)
        frontmatter = (FRONTMATTER_GRANT.search(path) or by_root) and not DOCS.match(path)
        surface = SURFACE.search(path) or any(kind == "surface" and _under(path, p) for kind, p in roots)
        if not (frontmatter or surface):
            continue
        new = show(tip, path)
        if new is None:
            found.append({"path": path, "kind": "unknown", "added": ["(deleted)"]})
            continue
        old = show(fork, path)
        origin = _origin(path, view) if old is None else None
        if origin:
            old = show(fork, origin)
        unparsed = False
        if frontmatter:
            agent = bool(AGENT.search(path)) or agent_by_root
            old_fm, new_fm = _block(old), _block(new)
            (old_keys, old_ok, old_raw), (new_keys, new_ok, new_raw) = (
                old_fm or ({}, True, ()), new_fm or ({}, True, ()))
            if old_raw != new_raw and not (old_ok and new_ok):
                unparsed = True
                found.append({"path": path, "kind": "unknown", "added": ["(unparsed frontmatter)"]})
            else:
                old_grants = _grant_entries(old, old_fm, agent)
                new_grants = _grant_entries(new, new_fm, agent)
                swapped = {_swap(e, mapping) for e in old_grants - new_grants}
                added = [] if "*" in old_grants else sorted(new_grants - old_grants - swapped)
                if added:
                    found.append({"path": path, "kind": "tools", "added": added})
                unknown = _unknown_keys(old_keys, new_keys)
                if unknown:
                    found.append({"path": path, "kind": "unknown", "added": unknown})
        if (frontmatter and not unparsed) or SETTINGS.search(path):
            for kind in ("deny", "ask"):
                try:
                    removed = sorted(_deny_entries(path, old, kind) - _deny_entries(path, new, kind))
                except ValueError as exc:
                    errors.append(_error(("show", path), 0, f"settings unreadable: {exc}"))
                    removed = []
                if removed:
                    found.append({"path": path, "kind": kind, "added": removed})
        if surface:
            # `--text`: a `-diff`/`binary` attribute (the base's or the branch's) would print no line
            code, out, err = _git_run(cwd, *SAFE, "diff", "--no-ext-diff", "--no-textconv", "--text",
                                      "-U0", "--no-renames", f"{base}...{tip}", "--", path, env=env)
            if code != 0:
                errors.append(_error(("diff", "-U0"), code, err))
                continue
            plus = [ln for ln in out.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
            added = [ln[1:].strip() for ln in plus if ln[1:].strip()]
            removed = any(ln.startswith("-") and not ln.startswith(("--- a/", "--- /dev/null"))
                          for ln in out.splitlines())
            if removed:
                found.append({"path": path, "kind": "unknown", "added": ["(removed or rewritten lines)"]})
            elif not plus:   # changed, yet no line read: a mode flip, an empty file, a diff with no lines
                found.append({"path": path, "kind": "unknown", "added": ["(no line read)"]})
            if added:
                found.append({"path": path, "kind": "surface", "added": added})
    return found


TEST_PATH = re.compile(r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]*$|_test\.[^/]+$|\.(test|spec)\.[^/]+$")
TEST_NAME = re.compile(r"(^|/)test_[^/]*$|_test\.[^/]+$|\.(test|spec)\.[^/]+$")
ASSERT = re.compile(r"\bassert\w*\b|\bexpect\s*\(")
GATE_PATH = re.compile(r"^scripts/verify_repo\.sh$|^scripts/toolchain/|^quenching/assets/checks/")
CODE_EXT = re.compile(r"\.(py|sh|js|jsx|ts|tsx|rb|go|rs|java|kt|cs)$")
TEST_DIRECT = re.compile(r"(^|/)(tests?|__tests__)/[^/]+$")
DISCOVER_WORD = re.compile(r"\b(discover|pytest|nosetests?|tox)\b")
TRIVIAL = re.compile(r"\bassert\s+True\s*$|\bexpect\s*\(\s*true\s*\)")
SUPPRESSION = re.compile(r"noqa|type:\s*ignore|\bskip(If|Unless|Test)?\b|\bxfail\b|\bxit\s*\(|\bxdescribe\s*\(")


def _read_discover(script: str) -> tuple[str, str] | None | bool:
    """The `(root, pattern)` of the first `unittest discover` in a gate script, comments and quotes
    ignored (`-s`/`--start-directory`/positional, `-p`/`--pattern`, defaults `.` and `test*.py`).
    `None`: the script names no test runner. `False`: it names one this reader does not recognise
    (pytest, an unexpanded variable, unbalanced quotes) -- inconclusive, never guessed."""
    named = False
    for line in script.replace("\\\n", " ").splitlines():
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            if DISCOVER_WORD.search(line.split("#", 1)[0]):
                named = True
            continue
        if not tokens:
            continue
        if "discover" in tokens and tokens[tokens.index("discover") - 1:tokens.index("discover")] == ["unittest"]:
            rest = tokens[tokens.index("discover") + 1:]
            root, pattern, i = None, "test*.py", 0
            while i < len(rest):
                t = rest[i]
                if t in ("-s", "--start-directory", "-p", "--pattern", "-t", "--top-level-directory") and i + 1 < len(rest):
                    if t in ("-s", "--start-directory"):
                        root = rest[i + 1]
                    elif t in ("-p", "--pattern"):
                        pattern = rest[i + 1]
                    i += 2
                    continue
                if t.startswith("--start-directory="):
                    root = t.split("=", 1)[1]
                elif t.startswith("--pattern="):
                    pattern = t.split("=", 1)[1]
                elif t.startswith("-"):
                    if t in ("-s", "--start-directory", "-p", "--pattern"):
                        return False
                elif root is None:
                    root = t
                i += 1
            root = "." if root is None else root
            if root in ("$ROOT", "${ROOT}", "$PWD", "${PWD}"):
                root = "."
            if "$" in root or "$" in pattern or "`" in root or "`" in pattern:
                return False
            return root, pattern
        if any(DISCOVER_WORD.fullmatch(t) for t in tokens):
            named = True
    return False if named else None


def _asserts(text: str) -> int:
    """Real assertions in `text`: a match outside a comment, and not `assert True`/`expect(true)`."""
    n = 0
    for line in text.splitlines():
        code = "" if re.match(r"\s*(#|//|\*|/\*)", line) else re.sub(r"\s#.*$", "", line)
        if ASSERT.search(code) and not TRIVIAL.search(code):
            n += 1
    return n


def _lowered(cwd: str, base: str, tip: str, env: dict[str, str], errors: list[dict]) -> list[dict]:
    """The ways the branch lowers the bar a gate measures, read from `base...tip`, never judged for
    justification. `severity: fail` — a test file deleted, a test file left with no assert it had at
    the base, a gate file (`scripts/verify_repo.sh`, `quenching/assets/checks/**`) deleted or
    with a removed non-comment line that no added line of that file repeats; `severity: warn` — a
    `noqa`, `type: ignore`, `skip`, `skipIf`, `skipUnless` or `xfail` added to a code file. A reworded
    gate step reads as shrunk: the conductor settles it, except a line whose `quenching:<head>`
    ids, rewritten by the BASE manifest's namespace map, are an added line verbatim."""
    code, out, err = _git_run(cwd, *SAFE, "diff", "--no-ext-diff", "--no-textconv", "--name-status",
                              "-z", "--no-renames", f"{base}...{tip}", env=env)
    if code != 0:
        errors.append(_error(("diff", "--name-status"), code, err))
        return []
    fields = [f for f in out.split("\0") if f]
    found = []

    def show(rev: str, path: str) -> str:
        c, text, _e = _git_run(cwd, *SAFE, "show", "--no-textconv", f"{rev}:{path}", env=env)
        return text if c == 0 else ""

    pairs = list(zip(fields[0::2], fields[1::2]))
    view = _modules(cwd, base, env, errors)
    mapping = view[2] if view else {}
    disc = _read_discover(show(tip, "scripts/verify_repo.sh"))
    root, pattern = disc if disc else (None, "test*.py")
    inconclusive = disc is False

    def discovered(path: str) -> bool:
        """Where the gate finds a test: a `.py` file the `unittest discover -s <root> -p <pattern>` of
        `scripts/verify_repo.sh` at the tip reaches; with no recognised command (or no gate), a test file of any language directly
        under a tests dir (and the result is declared inconclusive)."""
        if root is not None:
            if not path.endswith(".py"):
                return False
            return (posixpath.dirname(path) or ".") == posixpath.normpath(root) and fnmatch.fnmatch(posixpath.basename(path), pattern)
        return bool(TEST_DIRECT.search(path)) and bool(TEST_NAME.search(path)) and bool(CODE_EXT.search(path))

    added = [p for s_, p in pairs if s_.startswith("A") and discovered(p)]

    def is_test(path: str) -> bool:
        if TEST_NAME.search(path):
            return True
        return bool(TEST_PATH.search(path)) and bool(ASSERT.search(show(base, path)))

    gone = [p for s_, p in pairs if s_.startswith("D") and is_test(p)]
    had = {p: _asserts(show(base, p)) for p in gone}
    kept_n = {a: _asserts(show(tip, a)) for a in added}

    def base_name(path: str) -> str:
        return path.rsplit("/", 1)[-1]

    def candidates(g: str) -> list[str]:
        same = [a for a in added if base_name(a) == base_name(g)]
        return same or added                      # a same-name target is the only one that may absolve

    owner: dict[str, str] = {}                    # a target absolves ONE deletion: a one-to-one matching

    def place(g: str, seen: set[str]) -> bool:
        for a in candidates(g):
            if kept_n[a] >= had[g] and a not in seen:
                seen.add(a)
                if a not in owner or place(owner[a], seen):
                    owner[a] = g
                    return True
        return False

    for g in gone:
        place(g, set())
    paired = set(owner.values())
    if inconclusive and paired:
        found.append({"path": "scripts/verify_repo.sh", "severity": "warn", "kind": "discover-unrecognized",
                      "detail": "the test-discovery command was not recognised; a moved test was judged "
                                "by folder name, not by what the gate runs"})
    for status, path in pairs:
        gate = bool(GATE_PATH.search(path))
        deleted = status.startswith("D")
        test = path in gone if deleted else is_test(path)
        if deleted and test:                      # a move reads D plus A under --no-renames
            if path in paired:
                continue                          # a target under tests/ keeps its asserts
            dests = [a for a in added if a not in owner and base_name(a) == base_name(path)]
            if dests:
                found.append({"path": dests[0], "severity": "fail", "kind": "test-lost-asserts",
                              "detail": f"{kept_n[dests[0]]} of {had[path]} asserts kept"})
                continue
        if deleted and (test or gate):
            found.append({"path": path, "severity": "fail",
                          "kind": "test-deleted" if test else "gate-shrunk", "detail": "(deleted)"})
            continue
        if deleted:
            continue
        code, diff, err = _git_run(cwd, *SAFE, "diff", "--no-ext-diff", "--no-textconv", "--text",
                                   "-U0", "--no-renames", f"{base}...{tip}", "--", path, env=env)
        if code != 0:
            errors.append(_error(("diff", "-U0"), code, err))
            continue
        rows = diff.splitlines()
        body = rows[next((i for i, r in enumerate(rows) if r.startswith("@@")), len(rows)):]
        plus = [r[1:].strip() for r in body if r.startswith("+")]
        minus = [r[1:].strip() for r in body if r.startswith("-")]
        if test and _asserts(show(base, path)) and not _asserts(show(tip, path)):
            found.append({"path": path, "severity": "fail", "kind": "test-lost-asserts",
                          "detail": "no assert left"})
        if gate:
            lost = [m for m in minus if m and not m.startswith("#") and m not in plus
                    and _swap(m, mapping) not in plus]
            if lost:
                found.append({"path": path, "severity": "fail", "kind": "gate-shrunk", "detail": lost})
        if CODE_EXT.search(path):
            muted = [m for m in plus if SUPPRESSION.search(m)]
            if muted:
                found.append({"path": path, "severity": "warn", "kind": "suppression-added",
                              "detail": muted})
    return found


def _blob(cwd: str, tip: str, path: str, env: dict[str, str]) -> str | None:
    code, out = _run(cwd, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{tip}:{path}", env=env)
    return out.strip() if code == 0 and out.strip() else None


def _generated(cwd: str, base: str, tip: str, changed: list[str], env: dict[str, str],
               errors: list[dict]) -> list[str]:
    """The changed paths that are a byte-for-byte copy of their source file in a declared module.
    The manifest is read from the BASE, so the branch cannot declare its own outputs."""
    if not changed:
        return []
    code, out, err = _git_run(cwd, *SAFE, "show", f"{base}:{MODULES}", env=env)
    if code != 0:
        return []                         # no manifest at the base: nothing is declared generated
    try:
        manifest = json.loads(out)
        source = manifest["source"].strip("/")
        # A schema-1 manifest (a base before spec 2160) still names published module trees.
        roots = [m["path"].strip("/") for m in manifest["modules"]
                 if m.get("published") and m.get("path") and ("generated" in m or "owned" in m)]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append(_error(("show", MODULES), 0, f"unreadable module manifest: {exc}"))
        return []
    found = []
    for path in changed:
        for root in roots:
            if root != source and path.startswith(root + "/"):
                rel = path[len(root) + 1:]
                mine = _blob(cwd, tip, path, env)
                theirs = _blob(cwd, tip, f"{source}/{rel}", env)
                if mine == theirs:
                    found.append(path)
                break
    return found


def cmd_audit(args) -> int:
    cwd = os.getcwd()
    worktree = os.path.realpath(args.worktree)
    if not _registered(cwd, worktree):
        return refuse({"code": "audit-worktree-unregistered",
                       "message": f"not a worktree this repository registers: {args.worktree}"},
                      args.json)
    if not _branch_ok(worktree, args.branch):
        return refuse({"code": "audit-ref-invalid", "message": f"not a branch name: {args.branch}"},
                      args.json)
    errors: list[dict] = []
    env = _inert_env(worktree, errors)
    resolved = {}
    for label, ref in [("base", args.base), ("branch", f"refs/heads/{args.branch}"),
                       *(("sha", s) for s in args.sha)]:
        sha = _commit(worktree, ref, env)
        if sha is None:
            return refuse({"code": "audit-ref-invalid",
                           "message": f"{label} does not resolve to a commit: {ref}"}, args.json)
        resolved[ref] = sha
    base, tip = resolved[args.base], resolved[f"refs/heads/{args.branch}"]

    ancestry = {}
    for sha in args.sha:
        code, _out, err = _git_run(worktree, *SAFE, "merge-base", "--is-ancestor", resolved[sha], tip,
                                   env=env)
        if code > 1:                      # 1 is the answer "no"; anything above it is git failing
            errors.append(_error(("merge-base", "--is-ancestor"), code, err))
        ancestry[sha] = code == 0
    payload = {
        "ok": True,
        "worktree": worktree,
        "base": {"ref": args.base, "sha": base},
        "branch": {"ref": args.branch, "sha": tip},
        "status": _lines(worktree, "status", "--porcelain", "--untracked-files=normal",
                         "--ignore-submodules=all", env=env, errors=errors),
        "stash": _lines(worktree, "stash", "list", env=env, errors=errors),
        "commits": _lines(worktree, "log", "--oneline", f"{base}..{tip}", env=env, errors=errors),
        "changed": _paths(worktree, f"{base}...{tip}", env, errors),
        "ancestry": ancestry,
        "grants": [],
        "reflog": {"branch": _reflog(worktree, f"refs/heads/{args.branch}", env, errors),
                   "head": _reflog(worktree, "HEAD", env, errors)},
        "rewrites": {"branch": _rewrites(worktree, f"refs/heads/{args.branch}", env, errors),
                     "head": _rewrites(worktree, "HEAD", env, errors, branch=args.branch)},
    }
    payload["generated"] = _generated(worktree, base, tip, payload["changed"], env, errors)
    payload["grants"] = _grants(worktree, base, tip, payload["changed"], env, errors,
                                set(payload["generated"]))
    payload["lowered"] = _lowered(worktree, base, tip, env, errors)
    payload["errors"] = errors
    payload["complete"] = not errors

    lines = [f"worktree: {worktree}", f"branch: {args.branch} ({tip[:12]}) over {args.base}",
             f"commits: {len(payload['commits'])}", f"changed: {len(payload['changed'])} path(s)",
             f"generated: {len(payload['generated'])} path(s)",
             f"status: {len(payload['status'])} entries", f"stash: {len(payload['stash'])} entries",
             f"grants added: {sum(len(g['added']) for g in payload['grants'])}",
             f"lowered: {len(payload['lowered'])} entr(ies)"]
    lines += [f"ERROR {e['read']} (exit {e['code']}): {e['message']}" for e in errors]
    lines += [f"ancestor {s}: {'yes' if ok else 'NO'}" for s, ok in payload["ancestry"].items()]
    emit(args.json, payload, "\n".join(lines))
    return 0
