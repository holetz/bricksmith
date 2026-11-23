"""THE SINGLE DERIVATION — one spec's document reduced to its `info`, plus the stage
and the board state computed from it.

Moved verbatim out of the pre-refactor specs script."""
from __future__ import annotations

from quenching.common.frontmatter import parse_frontmatter
from quenching.specs.parse.sections import parse_sections, section_state
from quenching.specs.parse.tasks import parse_tasks
from quenching.specs.parse.text import body_after_frontmatter
from quenching.specs.schema import (DEFAULT_VERIFICATION, VERIFICATION_POLICIES,
                                    load_schema)


def _policy(fm: dict) -> str:
    v = str(fm.get("verification", "")).strip().lower()
    return v if v in VERIFICATION_POLICIES else DEFAULT_VERIFICATION


def derive_stage(spec: dict, sections: dict, fm: dict, tasks: list[dict],
                 schema: dict | None = None) -> str:
    """The sub-stage, COMPUTED from section completeness and frontmatter — never declared.

    Declared state is forgotten on edit and goes stale; derived state regresses on its own
    when a section empties. Rules are evaluated in schema order and the LAST match wins."""
    s = schema or load_schema()
    stage = spec["phase"]
    for rule in s.get("stages", {}).get("derived", []):
        if rule.get("phase") != spec["phase"]:
            continue
        if _stage_match(rule.get("when", {}), sections, fm, tasks):
            stage = rule["id"]
    return stage


def _stage_match(when: dict, sections: dict, fm: dict, tasks: list[dict]) -> bool:
    if "anyOf" in when:
        return any(_stage_match(w, sections, fm, tasks) for w in when["anyOf"])
    if "filled" in when:
        return all(section_state(sections, h) == "filled" for h in when["filled"])
    if "frontmatter" in when:
        return bool(fm.get(when["frontmatter"]))
    if "taskState" in when:
        want = set(when["taskState"])
        return any(t["state"] in want for t in tasks)
    return False


def derive_info(spec: dict, text: str) -> dict:
    """Everything derivable from one spec's document, in one pass.

    THE SINGLE DERIVATION. Every backend hands its canonical document here and gets the same
    `info` back — frontmatter, sections, tasks, stage and policy. No backend derives any of
    it, which is why "every backend behaves identically" is a property of the code rather
    than a claim to be re-tested per target."""
    fm = parse_frontmatter(text)
    sections = parse_sections(body_after_frontmatter(text))
    items = parse_tasks(text)
    # THE ONE SPLIT. `info["tasks"]` is work this document owns; `info["links"]` names
    # member specs (the composition). Keeping them in one list made a composite's progress
    # count a member as an unticked task; splitting here is what every consumer reads.
    links = [t for t in items if t["kind"] == "link"]
    tasks = [t for t in items if t["kind"] == "task"]
    info = dict(spec)
    info.update({
        "text": text,
        "frontmatter": fm,
        "sections": sections,
        "tasks": tasks,
        "links": links,
        "stage": derive_stage(spec, sections, fm, tasks),
        "verification": _policy(fm),
        # THE DATE COMES FROM THE DOCUMENT, not from the descriptor. It used to be the
        # basename's prefix, which every backend without filenames then had to fake. Reading
        # it here — in the one shared derivation — is what makes it the same fact in `files`,
        # in `github` and in a dict, and it is why no descriptor carries a `date` key at all.
        "date": str(fm.get("date", "")).strip(),
    })
    return info


def board_state_of(info: dict, member_states: list[dict] | None = None) -> str:
    """The `azureColumns` de-para's KEY, computed in the core and merely CONSULTED by the
    backend — `spec-backend.md` §The interface is the document, not the verbs already
    forbids a backend deriving anything of its own, and a board-state precedence is exactly
    that kind of derivation.

    PRECEDENCE: `archived` (the `archive` phase) > `reviewed` (the record `/quenching-specs conclude`
    stamps) > the derived stage. `reviewed` outranks the derived stage because it is the
    fact `plugin-configuration.md`'s `azureColumns` example maps to the `Aprovação` lane —
    the derived stage alone cannot tell "approved and executing" from "reviewed and awaiting
    merge", and the record is what does.

    A COMPOSITE FOLLOWS ITS MEMBERS: when any member is in flight (`in-progress`, `ready` or
    `unmerged`), its lane is `executing` even though the document itself has no ticked task.
    `member_states` is the card layer's already-derived list; None (the default) is the plain
    document answer every non-composite caller gets."""
    if info["phase"] == "archive":
        return "archived"
    if info["frontmatter"].get("reviewed"):
        return "reviewed"
    if member_states and any(str(m.get("status") or "") in
                             ("in-progress", "ready", "unmerged") for m in member_states):
        return "executing"
    return info["stage"]
