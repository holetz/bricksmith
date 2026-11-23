"""`migrate --to git` — move the specs of a tracker backend onto the `quenching` branch.
(`--to azure` and `--to github` are the other direction, below: the git store onto a tracker.)

Dry run unless `--write`. The whole front is read from the configured tracker backend (every
continuation part included, because `read_spec` reassembles the document), written under the
SAME IDs in ONE commit — open specs in `specs/`, closed ones in `specs/archive/` — and the
specs-axis configuration is copied into the branch's `quenching.json`. The command prints the
change the human applies to `.agents/quenching.json`; it never edits that file.

The check that makes the move trustworthy is an equality, not a promise: after the commit the
`git` store's listing must equal the source's modulo locator, and every difference is reported.
Closed tracker items are never touched (the history stays where it is); `--thin-open-cards`
rewrites only the OPEN ones to the thin card, after the commit has landed.
"""
from __future__ import annotations

import json
import os
import re
from typing import NamedTuple

from quenching.common.config import (config_path, find_repo_root, load_config as load_envelope,
                                     namespace)
from quenching.common.frontmatter import parse_frontmatter
from quenching.specs.backends import open_backend
from quenching.specs.backends.azure import (AZ_DESCRIPTION_MAX, azure_predecessor_ids,
                                             open_azure_backend)
from quenching.specs.backends.base import BackendRefusal
from quenching.specs.backends.git import open_git_backend
from quenching.specs.backends.github import GH_DEPENDENCY_LIMIT, open_github_backend
from quenching.specs.commands.output import Emitter
from quenching.specs.config import BRANCH_CONFIG_KEYS, CONFIG_FILE, load_config
from quenching.specs.commands.deps import _flow
from quenching.specs.parse import FIELD_KEYS, derive_info, strip_frontmatter_keys
from quenching.specs.parse.deps import deps_of
from quenching.specs.parse.fields import set_frontmatter_key
from quenching.specs.parse.records import spec_records
from quenching.specs.parse.tasks import task_progress


# The config the branch gains from the source repository: the specs-axis keys the loader lets
# the branch own, minus the ones that are ABOUT the card or the language and are decided apart.
CARRIED_KEYS = tuple(k for k in BRANCH_CONFIG_KEYS
                     if k not in ("card", "cardAt", "artifactLanguage"))


def reassemble(info: dict) -> str:
    """The canonical document with the fields a tracker stores natively put back into its
    frontmatter. A tracker backend keeps `tags`/`assignee`/`start`/`target` in native fields
    and strips them from the stored text, so the text alone is not the whole spec; the git
    store has no native fields and holds all of it in the one file."""
    text = info["text"]
    held = parse_frontmatter(text)
    for key in FIELD_KEYS:
        value = (info.get("frontmatter") or {}).get(key)
        if value in (None, "", []) or key in held:
            continue
        if isinstance(value, list):
            value = "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in value) + "]"
        text = set_frontmatter_key(text, key, str(value))
    return text


def comparable(info: dict) -> dict:
    """What `list --json` says about a spec, minus the locator — the equality's unit."""
    checked, blocked, total = task_progress(info["tasks"])
    fm = info["frontmatter"]
    return {"id": info["id"], "phase": info["phase"], "date": info["date"],
            "title": fm.get("title", ""), "stage": info["stage"],
            "outcome": fm.get("outcome") or None, "records": spec_records(fm),
            "tasks": {"checked": checked, "blocked": blocked, "total": total}}


def read_front(backend) -> tuple[list[dict], list[dict]]:
    """`(infos, unreadable)` for the whole front, in listing order."""
    infos, unreadable = [], []
    for row in backend.list_specs():
        info, err = backend.read_spec(row["id"])
        if err or info is None:
            unreadable.append({"id": row["id"], "code": (err or {}).get("code")})
        else:
            infos.append(info)
    return infos, unreadable


def diff_fronts(source: list[dict], target: list[dict]) -> list[dict]:
    """Every difference between two fronts, modulo locator."""
    left = {i["id"]: comparable(i) for i in source}
    right = {i["id"]: comparable(i) for i in target}
    diffs = []
    for spec_id in sorted(set(left) | set(right)):
        if spec_id not in right:
            diffs.append({"id": spec_id, "kind": "missing-in-target"})
        elif spec_id not in left:
            diffs.append({"id": spec_id, "kind": "extra-in-target"})
        elif left[spec_id] != right[spec_id]:
            diffs.append({"id": spec_id, "kind": "differs",
                          "fields": sorted(k for k in left[spec_id]
                                           if left[spec_id][k] != right[spec_id][k])})
    return diffs


def migration_config(source_namespace: dict, held: dict, provider: str) -> dict:
    """The branch's `specs` namespace after the move: what it already holds wins over what
    the source declares, so a re-run never undoes a hand edit on the branch."""
    config = {k: v for k, v in source_namespace.items() if k in CARRIED_KEYS}
    config["card"] = {"provider": provider, "at": "capture"}
    config.update(held)
    return config


def migrate(source, target, source_namespace: dict, *, write: bool,
            thin_cards: bool = False, card_client=None) -> dict:
    """Run (or preview) the move; returns the report `cmd_migrate` prints."""
    from quenching.specs import cards
    infos, unreadable = read_front(source)
    items = [(int(i["id"]), i["phase"], reassemble(i)) for i in infos]
    provider = {"github": "github", "azure-boards": "azure-boards"}.get(source.name, "none")
    config = migration_config(source_namespace, target.read_config(), provider)
    open_ids = [i for i, phase, _ in items if phase == "plans"]
    report = {"ok": True, "from": source.name, "to": "git", "dryRun": not write,
              "count": len(items), "open": len(open_ids),
              "closed": len(items) - len(open_ids), "unreadable": unreadable,
              "ids": [i for i, _, _ in items], "branchConfig": config,
              "configChange": {"file": CONFIG_FILE, "set": {"backend": "git"},
                               "card": config["card"],
                               "note": "the card and the specs-axis keys now live on the "
                                       "branch; remove them from the specs namespace here "
                                       "once the branch is pushed"},
              "thinOpenCards": open_ids if thin_cards else []}
    rebuilt = [derive_info(i, text) for i, (_, _, text) in zip(infos, items)]
    if not write:
        report["diffs"] = diff_fronts(infos, rebuilt)
        report["ok"] = not report["diffs"] and not unreadable
        return report
    report["written"] = target.import_specs(items, config=config)
    after, _ = read_front(target)
    report["diffs"] = diff_fronts(infos, after)
    report["identical"] = all(target.read_spec(i)[0]["text"] == t for i, _, t in items)
    report["ok"] = not report["diffs"] and not unreadable and report["identical"]
    report["cardErrors"] = []
    if thin_cards:
        client = card_client or cards.client_for_backend(source, source_namespace)
        by_id = {i["id"]: i for i in after}
        for spec_id in open_ids:
            try:
                client.update(spec_id, cards.card_state(by_id[spec_id]))
            except BackendRefusal as exc:
                report["cardErrors"].append({"id": spec_id, "message": exc.err.get("message")})
        report["ok"] = report["ok"] and not report["cardErrors"]
    return report


# -- `--to azure` / `--to github` -------------------------------------------- #
# A tracker allocates its own ids, so a spec's id changes. These helpers are pure: ordering,
# the id remap, and the document equality that tolerates the fields the backend stores natively.


class TrackerProfile(NamedTuple):
    """What differs between two tracker destinations: the `--to` value, the name a message
    shows, the backend the config change sets, the document ceiling (`None` when the
    backend spills an oversize body into parts of its own) and the most predecessor links
    one item holds (`None` when the backend states no cap)."""
    flag: str
    label: str
    backend: str
    ceiling: int | None
    link_limit: int | None = None


AZURE_PROFILE = TrackerProfile("azure", "Azure Boards", "azure-boards", AZ_DESCRIPTION_MAX)
GITHUB_PROFILE = TrackerProfile("github", "GitHub Issues", "github", None, GH_DEPENDENCY_LIMIT)

AZURE_REF_KEYS = ("epic", "parent")
_MEMBER_RE = re.compile(r"((?:^|\s)—\s*spec\s*:\s*#?)(\d+)(?=\s|—|$)",
                        re.IGNORECASE | re.MULTILINE)


def azure_order(items: list[tuple[int, str, str]]) -> tuple[list[int], list[dict]]:
    """`(creation order, unresolved)`: dependencies before dependents, ties by source id.

    A numeric `dependsOn` the front does not hold, a spec waiting for itself, a dependency
    cycle and an epic member naming an absent id are `unresolved` — the write refuses on any.
    A non-numeric dependency (slug, `owner/repo#id`) is not this store's and is ignored."""
    ids = {spec_id for spec_id, _, _ in items}
    waits: dict[int, set[int]] = {}
    unresolved: list[dict] = []
    for spec_id, _, text in sorted(items):
        fm = parse_frontmatter(text)
        deps = set()
        for dep in deps_of(fm):
            if not dep.isdigit():
                continue
            if int(dep) == spec_id:
                unresolved.append({"id": spec_id, "kind": "self", "ref": int(dep)})
            elif int(dep) not in ids:
                unresolved.append({"id": spec_id, "kind": "missing", "ref": int(dep)})
            else:
                deps.add(int(dep))
        waits[spec_id] = deps
        if _MEMBER_RE.search(text):
            unresolved += [{"id": spec_id, "kind": "missing-member", "ref": int(m.group(2))}
                           for m in _MEMBER_RE.finditer(text) if int(m.group(2)) not in ids]
    order: list[int] = []
    pending = dict(waits)
    while pending:
        ready = sorted(i for i, deps in pending.items() if deps <= set(order))
        if not ready:
            unresolved += [{"id": i, "kind": "cycle", "ref": sorted(pending[i])}
                           for i in sorted(pending)]
            break
        order.append(ready[0])
        del pending[ready[0]]
    return order, unresolved


def remap_text(text: str, idmap: dict[int, int], *, members: bool = True) -> str:
    """The document with its structured id references moved onto the new ids: `dependsOn`,
    the `epic`/`parent` keys, and (with `members`) an epic's `— spec: #<id>` items. Prose is
    never touched. Always called on the SOURCE text, so a new id is never remapped twice."""
    fm = parse_frontmatter(text)
    deps = deps_of(fm)
    if any(d.isdigit() and int(d) in idmap for d in deps):
        text = set_frontmatter_key(text, "dependsOn", _flow(
            [str(idmap[int(d)]) if d.isdigit() and int(d) in idmap else d for d in deps]))
    for key in AZURE_REF_KEYS:
        value = str(fm.get(key) or "").strip().lstrip("#")
        if value.isdigit() and int(value) in idmap:
            text = set_frontmatter_key(text, key, str(idmap[int(value)]))
    if members and _MEMBER_RE.search(text):
        text = _MEMBER_RE.sub(lambda m: m.group(1) + str(idmap.get(int(m.group(2)),
                                                                    m.group(2))), text)
    return text


def _norm(value):
    return None if value in (None, "", []) else (
        [str(v) for v in value] if isinstance(value, list) else str(value))


def document_matches(expected: str, info: dict) -> bool:
    """Whether the item read back holds the expected document: the body with the natively
    stored keys stripped, and those keys compared as values, since the tracker keeps them
    in fields of its own."""
    held, want = info["frontmatter"], parse_frontmatter(expected)
    same_body = (strip_frontmatter_keys(info["text"], FIELD_KEYS).strip()
                 == strip_frontmatter_keys(expected, FIELD_KEYS).strip())
    return same_body and all(_norm(held.get(k)) == _norm(want.get(k)) for k in FIELD_KEYS)


def _refuse(code: str, message: str, **extra) -> BackendRefusal:
    return BackendRefusal({"code": code, "exit": 2, "message": message, **extra})


def migrate_to_azure(source, target, *, write: bool) -> dict:
    """The move of a git store onto Azure Boards — `migrate_to_tracker` with its profile."""
    return migrate_to_tracker(source, target, AZURE_PROFILE, write=write)


def migrate_to_github(source, target, *, write: bool) -> dict:
    """The move of a git store onto GitHub Issues — `migrate_to_tracker` with its profile."""
    return migrate_to_tracker(source, target, GITHUB_PROFILE, write=write)


def links_match(expected: str, target, new_id: int) -> bool:
    """Whether the destination's native predecessor links equal the expected `dependsOn`.
    A destination without `predecessor_links` has nothing to read back, so it matches."""
    read_links = getattr(target, "predecessor_links", None)
    if read_links is None:
        return True
    want = sorted(azure_predecessor_ids(parse_frontmatter(expected), new_id))
    return list(read_links(new_id)) == want


def migrate_to_tracker(source, target, profile: TrackerProfile, *, write: bool) -> dict:
    """Run (or preview) the move of a git store onto the tracker `profile` names; returns
    the report.

    Creates through the destination's own `create_spec`, in dependency order, so every
    predecessor link is born with its item. Raises `BackendRefusal` (nothing written) on a
    non-empty destination, an unreadable spec, an unresolvable reference or a document over
    the profile's ceiling; the dry run reports the same conditions instead."""
    infos, unreadable = read_front(source)
    items = [(int(i["id"]), i["phase"], reassemble(i)) for i in infos]
    docs = {spec_id: (phase, text) for spec_id, phase, text in items}
    order, unresolved = azure_order(items)
    oversize = [i for i, _, text in items
                if profile.ceiling is not None and len(text) > profile.ceiling]
    # Checked up front like the ceiling: a cap the destination refuses mid-batch would leave
    # the predecessors created and the dependent spec missing.
    overlinked = [i for i, _, text in items if profile.link_limit is not None
                  and len(azure_predecessor_ids(parse_frontmatter(text), i)) > profile.link_limit]
    occupied = len(target.list_specs())
    open_count = sum(1 for _, phase, _ in items if phase == "plans")
    report = {"ok": True, "from": source.name, "to": target.name, "dryRun": not write,
              "count": len(items), "open": open_count, "closed": len(items) - open_count,
              "unreadable": unreadable, "order": order, "idMap": {},
              "unresolved": unresolved, "oversize": oversize, "occupied": occupied,
              "configChange": {"file": CONFIG_FILE, "set": {"backend": profile.backend},
                               "note": "apply once the equality holds; the git store is "
                                       "left untouched and the id map above is the only "
                                       "record of the old ids"}}
    if profile.link_limit is not None:
        report["overlinked"] = overlinked
    blocked = bool(unreadable or unresolved or oversize or overlinked or occupied)
    if not write:
        report["diffs"] = []
        report["ok"] = not blocked
        return report
    if occupied:
        raise _refuse("sp-migrate-target-not-empty", f"the {profile.label} destination already "
                      f"lists {occupied} spec(s); a tracker create is not idempotent, so a "
                      "re-run would duplicate them — nothing was written")
    if unreadable:
        raise _refuse("sp-migrate-unreadable", "specs the source could not read: "
                      + ", ".join(str(u["id"]) for u in unreadable) + " — nothing was written")
    if unresolved:
        raise _refuse("sp-migrate-unresolved-references", "references to ids the front does "
                      "not hold, or a dependency cycle — nothing was written",
                      unresolved=unresolved)
    if oversize:
        raise _refuse("sp-migrate-description-ceiling", "documents over the description "
                      f"ceiling of {profile.ceiling} characters: "
                      + ", ".join(map(str, oversize)) + " — nothing was written")
    if overlinked:
        raise _refuse("sp-migrate-dependency-limit", f"specs waiting on more than "
                      f"{profile.link_limit} issues, which {profile.label} cannot link: "
                      + ", ".join(map(str, overlinked)) + " — nothing was written")
    idmap: dict[int, int] = {}
    created: dict[int, str] = {}
    current = None
    try:
        for current in order:
            phase, text = docs[current]
            created[current] = remap_text(text, idmap, members=False)
            locator = target.create_spec(phase, created[current])
            idmap[current] = int(re.search(r"(\d+)/?$", locator).group(1))
        for current in order:
            expected = remap_text(docs[current][1], idmap)
            if expected != created[current]:
                info, _ = target.read_spec(idmap[current])
                target.write_spec(info, expected)
    except BackendRefusal as exc:
        report.update(ok=False, idMap=idmap, failed={"id": current, "code": exc.err.get("code"),
                                                      "message": exc.err.get("message")})
        return report
    report["idMap"] = idmap
    left, right, mismatched, unlinked = [], [], [], []
    # The read-back is requests too: a refusal here keeps the report, whose id map is the
    # only record of the old ids once the items exist.
    try:
        for spec_id in order:
            phase, text = docs[spec_id]
            expected = remap_text(text, idmap)
            new = idmap[spec_id]
            left.append(derive_info({"id": new, "phase": phase}, expected))
            info, _ = target.read_spec(new)
            right.append(info)
            if info is None or not document_matches(expected, info):
                mismatched.append(spec_id)
            if info is not None and not links_match(expected, target, new):
                unlinked.append(spec_id)
    except BackendRefusal as exc:
        report.update(ok=False, failed={"id": spec_id, "stage": "verify",
                                        "code": exc.err.get("code"),
                                        "message": exc.err.get("message")})
        return report
    report["diffs"] = [{**d, "sourceId": next(k for k, v in idmap.items() if v == d["id"])}
                       for d in diff_fronts(left, [r for r in right if r])]
    report["identical"] = not mismatched
    report["mismatched"] = mismatched
    report["unlinked"] = unlinked
    report["ok"] = not report["diffs"] and not mismatched and not unlinked
    return report


def _cmd_migrate_tracker(args, root: str, out: Emitter, cfg: dict, profile: TrackerProfile,
                         open_target) -> int:
    if cfg.get("backend") != "git":
        return out.emit_err(args.json, {
            "code": "sp-migrate-source-not-git", "exit": 2, "backend": cfg.get("backend"),
            "message": f"`--to {profile.flag}` moves the `git` store onto {profile.label}; "
                       "this repository's backend is not `git`"})
    if args.thin_open_cards:
        return out.emit_err(args.json, {
            "code": "sp-migrate-thin-git-only", "exit": 2,
            "message": "--thin-open-cards belongs to `--to git`; there is no card to thin here"})
    source, err = open_git_backend(root)
    if err:
        return out.emit_err(args.json, err)
    target, err = open_target(root)
    if err:
        return out.emit_err(args.json, err)
    try:
        report = migrate_to_tracker(source, target, profile, write=args.write)
    except BackendRefusal as exc:
        return out.emit_err(args.json, exc.err)
    report["configChange"]["file"] = _config_file(root)
    if args.json:
        out.emit(True, report, "")
        return 0 if report["ok"] else 1
    verb = "migrated" if args.write else "dry run — would migrate"
    lines = [f"{verb} {report['count']} specs ({report['open']} open, {report['closed']} "
             f"archived) from the `quenching` branch to {profile.label}"]
    lines += [f"  unreadable: {u['id']} ({u['code']})" for u in report["unreadable"]]
    lines += [f"  unresolved: {u['id']} {u['kind']} {u['ref']}" for u in report["unresolved"]]
    lines += [f"  oversize: {i}" for i in report["oversize"]]
    lines += [f"  over the dependency cap: {i}" for i in report.get("overlinked", [])]
    if report["occupied"]:
        lines.append(f"  destination already lists {report['occupied']} spec(s)")
    if report.get("failed"):
        lines.append(f"  FAILED at {report['failed']['id']}: {report['failed']['message']}")
    lines += [f"  {old} -> {new}" for old, new in report["idMap"].items()]
    lines += [f"  diff: {d['sourceId']} -> {d['id']} {d['kind']} {d.get('fields', '')}"
              for d in report.get("diffs", [])]
    lines += [f"  document differs: {i}" for i in report.get("mismatched", [])]
    lines += [f"  dependencies differ: {i}" for i in report.get("unlinked", [])]
    if args.write:
        lines.append("equality: " + ("every item read back equals its source modulo id"
                                     if report["ok"] else "not proved"))
    lines.append(f"apply in {report['configChange']['file']}: " + json.dumps(report["configChange"]["set"]))
    if not args.write:
        lines.append("nothing was written — pass --write to create the work items")
    out.emit(False, report, "\n".join(lines))
    return 0 if report["ok"] else 1


def _config_file(root: str) -> str:
    """The configuration file this repository is actually read from, repo-relative: the
    `.agents/` home, or the legacy `.claude/quenching.json` while the home is absent."""
    repo = find_repo_root(root)
    return os.path.relpath(config_path(repo), repo)


def cmd_migrate(args, root: str, out: Emitter) -> int:
    cfg = load_config(root)
    if not getattr(args, "to", None):
        return out.emit_err(args.json, {
            "code": "sp-local-backend-removed", "exit": 2,
            "backend": cfg.get("provider") or cfg.get("backend"),
            "message": "`cq specs migrate` needs a destination: `--to git` moves the specs of "
                       "a tracker backend onto the `quenching` branch (the removed local "
                       "files backend has no migration)"})
    if args.to == "azure":
        return _cmd_migrate_tracker(args, root, out, cfg, AZURE_PROFILE, open_azure_backend)
    if args.to == "github":
        return _cmd_migrate_tracker(args, root, out, cfg, GITHUB_PROFILE, open_github_backend)
    if cfg.get("backend") == "git":
        return out.emit_err(args.json, {
            "code": "sp-migrate-already-git", "exit": 2,
            "message": "the repository already uses the `git` store; there is no tracker "
                       "front to migrate from"})
    if args.thin_open_cards and not args.write:
        return out.emit_err(args.json, {
            "code": "sp-migrate-thin-needs-write", "exit": 2,
            "message": "--thin-open-cards rewrites tracker items and only runs with --write"})
    source, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    target, err = open_git_backend(root)
    if err:
        return out.emit_err(args.json, err)
    report = migrate(source, target, dict(namespace(load_envelope(root), "specs")),
                     write=args.write, thin_cards=args.thin_open_cards)
    report["configChange"]["file"] = _config_file(root)
    if args.json:
        out.emit(True, report, "")
        return 0 if report["ok"] else 1
    verb = "migrated" if args.write else "dry run — would migrate"
    lines = [f"{verb} {report['count']} specs ({report['open']} open, {report['closed']} "
             f"archived) from {report['from']} to the `quenching` branch"]
    lines += [f"  unreadable: {u['id']} ({u['code']})" for u in report["unreadable"]]
    lines += [f"  diff: {d['id']} {d['kind']} {d.get('fields', '')}" for d in report["diffs"]]
    lines += [f"  card error: {e['id']}: {e['message']}" for e in report.get("cardErrors", [])]
    lines.append("equality: " + ("list --json matches the source modulo locator"
                                 if not report["diffs"]
                                 else f"{len(report['diffs'])} difference(s)"))
    lines.append(f"apply in {report['configChange']['file']}: " + json.dumps(report["configChange"]["set"])
                 + f" (card: {json.dumps(report['configChange']['card'])})")
    if not args.write:
        lines.append("nothing was written — pass --write to commit the batch")
    out.emit(False, report, "\n".join(lines))
    return 0 if report["ok"] else 1
