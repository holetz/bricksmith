"""The spec lifecycle contract — the vocabulary, the embedded schema and template,
and the readers that resolve both from the shipped assets.

Moved verbatim out of the pre-refactor specs script, with ONE adjustment: `ASSET_DIR` climbs three
levels rather than one, because this module sits at `assets/bin/quenching/specs/`
and the assets it reads are at `assets/specs/`. The old depth resolved to
`assets/bin/quenching/specs` — a directory that exists and holds no schema — and
the fallback below would have swallowed the mistake in silence, since a missing
asset is a legal state (an installed copy has none).

`HEADING_RE` is imported INSIDE the two functions that use it, never at module
level: `quenching.specs.parse` reaches back here for the vocabulary block through
its own `__init__`, so a top-level edge to `parse.text` would close an import
cycle the moment this module is the one imported first."""
from __future__ import annotations

import json
import os
import re

from quenching.common.io import read_text

HERE = os.path.dirname(os.path.abspath(__file__))
# `<...>/assets/bin/quenching/specs/` -> `<...>/assets/specs/`.
ASSET_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "..", "specs"))

VERIFICATION_POLICIES = ("per-task", "per-section", "end-of-plan")
DEFAULT_VERIFICATION = "per-section"
OUTCOMES = ("done", "abandoned")
# The four strategies `/quenching-specs conclude` offers. Two of them create no merge commit, so the
# record has no subject to name and carries an explicit none instead — a fact about the
# strategy, not a gap in the record.
MERGE_STRATEGIES = ("merge-commit", "squash", "rebase", "fast-forward")
MERGE_ANCHORLESS_STRATEGIES = ("rebase", "fast-forward")
# `gh pr merge --merge|--squash|--rebase` maps onto three of the four; `fast-forward` has no
# `gh` equivalent, so the PR route is never offered under it and `pr:` has nothing to record.
MERGE_NO_PR_STRATEGIES = ("fast-forward",)
RECORD_NONE_RE = re.compile(r"^none\b", re.IGNORECASE)

# --------------------------------------------------------------------------- #
# embedded assets (fallbacks when the sibling asset files are absent)
# Keep in lockstep with assets/specs/schema.json and assets/specs/templates/spec.md.
# --------------------------------------------------------------------------- #
DEFAULT_SCHEMA: dict = {
    "schema": "spec-lifecycle",
    "version": "3.0.0",
    "filename": {
        "pattern": r"^([a-z0-9]+(?:-[a-z0-9]+)*)\.md$",
        "groups": [],
        "example": "session-tokens.md",
    },
    "frontmatter": {
        "required": ["title", "date"],
        "optional": ["verification", "priority", "refined", "approved", "branch", "pr", "reviewed",
                     "merge", "buildCost", "run", "outcome", "workItemType", "epic", "dependsOn", "tags", "assignee", "start", "target", "end"],
        "verification": list(VERIFICATION_POLICIES),
        "outcome": list(OUTCOMES),
        "records": {
            "priority": {"fields": ["level", "criticality", "complexity", "date"],
                         "writtenBy": "triage", "writeOnce": False, "label": "spec:ranked",
                         "complexity": {"levels": ["low", "medium", "high", "xhigh"],
                                        "writtenBy": ["triage", "create", "develop"]}},
            "refined": {"fields": ["mode", "date"],
                        "writtenBy": "develop", "writeOnce": False,
                        "label": "spec:interrogated"},
            "approved": {"fields": ["date", "by"],
                         "by": {"levels": ["human", "session", "low-gear", "orchestrator-forced"]},
                         "writtenBy": "develop, or execute inline", "writeOnce": True,
                         "label": "spec:approved"},
            "branch": {"fields": ["base", "work"],
                       "writtenBy": "execute, or git:branch", "writeOnce": True},
            "pr": {"fields": ["number", "url", "date"],
                   "writtenBy": "git:pr:create", "writeOnce": False},
            "reviewed": {"fields": ["date"],
                         "writtenBy": "conclude", "writeOnce": False,
                         "label": "spec:reviewed"},
            "merge": {"fields": ["strategy", "subject", "pr"],
                      "strategies": list(MERGE_STRATEGIES),
                      "anchorless": list(MERGE_ANCHORLESS_STRATEGIES),
                      "noPr": list(MERGE_NO_PR_STRATEGIES),
                      "writtenBy": "git:merge", "writeOnce": True, "label": "spec:merged"},
            "buildCost": {"fields": ["status", "date", "source", "measurements", "reason"],
                          "status": {"levels": ["measured", "unavailable"]},
                          "writtenBy": "conclude", "writeOnce": False},
            "run": {"fields": ["state", "verifier", "by", "date"],
                    "state": {"levels": ["done", "continue", "blocked", "failed"]},
                    "verifier": {"levels": ["pass", "fail", "not-run"]},
                    "by": {"levels": ["verifier"]},
                    "writtenBy": "spec-runner", "writeOnce": False},
            "outcome": {"valuesFrom": "frontmatter.outcome",
                        "writtenBy": "conclude", "writeOnce": True},
        },
    },
    "sections": [
        {"heading": "Problem", "order": 1, "group": "definition", "moment": "decision"},
        {"heading": "Proposal", "order": 2, "group": "definition", "moment": "build"},
        {"heading": "Out of Scope", "order": 3, "group": "definition", "moment": "build"},
        {"heading": "Impact", "order": 4, "group": "definition", "moment": "build", "parsed": True},
        {"heading": "Validation", "order": 5, "group": "definition", "moment": "close"},
        {"heading": "Design", "order": 6, "group": "definition", "moment": "build"},
        {"heading": "Alternatives Considered", "order": 7, "group": "definition", "moment": "decision"},
        {"heading": "Open Decisions", "order": 8, "group": "definition", "moment": "decision"},
        {"heading": "Risks", "order": 9, "group": "definition", "moment": "decision"},
        {"heading": "Handoff", "order": 10, "group": "execution", "moment": "build"},
        {"heading": "Tasks", "order": 11, "group": "execution", "moment": "build"},
        {"heading": "Discoveries", "order": 12, "group": "execution"},
        {"heading": "Outcome", "order": 13, "group": "archive", "moment": "close"},
    ],
    "impact": {
        "parsedSubheading": "Standards this spec will write into /docs/standards/",
        "acceptedAliases": [
            "Standards this plan will write into /.docs/standards/",
            "Standards this spec will write into /.docs/standards/",
        ],
        "pathPrefix": "/docs/standards/",
    },
    "phases": [
        {"id": "plans", "folder": "plans", "role": "active",
         "entryGate": ["Problem"], "warnWhenEmpty": []},
        {"id": "archive", "folder": "archive", "role": "closed",
         "entryGate": ["Outcome"], "warnWhenEmpty": []},
    ],
    "promote": {
        "sequence": ["plans", "archive"],
        "explicitNone": "- none — <reason>",
        "filledRule": "An explicit none counts as FILLED. A heading present with an empty body "
                      "is malformed and refuses. An absent heading before its own gate is legal.",
        "openTasks": {"done": "refuse", "abandoned": "allow", "forceFlag": "--force"},
    },
    "stages": {
        "resolution": "last-match-wins",
        "derived": [
            {"id": "captured", "phase": "plans", "when": {"filled": ["Problem"]}},
            {"id": "proposed", "phase": "plans", "when": {"filled": ["Proposal"]}},
            {"id": "designed", "phase": "plans", "when": {"filled": ["Design"]}},
            {"id": "refined", "phase": "plans", "when": {"frontmatter": "refined"}},
            {"id": "ready", "phase": "plans", "gate": True,
             "when": {"filled": ["Problem", "Proposal", "Out of Scope", "Impact",
                                 "Validation", "Design", "Alternatives Considered",
                                 "Open Decisions", "Risks", "Tasks"]},
             "warnWhenEmpty": ["Handoff"]},
            {"id": "approved", "phase": "plans", "when": {"frontmatter": "approved"}},
            {"id": "executing", "phase": "plans",
             "when": {"taskState": ["x", "!"]},
             "label": "spec:built"},
        ],
    },
}


def _behavioral(node):
    """A schema with the prose stripped. `note`/`why` exist for a human reading
    schema.json; the embedded fallback has never carried them, and they are not what the
    tool branches on.

    Moved verbatim out of the pre-refactor specs script. Left behind when `DEFAULT_SCHEMA` moved
    here — without it, `test_specs_assets.py`'s schema-drift check falls back to a raw `==` between `schema.json` (which
    carries `note`/`why`) and `DEFAULT_SCHEMA` (which never has), a comparison that is `False`
    by construction and reports drift on every conformant pair."""
    if isinstance(node, dict):
        return {k: _behavioral(v) for k, v in node.items() if k not in ("note", "why")}
    if isinstance(node, list):
        return [_behavioral(v) for v in node]
    return node


# The FULL template, embedded VERBATIM so an installed copy with no adjacent assets can
# still stamp a capture AND pull any heading's guidance for `section --write`. It is a
# byte-for-byte copy of assets/specs/templates/spec.md — `test_specs_assets.py`'s
# `test_the_embedded_template_is_byte_for_byte_spec_md` proves it, and EDIT BOTH OR NEITHER.
TEMPLATE_SPEC = """---
title: <TITLE>
date: <DATE>
verification: <VERIFICATION>
---

# <TITLE>

<!-- ONE spec is ONE file for its whole lifecycle. Phases enrich it; they never split it.

     `.agents/quenching/bin/cq specs new` stamps the frontmatter and `## Problem` ALONE — a captured spec is four
     lines of body, not a thirteen-heading skeleton. Every other heading below is created on
     first write by `.agents/quenching/bin/cq specs section <id> "<Heading>" --write`, which inserts it in the
     canonical position with the guidance comment kept here.

     THE STAGE-SCOPED EXPLICIT-NONE RULE. A heading is required — and required to carry
     `- none — <reason>` when it has nothing in it — only once ITS OWN gate is reached:

       new (capture)        `## Problem`
       ready (derived)      the nine definition sections (`## Problem` .. `## Risks`)
                            AND `## Tasks`
       ready (warn only)    `## Handoff` non-empty
       promote -> archive/  `## Outcome`

     `ready` is a DERIVED STAGE, not a folder: a spec lives in `plans/` for its whole active
     life, and filling those ten sections is what makes it ready. Nothing refuses on that
     gate — it is a floor `execute` reports against, and the human's go-ahead is the
     `approved:` frontmatter record, asked for inline.

     Before its gate, a heading's absence is NOT an omission — it is a not-yet. After its
     gate, three rules decide whether a section counts as filled:

       1. `- none — <reason>` counts as filled. An omission and a null are different facts.
       2. A heading present with an EMPTY body is malformed and refuses. It is neither an
          answer nor a not-yet.
       3. An absent heading before its gate is legal.

     Headings are a PARSED contract — canonical English, exactly as written here. Body prose
     follows the repo's language. A heading outside this set is a stray and validate flags it.
     *(`standards/agents/communication.md` owns that language rule for a repo whose bundle has
     one. This template states it self-contained rather than citing it: the mold is stamped into
     repos that never adopted the bundle, where that path resolves to nothing.)*

     MOMENT. Each section belongs to one of three moments on the spec's timeline: `decision`
     (the human, deciding whether to build), `build` (the executor, in step 4 of
     `/quenching-specs execute`), `close` (`/quenching-specs conclude`, at archive time). `## Discoveries` belongs
     to none of them — captured indiscriminately while building, resolved later by
     `/quenching-specs develop`'s triage sweep on its own schedule. An orchestrator sends an executor
     exactly the `build` set; that is what lets one file serve every moment without bloating
     agent context. -->

## Problem

<!-- MOMENT: decision. Gate: new (capture).

     The problem or opportunity this spec answers, and why now. This is the only section a
     freshly captured spec carries — write it even if it is two sentences. -->

## Proposal

<!-- MOMENT: build. Gate: ready (derived).

     The change at a high level, in bullet points. What will be true afterwards that is not
     true now. -->

## Out of Scope

<!-- MOMENT: build. Gate: ready (derived).

     What this spec deliberately does NOT do, and why it was ruled out.

     Empty is written `- none — <reason>`. "We drew the boundary and nothing fell outside it"
     and "nobody ever drew the boundary" are different answers, and an absent section cannot
     tell them apart. -->

## Impact

<!-- MOMENT: build + PARSED. Gate: ready (derived).

     Declared scope for human review. The `### Standards this spec will write into
     /docs/standards/` sub-heading below is PARSED by `.agents/quenching/bin/cq specs validate`: every
     `/docs/standards/**.md` path bulleted under it must be named by a `## Tasks` item, or
     validate emits `sp-impact-uncovered` (warn). Keep that heading text verbatim — it is the
     anchor.

     Example of a parsed bullet:
       - `/docs/standards/naming/command-surface.md` — the bijection rule for wrappers

     The sibling sub-headings are prose for the reader and are deliberately NOT parsed: they
     name paths the spec never promised to write. A spec with no such sub-heading declares
     nothing and is never flagged — the check is opt-in by writing the heading. -->

### Standards this spec will write into /docs/standards/

- <path under /docs/standards/> — <the rule it states>

### Standards at `authority: background` this spec may resolve

- <path, or `none`>

### Product code this spec expects to touch

- `<path>` — <why>

## Validation

<!-- MOMENT: close (plus the agent's `verify:` fallback, resolved lazily). Gate: ready (derived).

     How anyone confirms this spec actually worked: the commands to run and the output they
     must produce, the fixtures to check, the invariants that must still hold afterwards.

     This section is LOAD-BEARING: a `## Tasks` item with no `verify:` line falls back to it.

     Empty is written `- none — <reason>`, which is a claim that the spec is unverifiable by
     construction. Make it on purpose or fill it in. -->

## Design

<!-- MOMENT: build. Gate: ready (derived).

     The choices made and their rationale, plus the background and binding contracts this
     design must not contradict. For each decision: what was chosen, why, and what was
     weighed against it.

     Empty is written `- none — <reason>` (e.g. "mechanical change, no design surface"). -->

## Alternatives Considered

<!-- MOMENT: decision. Gate: ready (derived).

     Whole-shape alternatives rejected at the spec level, each with the reason it lost.
     Per-decision alternatives can stay inside `## Design`; this section is for the ones that
     would have changed the spec's shape.

     Empty is written `- none — <reason>` (e.g. "only one viable approach"). -->

## Open Decisions

<!-- MOMENT: decision. Gate: ready (derived).

     What is deliberately still undecided, and how each will be decided — the evidence or the
     moment that settles it, not "TBD".

     Empty is written `- none — <reason>`. -->

## Risks

<!-- MOMENT: decision. Gate: ready (derived).

     What could go wrong, and the mitigation for each. A risk taken knowingly is written
     `ACCEPTED — <why>`; a silent failure mode is the shape to hunt for.

     Empty is written `- none — <reason>`. -->

## Handoff

<!-- MOMENT: build. Warned on when empty once the ready gate is met.

     The context an executor needs and cannot derive: the state of play, the conventions in
     force, what was already tried. Small by construction — it is sent with EVERY task.

     Refresh is bound to EVENTS, not judgment: the orchestrator rewrites this after each
     committed task. Staleness is this section's failure mode. -->

## Tasks

<!-- MOMENT: build. Gate: ready (derived).

     Checkboxes `- [ ] <id> <text>` grouped under `### N. <Section>` headings.
     `.agents/quenching/bin/cq specs task --spec <id> --check <task-id>` flips one mechanically — NEVER hand-edit the
     `[ ]` / `[x]` character. `--subject <line>` records the commit that implements it.

     AN ITEM IS EITHER A TASK OR A MEMBER SPEC. A checkbox whose text carries
     `— spec: #<id>` names another document; it is not work to do here. Its first token is
     the label, and `after:` orders it against other labels:

       - [ ] S1 Foundations — spec: #2138
       - [ ] S2 Migration — spec: #2139 — after: S1

     A spec with at least one spec item is a COMPOSITE and needs no `workItemType: epic`.
     `### N.` headings are waves: one wave is a feature, more than one is an epic. A
     composite may be a member of another, and a spec may belong to several. Its items'
     state is derived from the members and never ticked here (`.agents/quenching/bin/cq specs link add|remove`).

     A checkbox MAY carry indented metadata lines directly beneath it:

       - [ ] 3.2 Add rate limiting to the auth middleware
             files: src/middleware/auth.ts, src/config/limits.ts (new)
             pattern: src/middleware/cors.ts
             verify: pnpm test middleware/
             subject: plan/<id>: 3.2 Add rate limiting to the auth middleware

     files:    the paths this task may touch. Declaring them is what PERMITS the task to be
               handed to an executor sub-agent, and what makes a `[P]` marker checkable.
               A trailing parenthetical is closed grammar, and exactly two are reserved:
               `(new)` marks a path about to be created; `(operational)` declares a task
               that writes no repository file at all — the marker IS its whole `files:`,
               its proof is the `verify:`, and the verifier accepts it with no commit sha.
               Anything else is refused as `sp-files-annotation`; the marker beside a path
               is refused as `sp-files-operational`, and the marker with no `verify:` is
               warned as `sp-operational-no-verify`.
     pattern:  an existing file to imitate — the cheapest context an executor can be given.
     verify:   the command that proves the task done. WHEN it runs is the `verification`
               frontmatter policy, not this section's business. With no `verify:` line the
               task falls back to `## Validation`.
     subject:  written by `task --check --subject`, never by hand — the SUBJECT of the commit
               that implements this task, resolved by `git log --grep --fixed-strings`. It is
               known BEFORE the commit, so the box is ticked INTO the task's own commit
               instead of a bookkeeping commit that follows it. A spec built before this
               change carries `commit: <sha>`; both forms are read, neither is backfilled.

     `[P]` right after the id marks a task parallel-eligible:

       - [ ] 3.3 [P] Add the rate-limit config loader

     Set HERE, at definition time, and NEVER inferred while building. Honoured only when the
     marked tasks' `files:` sets are provably disjoint and none writes into `/docs/` —
     `.agents/quenching/bin/cq specs parallel` checks the disjunction mechanically rather than judging it in prose.
     Serial execution is the default and needs no marker.

     A BLOCKED task is a visible marker, not a hidden counter:

       - [!] 2.3 Implement the gate check — blocked: the vendor SDK has no hook for it

     Written by the orchestrator when it decides to stop retrying; `next` skips it. There is
     no attempt budget — an honest written reason serves better than a counter nobody sees. -->

### 1. <Section>

- [ ] 1.1 <first task>
- [ ] 1.2 <next task>

## Discoveries

<!-- MOMENT: none — triage, resolved by `/quenching-specs develop`'s discoveries bank whenever it runs,
     not tied to one of the three. No gate — appended during execution.

     One line per discovery, appended by `.agents/quenching/bin/cq specs discover <id> "<text>"` while building.
     Captured INDISCRIMINATELY: whether one is worth acting on is triage's judgment, not the
     executor's.

     The triage sweep resolves each entry IN PLACE, so provenance is never lost:

       - the rate limiter double-counts retries → promoted: fix-retry-accounting
       - the config loader is slow on cold start → dismissed: acceptable, runs once -->

## Outcome

<!-- MOMENT: close. Gate: promote -> archive/.

     What actually happened, written at archive time: what shipped, what was left out, what
     the next reader needs to know. `outcome: done | abandoned` is stamped into the
     frontmatter by `.agents/quenching/bin/cq specs promote --to archive`; this section is the prose behind it.

     For an abandoned spec, the reason it will not be built is the whole content. -->
"""


def load_schema() -> dict:
    p = os.path.join(ASSET_DIR, "schema.json")
    txt = read_text(p)
    if txt:
        try:
            obj = json.loads(txt)
            if isinstance(obj, dict) and obj.get("sections"):
                return obj
        except json.JSONDecodeError:
            pass
    return DEFAULT_SCHEMA


def load_template() -> str:
    """The FULL thirteen-section authoring reference — frontmatter, the contract preamble,
    and every heading with its guidance comment.

    Two consumers read it and they need different slices: `new` stamps only the capture
    form (below), while `section --write` pulls one heading's guidance when creating it.
    Keeping one source for both is what stops the guidance drifting from the contract."""
    txt = read_text(os.path.join(ASSET_DIR, "templates", "spec.md"))
    return txt if txt is not None else TEMPLATE_SPEC


def capture_form(template_text: str | None = None, schema: dict | None = None) -> str:
    """What `new` stamps: the frontmatter and the contract preamble, then the heading blocks
    the `plans` entry gate names — `## Problem` with its guidance, and nothing else.

    Sliced by GATE MEMBERSHIP, never by position. What makes a heading part of capture is the
    gate, not where it sits in the file, so read the gate.

    A captured spec is four lines of body, not a thirteen-heading skeleton. That is not
    cosmetic: the explicit-none rule makes `- none — <reason>` count as filled, so a spec
    born with thirteen headings would derive as `designed` and pass every promote gate
    without anyone having thought anything."""
    from quenching.specs.parse.text import HEADING_RE, split_lines      # deferred: see the module docstring
    text = template_text if template_text is not None else load_template()
    gate = phase_spec("plans", schema).get("entryGate", [])
    if not gate:
        return text
    lines = text.splitlines(keepends=True)
    preamble = None
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m and len(m.group(1)) == 2:
            preamble = "".join(lines[:i]).rstrip()
            break
    if preamble is None:
        return text
    blocks = [section_guidance(h, text).rstrip() for h in gate]
    return preamble + "\n\n" + "\n\n".join(blocks) + "\n"


def section_guidance(heading: str, template_text: str | None = None) -> str:
    """One heading's block from the template — the heading line plus its guidance comment,
    used by `section --write` when it creates a heading that does not exist yet."""
    from quenching.specs.parse.text import HEADING_RE, split_lines      # deferred: see the module docstring
    text = template_text if template_text is not None else load_template()
    lines = split_lines(text)
    want = heading.strip().lower()
    start = None
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m and len(m.group(1)) == 2:
            if start is not None:
                return "\n".join(lines[start:i]).rstrip() + "\n"
            if m.group(2).strip().lower() == want:
                start = i
    if start is not None:
        return "\n".join(lines[start:]).rstrip() + "\n"
    return f"## {heading}\n"


def canonical_headings(schema: dict | None = None) -> list[str]:
    s = schema or load_schema()
    return [x["heading"] for x in sorted(s["sections"], key=lambda d: d.get("order", 0))]


def headings_for_moment(moment: str, schema: dict | None = None) -> list[str]:
    """The canonical headings declared `moment: <moment>`, in canonical order.

    `## Discoveries` declares no `moment` — resolved on its own schedule by
    `/quenching-specs develop`'s triage sweep, not one of `decision` / `build` / `close` — so it never
    matches here, by construction rather than by exclusion list."""
    s = schema or load_schema()
    return [x["heading"] for x in sorted(s["sections"], key=lambda d: d.get("order", 0))
            if x.get("moment") == moment]


def phase_spec(phase: str, schema: dict | None = None) -> dict:
    s = schema or load_schema()
    for p in s.get("phases", []):
        if p.get("id") == phase:
            return p
    return {"id": phase, "folder": phase, "entryGate": [], "warnWhenEmpty": []}
