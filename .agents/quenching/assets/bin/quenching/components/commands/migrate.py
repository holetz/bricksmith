"""`cq components migrate` — move a target repository onto the generic `.agents/` layer.

The plan turns the legacy layout, where `.claude/` was the source and `.agents/` a Codex
translation of it, into the generic one: skills in `.agents/skills/<name>/SKILL.md`, references
in `.agents/references/**`, instructions in `AGENTS.md`, configuration in `.agents/quenching.json`,
and a pointer-only `CLAUDE.md` removed (Claude Code 2.1.277+ reads `AGENTS.md`). A nested `.claude/commands/<a>/<b>.md` becomes the flat
skill `<a>-<b>`, and a nested `.agents/skills/<a>/<b>/` folder becomes `<a>-<b>/` whole, its
sibling files with it.

An `.agents/skills/<path>/` or `.agents/references/<rel>` beside a `.claude/` source is a second
editable copy, whatever wrote it — the retired `cq components translate` included: it is a
conflict for the human to settle, and nothing is written. The legacy `openspec-*` skills and
`opsx/` commands are not this migration's and stay where they are.

Without `--write` it only plans: exit 0 with `conformant: true` when there is nothing to do, exit
1 with the operations otherwise. Two editable copies that disagree are a conflict: reported, and
nothing is written. `--write` applies the plan with `git mv` and `git rm`, so history follows the
moved files, then rewrites the citations inside them.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap
from pathlib import Path

from quenching.common.deploy import generated_skills, read_manifest
from quenching.common.output import FINDINGS, OK, emit, refuse
from quenching.common.frontmatter import parse_frontmatter
from quenching.components.commands.bridge import MANIFEST as BRIDGE_MANIFEST
from quenching.components.commands.lint import _split_tools
from quenching.components.families import compute_families

CLAUDE = Path(".claude")
AGENTS = Path(".agents")
SKILLS = AGENTS / "skills"
POINTER = "@AGENTS.md\n"
RESERVED_PREFIX = "quenching-"
TRANSLATION_MANIFESTS = (AGENTS / ".generated-from.json", AGENTS / ".generated-files.json")


def legacy_openspec(rel: str) -> bool:
    """`openspec init` leftovers: clearing them is a human's call, never this migration's."""
    return (rel.startswith((CLAUDE / "commands" / "opsx").as_posix() + "/")
            or rel.startswith((CLAUDE / "skills" / "openspec-").as_posix()))


def flat_destination(rel: str, skill_dirs: set[tuple[str, ...]]) -> str:
    """`a/b/scripts/x.sh` -> `a-b/scripts/x.sh` when `a/b/` is a skill folder: the deepest
    skill folder holding a file owns it, and the whole folder flattens together."""
    parts = tuple(rel.split("/"))
    for depth in range(len(parts) - 1, 0, -1):
        if parts[:depth] in skill_dirs:
            return "/".join(("-".join(parts[:depth]),) + parts[depth:])
    return rel


def _tracked(repo: Path, *paths: Path) -> list[str]:
    """Repo-relative tracked files under `paths`: only what git holds can move with history."""
    done = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--",
                           *(p.as_posix() for p in paths)],
                          check=True, capture_output=True, text=True)
    return sorted(name for name in done.stdout.split("\0") if name)


def _bridged(repo: Path) -> set[str]:
    path = repo / BRIDGE_MANIFEST
    if not path.is_file():
        return set()
    try:
        files = json.loads(path.read_text(encoding="utf-8")).get("files", {})
    except (ValueError, AttributeError):
        return set()
    return {rel.split("/", 1)[0] for rel in files}


def _deployed(repo: Path) -> tuple[set[str], set[str]]:
    """What `cq deploy` owns, from `.agents/.quenching-deploy.json`: its file paths and its
    skill names. The migration only moves what the target wrote."""
    files = (read_manifest(str(repo)) or {}).get("files")
    return (set(files) if isinstance(files, dict) else set()), generated_skills(str(repo))


def _owned_by_deploy(rel: str, files: set[str], skills: set[str]) -> bool:
    if rel in files:
        return True
    prefix = SKILLS.as_posix() + "/"
    return rel.startswith(prefix) and rel[len(prefix):].split("/", 1)[0] in skills


def _skill_dirs(paths: list[str]) -> set[tuple[str, ...]]:
    return {tuple(rel.split("/")[:-1]) for rel in paths if rel.split("/")[-1] == "SKILL.md"}


def skill_name(command_relpath: str) -> str:
    """`a/b.md` -> `a-b`: Claude Code discovers only flat skill folders."""
    return command_relpath[:-3].replace("/", "-")


class Plan:
    def __init__(self, repo: Path, tracked: set[str]):
        self.repo = repo
        self.tracked = tracked
        self.ops: list[dict] = []
        self.conflicts: list[dict] = []
        self.renames: dict[str, str] = {}
        self.removed: set[str] = set()
        self.claimed: set[str] = set()

    def remove(self, rel: str, why: str) -> None:
        if rel in self.tracked and rel not in self.removed:
            self.removed.add(rel)
            self.ops.append({"op": "remove", "path": rel, "why": why})

    def reserve(self, src: str, name: str) -> None:
        """`quenching-` belongs to the installed modules: a skill of the target's own that
        carries it is a conflict for the human, never moved or grouped."""
        path = f"{SKILLS.as_posix()}/{name}"
        if not any(c["path"] == path and c["why"] == "reserved prefix" for c in self.conflicts):
            self.conflicts.append({"path": path, "with": src, "why": "reserved prefix"})

    def move(self, src: str, dst: str) -> None:
        skills = SKILLS.as_posix() + "/"
        name = dst[len(skills):].split("/", 1)[0] if dst.startswith(skills) else ""
        if name.startswith(RESERVED_PREFIX):
            self.reserve(src, name)
            return
        if dst in self.claimed or (dst in self.tracked and dst not in self.removed):
            self.conflicts.append({"path": dst, "with": src, "why": "two editable copies"})
            return
        self.claimed.add(dst)
        self.ops.append({"op": "move", "from": src, "to": dst})


def build_plan(repo: Path) -> Plan:
    """The `.claude/` source moves into `.agents/`; a copy already there is a conflict."""
    deployed_files, deployed_skills = _deployed(repo)
    tracked = [rel for rel in _tracked(repo, CLAUDE, AGENTS, Path("CLAUDE.md"), Path("AGENTS.md"))
               if not legacy_openspec(rel) and not _owned_by_deploy(rel, deployed_files,
                                                                    deployed_skills)]
    plan = Plan(repo, set(tracked))
    commands = (CLAUDE / "commands").as_posix() + "/"
    references = (CLAUDE / "references").as_posix() + "/"
    for manifest in TRANSLATION_MANIFESTS:
        plan.remove(manifest.as_posix(), "manifest of the old translation")
    plan.remove((AGENTS / "AGENTS.md").as_posix(), "translation of CLAUDE.md")
    for rel in tracked:
        if rel.startswith(commands) and rel.endswith(".md"):
            dst = (SKILLS / skill_name(rel[len(commands):]) / "SKILL.md").as_posix()
            plan.renames[rel] = dst
            plan.move(rel, dst)
        elif rel.startswith(references):
            plan.move(rel, (AGENTS / "references" / rel[len(references):]).as_posix())
    claude_skills = (CLAUDE / "skills").as_posix() + "/"
    bridged = _bridged(repo)
    owned = [rel[len(claude_skills):] for rel in tracked if rel.startswith(claude_skills)
             and rel[len(claude_skills):].split("/", 1)[0] not in bridged
             and rel != BRIDGE_MANIFEST.as_posix()]
    owned_dirs = _skill_dirs(owned)
    for inner in owned:
        dst = (SKILLS / flat_destination(inner, owned_dirs)).as_posix()
        if dst != (SKILLS / inner).as_posix():
            plan.renames[claude_skills + inner] = dst
        plan.move(claude_skills + inner, dst)
    agents_skills = SKILLS.as_posix() + "/"
    for rel in tracked:
        if rel.startswith(agents_skills):
            name = rel[len(agents_skills):].split("/", 1)[0]
            if name.startswith(RESERVED_PREFIX):
                plan.reserve(rel, name)
    nested = [rel[len(agents_skills):] for rel in tracked if rel.startswith(agents_skills)
              and rel not in plan.removed]
    dirs = {d for d in _skill_dirs(nested) if len(d) > 1}
    for inner in nested:
        dst = flat_destination(inner, dirs)
        if dst != inner:
            plan.renames[agents_skills + inner] = agents_skills + dst
            plan.move(agents_skills + inner, agents_skills + dst)
    legacy, home = (CLAUDE / "quenching.json").as_posix(), (AGENTS / "quenching.json").as_posix()
    if legacy in plan.tracked:
        if home in plan.tracked:
            if (repo / legacy).read_bytes() == (repo / home).read_bytes():
                plan.remove(legacy, f"identical to {home}")
            else:
                plan.conflicts.append({"path": home, "with": legacy,
                                       "why": "two configuration files differ"})
        elif (repo / home).exists():
            plan.conflicts.append({"path": home, "with": legacy,
                                   "why": "the home exists but is not tracked"})
        else:
            plan.move(legacy, home)
    claude_md, agents_md = repo / "CLAUDE.md", repo / "AGENTS.md"
    pointer_only = claude_md.is_file() and claude_md.read_text(
        encoding="utf-8").strip() == POINTER.strip()
    if claude_md.is_file() and pointer_only:
        # Claude Code 2.1.277+ reads AGENTS.md directly; the import is legacy residue.
        if "CLAUDE.md" in plan.tracked:
            plan.remove("CLAUDE.md", "legacy @AGENTS.md pointer; AGENTS.md is read directly")
    elif claude_md.is_file():
        if agents_md.is_file() and agents_md.read_bytes() == claude_md.read_bytes():
            plan.remove("CLAUDE.md", "duplicate of AGENTS.md; one source remains")
        elif agents_md.is_file():
            plan.conflicts.append({"path": "AGENTS.md", "with": "CLAUDE.md",
                                   "why": "both carry their own instructions"})
        elif "CLAUDE.md" not in plan.tracked:
            plan.conflicts.append({"path": "CLAUDE.md", "with": "AGENTS.md",
                                   "why": "CLAUDE.md is not tracked, so git mv cannot move it"})
        else:
            plan.ops.append({"op": "move", "from": "CLAUDE.md", "to": "AGENTS.md"})
    return plan


def _grant_union(tool_lists: list[list[str]]) -> list[str]:
    """The router's `allowed-tools`: every tool of every flow once, a bare `Bash` standing for
    each scoped `Bash(...)` — the calculation `sk-router-grant` checks."""
    tools = [tool for tools in tool_lists for tool in tools]
    bash_open = "Bash" in tools
    out: list[str] = []
    for tool in tools:
        if tool in out or (bash_open and tool.startswith("Bash(")):
            continue
        out.append(tool)
    return out


def _final_files(plan: Plan) -> dict[str, str]:
    """`{path after the layout plan: path today}` for every tracked file that survives it."""
    moved = {op["from"]: op["to"] for op in plan.ops if op["op"] == "move"}
    return {moved.get(rel, rel): rel for rel in sorted(plan.tracked) if rel not in plan.removed}


def build_routers(plan: Plan) -> list[dict]:
    """The families of the target's own skills that earn a router, read off the layout the plan
    leaves: per family its `kind` (`new` or `existing`), `router`, `members`, `flows`, the `grant`
    union of the members' `allowed-tools`, and the `conflicts` that would stop applying it."""
    final = _final_files(plan)
    skills = SKILLS.as_posix() + "/"
    commands = (CLAUDE / "commands").as_posix() + "/"
    entries = []
    for dst, src in final.items():
        if not (dst.startswith(skills) and dst.endswith("/SKILL.md")
                and dst.count("/") == skills.count("/") + 1):
            continue
        name = dst[len(skills):].split("/", 1)[0]
        text = (plan.repo / src).read_text(encoding="utf-8") if (plan.repo / src).is_file() else ""
        entries.append({"command": "/" + name, "path": src, "text": text,
                        "relpath": src[len(commands):] if src.startswith(commands) else f"{name}/SKILL.md",
                        "frontmatter": parse_frontmatter(text)})
    by_name = {entry["command"].lstrip("/"): entry for entry in entries}
    routers = []
    for family, item in compute_families(entries).items():
        if not item["candidate"]:
            continue
        members = item["members"]
        conflicts = []
        table = _table_flows(by_name[family]["text"]) if item["kind"] == "existing" else set()
        refs = _existing_refs(final, family)
        for member, flow in zip(members, item["flows"]):
            target = f"{skills}{family}/references/{flow}.md"
            if item["kind"] == "new":
                if target in final or any(path.startswith(target[:-3] + "/") for path in final):
                    conflicts.append({"path": target, "with": member, "why": "two editable copies"})
            elif flow not in table or target not in refs:
                conflicts.append({"path": f"{skills}{member}", "with": family,
                                  "why": "shortcut without a reference in the router"})
        grant = _grant_union([_split_tools(str(by_name[m]["frontmatter"].get("allowed-tools", "")))
                              for m in members])
        routers.append({"family": family, "kind": item["kind"], "router": family,
                        "members": members, "flows": item["flows"], "grant": grant,
                        "conflicts": conflicts})
    return routers


TABLE_ROW_RE = re.compile(r"^\|\s*`([a-z0-9-]+)`\s*\|", re.MULTILINE)


def _table_flows(router_text: str) -> set[str]:
    return set(TABLE_ROW_RE.findall(router_text))


def _existing_refs(final: dict[str, str], family: str) -> set[str]:
    """Where the family's references end up: beside the router, wherever they are today."""
    out = set()
    for path in final:
        for old in (f".agents/references/{family}-flows/", f".claude/references/{family}-flows/"):
            if path.startswith(old):
                out.add(f"{SKILLS.as_posix()}/{family}/references/{path[len(old):]}")
        if path.startswith(f"{SKILLS.as_posix()}/{family}/references/"):
            out.add(path)
    return out


def invocation(path: str) -> str | None:
    """`.claude/commands/a/b.md` -> `/a:b`, the name the moved command answered to."""
    prefix = (CLAUDE / "commands").as_posix() + "/"
    return "/" + path[len(prefix):-3].replace("/", ":") if path.startswith(prefix) else None


def rewrite_text(text: str, renames: dict[str, str]) -> str:
    """Rewrite paths and invocations of the moved commands: `/a:b`, and the Skill tool's
    `Skill(skill="a:b")`, `Skill("a:b")`, `Skill(a:b)` and `"skill": "a:b"` forms."""
    for old, new in sorted(renames.items(), key=lambda item: -len(item[0])):
        text = text.replace(old, new)
        name = invocation(old)
        if name and ":" in name:
            flat = Path(new).parent.name
            text = re.sub(r"(?<![\w/.:-])" + re.escape(name) + r"(?![\w:-])", "/" + flat, text)
            bare = re.escape(name[1:])
            text = re.sub(r"(\bSkill\(\s*(?:skill\s*[=:]\s*)?[\"']?/?)" + bare
                          + r"(?=[\"'\s,)])", lambda m: m.group(1) + flat, text)
            text = re.sub(r"([\"']skill[\"']\s*:\s*[\"']/?)" + bare + r"(?=[\"'])",
                          lambda m: m.group(1) + flat, text)
    return (text.replace(".claude/references/", ".agents/references/")
            .replace(".claude/skills/", ".agents/skills/"))


def with_name(text: str, name: str) -> str:
    """Set the frontmatter `name:` to the folder name, which OpenCode requires to match."""
    nl = "\r\n" if text.startswith("---\r\n") else "\n"
    if not text.startswith("---" + nl):
        return f"---{nl}name: {name}{nl}---{nl}" + text
    start = len("---" + nl)
    end = text.find(nl + "---", start - len(nl))
    if end < 0:
        return text
    head = text[start:end + len(nl)] if end >= start else ""
    if re.search(r"(?m)^name:", head):
        head = re.sub(r"(?m)^name:[^\r\n]*", f"name: {name}", head, count=1)
    else:
        head = f"name: {name}{nl}" + head
    return "---" + nl + head + text[end + len(nl):]


def _git(repo: Path, *argv: str) -> None:
    subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True, text=True)


def apply(plan: Plan) -> None:
    repo = plan.repo
    for op in plan.ops:
        if op["op"] == "remove":
            _git(repo, "rm", "-q", "-r", "--", op["path"])
        elif op["op"] == "move":
            (repo / op["to"]).parent.mkdir(parents=True, exist_ok=True)
            _git(repo, "mv", "--", op["from"], op["to"])
    for op in plan.ops:
        parent = (repo / op.get("from", op.get("path", ""))).parent
        while parent != repo and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    targets = {repo / op["to"] for op in plan.ops
               if op["op"] == "move" and op["to"].endswith(".md")}
    for path in sorted(targets):
        text = path.read_text(encoding="utf-8")
        new = rewrite_text(text, plan.renames)
        if path.name == "SKILL.md" and path.parent.parent == repo / SKILLS:
            new = with_name(new, path.parent.name)
        if new != text:
            path.write_text(new, encoding="utf-8")
            _git(repo, "add", "--", path.relative_to(repo).as_posix())


DESCRIPTION_MAX = 1024
ROUTER_DROPPED_KEYS = ("name", "disable-model-invocation")


def _first_sentence(description: str) -> str:
    flat = " ".join(str(description).split())
    return re.split(r"(?<=[.!?])\s", flat, maxsplit=1)[0].rstrip(".")


def _flow_frontmatter(text: str) -> str:
    """A member becomes a reference: `description`, `allowed-tools` and `argument-hint` stay,
    `name` and `disable-model-invocation` go; the body is untouched."""
    nl = "\r\n" if text.startswith("---\r\n") else "\n"
    if not text.startswith("---" + nl):
        return text
    end = text.find(nl + "---", 3)
    if end < 0:
        return text
    head = text[len("---" + nl):end + len(nl)]
    kept = [line for line in head.splitlines(keepends=True)
            if not any(line.startswith(key + ":") for key in ROUTER_DROPPED_KEYS)]
    return "---" + nl + "".join(kept) + text[end + len(nl):]


def _router_skill(family: str, flows: list[tuple[str, dict]], grant: list[str]) -> str:
    """The router's `SKILL.md`: a mechanical description that names every flow, the union
    grant, and the table of flows each pointing at its reference."""
    names = ", ".join(flow for flow, _ in flows)
    description = f"Run one {family} flow: {names}."
    for flow, fm in flows:
        piece = _first_sentence(fm.get("description", ""))
        if piece and len(description) + len(flow) + len(piece) + 4 <= DESCRIPTION_MAX:
            description += f" {flow}: {piece}."
    rows = "\n".join(
        f"| `{flow}` | [references/{flow}.md](.agents/skills/{family}/references/{flow}.md) | "
        f"{_first_sentence(fm.get('description', '')) or flow} |" for flow, fm in flows)
    folded = "\n".join("  " + line for line in
                       textwrap.wrap(description, 96, break_long_words=False) or [description])
    return (f"---\nname: {family}\ndescription: >-\n{folded}\n"
            f"argument-hint: \"[{'|'.join(flow for flow, _ in flows)}] [input]\"\n"
            f"allowed-tools: {', '.join(grant)}\n---\n\n"
            f"# /{family} — one entry point for the {family} flows\n\n"
            "**Input**: `$ARGUMENTS` — an optional flow name, then what that flow takes. The "
            "remaining words go to the flow untouched.\n\n"
            "This skill carries no flow of its own. It picks ONE flow, loads that flow's reference "
            "and follows it as if it were this body.\n\n"
            "**Grant.** The router grants the union of its flows' `allowed-tools`, each declared "
            "in the flow's own reference.\n\n## Workflow\n\n### 1. Pick the flow\n"
            "The first word of `$ARGUMENTS` naming a flow in the table picks it; no flow named, "
            "the request's own words pick it. An input that fits two flows is asked, never "
            "guessed.\n\n| Flow | Reference | Picked when |\n| --- | --- | --- |\n"
            f"{rows}\n\n**Done when:** exactly one flow is chosen, with its input.\n\n"
            "### 2. Load the flow and follow it\n"
            f"Read the chosen flow's reference under `.agents/skills/{family}/references/` whole, "
            "once, and follow it with the input from step 1.\n"
            "**Done when:** the flow ran to its own `Done when:`, or stopped on its own stop "
            "condition.\n\n## Invariants\n\n"
            "- Never run two flows in one invocation.\n"
            "- Never edit what a flow's reference says here: a flow changes in its reference.\n")


def _rewrite_citations(repo: Path, family: str, members: list[str], flows: list[str],
                       paths: dict[str, str]) -> None:
    """`/<family>-<flow>` and `/<family>:<flow>` become `/<family> <flow>`, and a moved path
    becomes its new one, in every `.md` the target wrote — never a file the deploy owns."""
    deployed_files, deployed_skills = _deployed(repo)
    listed = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--",
                             SKILLS.as_posix(), (AGENTS / "references").as_posix(), "AGENTS.md"],
                            check=True, capture_output=True, text=True).stdout.split("\0")
    ordered = sorted(zip(members, flows), key=lambda pair: -len(pair[0]))
    for rel in sorted(name for name in listed if name.endswith(".md")):
        if _owned_by_deploy(rel, deployed_files, deployed_skills):
            continue
        path = repo / rel
        text = new = path.read_text(encoding="utf-8")
        for old, dst in sorted(paths.items(), key=lambda item: -len(item[0])):
            new = new.replace(old, dst)
        for member, flow in ordered:
            for form in {f"/{member}", f"/{family}:{flow}", f"/{family}:{flow.replace('-', ':')}"}:
                new = re.sub(r"(?<![\w/.:-])" + re.escape(form) + r"(?![\w:-])",
                             f"/{family} {flow}", new)
        if new != text:
            path.write_text(new, encoding="utf-8")
            _git(repo, "add", "--", rel)


def apply_router(repo: Path, entry: dict) -> None:
    """Group one family under its router with `git mv`, so history follows every member."""
    family, members, flows = entry["family"], entry["members"], entry["flows"]
    home = f"{SKILLS.as_posix()}/{family}"
    paths: dict[str, str] = {}
    if entry["kind"] == "existing":
        for old in (f".agents/references/{family}-flows/", f".claude/references/{family}-flows/"):
            for rel in _tracked(repo, Path(old)):
                dst = f"{home}/references/{rel[len(old):]}"
                (repo / dst).parent.mkdir(parents=True, exist_ok=True)
                _git(repo, "mv", "--", rel, dst)
            paths[old] = f"{home}/references/"
        for member in members:
            _git(repo, "rm", "-q", "-r", "--", f"{SKILLS.as_posix()}/{member}")
    else:
        described: list[tuple[str, dict]] = []
        for member, flow in zip(members, flows):
            src = f"{SKILLS.as_posix()}/{member}"
            described.append((flow, parse_frontmatter((repo / src / "SKILL.md").read_text(
                encoding="utf-8"))))
            for rel in _tracked(repo, Path(src)):
                inner = rel[len(src) + 1:]
                dst = (f"{home}/references/{flow}.md" if inner == "SKILL.md"
                       else f"{home}/references/{flow}/{inner}")
                (repo / dst).parent.mkdir(parents=True, exist_ok=True)
                _git(repo, "mv", "--", rel, dst)
                paths[rel] = dst
            reference = repo / f"{home}/references/{flow}.md"
            reference.write_text(_flow_frontmatter(reference.read_text(encoding="utf-8")),
                                 encoding="utf-8")
            _git(repo, "add", "--", reference.relative_to(repo).as_posix())
            leftover = repo / src
            while leftover != repo / SKILLS and leftover.is_dir() and not any(leftover.iterdir()):
                leftover.rmdir()
                leftover = leftover.parent
        router = repo / home / "SKILL.md"
        router.write_text(_router_skill(family, described, entry["grant"]), encoding="utf-8")
        _git(repo, "add", "--", router.relative_to(repo).as_posix())
    _rewrite_citations(repo, family, members, flows, paths)


def add_arguments(sp) -> None:
    sp.add_argument("--target", default=".", help="the repository to migrate")
    sp.add_argument("--write", action="store_true", help="apply the plan with git mv / git rm")
    sp.add_argument("--router", action="append", default=[], metavar="FAMILY",
                    help="with --write, group this family's skills under one router "
                         "(repeatable; a family not named is not touched)")
    sp.add_argument("--json", action="store_true", help="machine-readable output")


def cmd_migrate(args, _root: str) -> int:
    repo = Path(os.path.abspath(args.target))
    if subprocess.run(["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
                      capture_output=True, text=True).stdout.strip() != "true":
        return refuse({"code": "ct-migrate-not-git", "target": str(repo),
                       "message": f"{repo} is not a git checkout; the migration moves files "
                                  "with git mv to keep their history"}, args.json)
    plan = build_plan(repo)
    routers = build_routers(plan)
    unknown = sorted(set(args.router) - {entry["family"] for entry in routers})
    if unknown:
        return refuse({"code": "ct-migrate-router-unknown", "families": unknown,
                       "message": "no router candidate with that name: "
                                  + ", ".join(unknown),
                       "remedy": "name a family listed in `routers` of the plan"}, args.json)
    chosen = [entry for entry in routers if entry["family"] in args.router]
    if args.write:
        plan.conflicts += [c for entry in chosen for c in entry["conflicts"]]
    if args.write and plan.conflicts:
        return refuse({"code": "ct-migrate-conflict", "conflicts": plan.conflicts,
                       "message": "the plan has conflicts; nothing was written",
                       "remedy": "settle each pair by hand, then rerun"}, args.json)
    if args.write and (plan.ops or chosen):
        try:
            apply(plan)
            for entry in chosen:
                apply_router(repo, entry)
        except subprocess.CalledProcessError as error:
            return refuse({"code": "ct-migrate-git", "command": " ".join(error.cmd),
                           "message": f"git failed mid-migration: {error.stderr.strip()}",
                           "remedy": "inspect `git status` in the target; the index holds "
                                     "what was applied so far"}, args.json)
    pending = [] if args.write else plan.ops
    payload = {"ok": not plan.conflicts, "target": str(repo),
               "conformant": not plan.ops and not plan.conflicts,
               "operations": plan.ops, "conflicts": plan.conflicts,
               "renames": plan.renames, "routers": routers, "written": bool(args.write and (plan.ops or chosen))}
    if args.write:
        payload["routers"] = [entry for entry in routers if entry not in chosen]
        payload["applied"] = [entry["family"] for entry in chosen]
    lines = [f"components migrate — {repo}",
             "  conformant, nothing to align" if payload["conformant"] else
             f"  operations: {len(plan.ops)}  conflicts: {len(plan.conflicts)}"
             + ("  (written)" if payload["written"] else "")]
    for op in plan.ops:
        lines.append("  " + " ".join(f"{k}={v}" for k, v in op.items()))
    for entry in routers:
        lines.append(f"  router {entry['kind']}: {entry['router']} ({len(entry['members'])} members;"
                     f" apply with --write --router {entry['family']})")
    for conflict in plan.conflicts:
        lines.append(f"  conflict: {conflict['path']} vs {conflict['with']} — {conflict['why']}")
    emit(args.json, payload, "\n".join(lines))
    return FINDINGS if pending or plan.conflicts else OK
