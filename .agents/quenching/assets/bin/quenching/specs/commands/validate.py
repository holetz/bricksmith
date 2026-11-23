"""`validate` — every finding for the whole workspace, in the `sp-*` vocabulary."""
from __future__ import annotations

import json
import os
import re

from quenching.common.frontmatter import frontmatter_anomalies
from quenching.common.output import exit_for
from quenching.specs.backends import open_backend
from quenching.specs.backends.base import SpecBackend
from quenching.specs.commands.output import Emitter, front_fields, read_one
from quenching.specs.parse import PHASES
from quenching.specs.members import origin_url, own_repo, read_members
from quenching.specs.parse.deps import dep_findings, deps_of, find_cycle, find_cycle_through
from quenching.specs.parse.epics import (composition_cycle, composition_findings,
                                         deprecation_findings, has_links, is_remote,
                                         parse_items, task_after_problems)
from quenching.specs.parse.edit import unclosed_fence_at
from quenching.specs.parse.text import body_after_frontmatter
from quenching.specs.parse.sections import (duplicate_headings, gate_report, parse_impact_standards, ready_report,
                                            section_spans, section_state, stray_headings)
from quenching.specs.parse.tasks import _files_bad_annotation, operational_entries
from quenching.specs.schema import (MERGE_ANCHORLESS_STRATEGIES, MERGE_NO_PR_STRATEGIES,
                                    MERGE_STRATEGIES, RECORD_NONE_RE, VERIFICATION_POLICIES,
                                    canonical_headings, load_schema)


# A task whose title or `verify:` runs a generator writes files; with `files:` empty the
# verifier fails the scope of what it wrote. Heuristic by design: a warning, never an error.
_GENERATOR_RE = re.compile(r"generate_|regenera|espelho", re.IGNORECASE)


def _finding(code: str, severity: str, message: str, **extra) -> dict:
    return {"code": code, "severity": severity, "message": message, **extra}


def validate_spec(backend: SpecBackend, s: dict,
                  emitter: Emitter | None = None, sink: dict | None = None) -> list[dict]:
    """Every finding for ONE provider spec, in the v2 `sp-*` vocabulary.

    The phase-scoped rule is asserted against the schema's per-phase sets — the SAME sets
    `promote` gates on, so the two can never drift into disagreeing about what a phase
    requires.

    ASKED OF THE BACKEND, never of the path — and the derivation is the shared one, so this
    validates the same `frontmatter`/`sections`/`tasks` every other command reads. Against
    GitHub the locator is an issue URL, so `read_text` returned nothing and EVERY spec was
    reported missing every required key: 210 fabricated findings on this repository, from
    documents that were entirely well-formed."""
    where = s.get("path") or f"{s['folder']}/{s['id']}"
    spec_id = str(s["id"])
    info, rerr = read_one(backend, s["id"], emitter or Emitter())
    if rerr or info is None:
        # A listing row can disappear between list and read. Deriving from an empty document
        # would fabricate findings from a document that was never read.
        return []
    text, fm = info["text"], info["frontmatter"]
    sections, tasks = info["sections"], info["tasks"]
    out: list[dict] = []
    if sink is not None:
        sink[spec_id] = info      # the epic cross-check reads what this sweep already read

    # BEFORE the required-key checks, so a parse failure is never presented as a
    # content gap — a value this parser could not represent used to surface as a
    # missing `title`, naming the absence rather than the misread that caused it.
    for a in frontmatter_anomalies(text):
        out.append(_finding("sp-frontmatter-unparsed", "warn",
                            f"{where}: `{a['key']}`: {a['detail']}", spec=spec_id, path=where,
                            kind=a["kind"], key=a["key"],
                            remedy="quote the value, or write the comment on its own line — "
                                   "see /docs/standards/code/frontmatter-parser.md"))

    schema = load_schema()
    for key in schema.get("frontmatter", {}).get("required", []):
        if not str(fm.get(key, "")).strip():
            out.append(_finding("sp-missing-frontmatter", "error",
                                f"{where}: frontmatter has no `{key}`", spec=spec_id,
                                path=where, remedy=f"add `{key}:` to the frontmatter"))
    pol = str(fm.get("verification", "")).strip().lower()
    if pol and pol not in VERIFICATION_POLICIES:
        out.append(_finding("sp-bad-verification", "error",
                            f"{where}: verification `{pol}` is not one of "
                            f"{', '.join(VERIFICATION_POLICIES)}", spec=spec_id, path=where,
                            remedy=f"set verification to one of {', '.join(VERIFICATION_POLICIES)}"))
    priority = fm.get("priority")
    if isinstance(priority, dict):
        levels = (schema.get("frontmatter", {}).get("records", {}).get("priority", {})
                  .get("complexity", {}).get("levels", []))
        comp = str(priority.get("complexity", "")).strip().lower()
        if comp and levels and comp not in levels:
            out.append(_finding("sp-bad-complexity", "warn",
                                f"{where}: priority.complexity `{comp}` is not one of "
                                f"{', '.join(levels)}", spec=spec_id, path=where,
                                remedy=f"set priority.complexity to one of {', '.join(levels)}"))

    for h in stray_headings(sections, schema):
        out.append(_finding("sp-stray-heading", "warn",
                            f"{where}: `## {h}` is not one of the thirteen canonical headings",
                            spec=spec_id, path=where, heading=h,
                            # A REMEDY NAMES AN ACTION THE SURFACE OFFERS. This one read "fold
                            # it into one" for as long as no command could — `cmd_section`
                            # refused a non-canonical heading before it ever looked at
                            # `--write`, so the only way to close the finding was to edit the
                            # document outside the tool. `--fold` is that action now.
                            remedy=(f"close it with `cq specs section --fold` — `cq specs "
                                    f"section {spec_id} --fold \"{h}\"` demotes it into the "
                                    "canonical section above, text preserved")))

    # A fence the document never closes makes `parse_sections` read every later `## ` as code:
    # those sections vanish from every read without a word, and the next write of the section
    # holding the opener deletes them. `section --write` refuses such a body
    # (`sp-write-unclosed-fence`); this names one that reached the store another way.
    if found := unclosed_fence_at(text):
        lineno, opener = found
        holder = next((h for h, a, b in section_spans(text) if a < lineno - 1 < b), None)
        where_in = f" inside `## {holder}`" if holder else ""
        out.append(_finding("sp-unclosed-fence", "error",
                            f"{where}: line {lineno} opens `{opener}`{where_in} and the fence "
                            f"never closes — every `## ` after it reads as code",
                            spec=spec_id, path=where, line=lineno, opener=opener,
                            heading=holder,
                            remedy=(f"close the block: `cq specs section {spec_id} \"{holder}\"` "
                                    "reads the swallowed sections back inside its body; write "
                                    f"them back with the fence closed through `cq specs section "
                                    f"{spec_id} \"{holder},<each swallowed heading>\" --write`"
                                    if holder else
                                    f"close the block opened at line {lineno}")))

    # The phase-scoped rule governs whether a heading must be PRESENT — so `missing` is
    # checked only against the gate of the phase this spec is IN.
    gates = gate_report({"sections": sections}, s["phase"], schema)
    for h in gates["missing"]:
        out.append(_finding("sp-gate-unmet", "warn",
                            f"{where}: `## {h}` is required in {s['phase']}/ and is absent",
                            spec=spec_id, path=where, heading=h,
                            remedy=f"cq specs section {spec_id} \"{h}\" --write"))
    # Malformed is NOT phase-scoped. Once a heading exists it must say something, in any
    # phase: it is neither an answer nor a not-yet, and leaving it for the promote to catch
    # means a spec looks fine right up until the gate refuses it.
    for h in canonical_headings(schema):
        if section_state(sections, h) == "empty":
            out.append(_finding("sp-empty-section", "error",
                                f"{where}: `## {h}` is present but empty — neither an answer "
                                f"nor a not-yet", spec=spec_id, path=where, heading=h,
                                remedy="fill it, or write `- none — <reason>`"))
    # A canonical heading written twice: `parse_sections` keeps the FIRST, so an empty first copy
    # reads as the section while the real text sits under the second — the 1315 shape.
    for h in duplicate_headings(body_after_frontmatter(text)):
        out.append(_finding("sp-duplicate-heading", "error",
                            f"{where}: `## {h}` appears more than once — the parser reads only "
                            f"the first", spec=spec_id, path=where, heading=h,
                            remedy=f"cq specs section {spec_id} \"{h}\" --write  (the rewrite "
                                   "drops the later copies)"))
    # `Handoff` is warned on by the derived ready gate: a spec that is buildable but has
    # no executor context is incomplete for the next command.
    ready = ready_report({"sections": sections}, schema) if s["phase"] == "plans" else None
    if ready and ready["ok"]:
        for h in ready["warn"]:
            out.append(_finding("sp-handoff-empty", "warn",
                                f"{where}: `## {h}` is empty in a spec that meets the ready "
                                f"gate — an executor gets no context", spec=spec_id,
                                path=where, heading=h,
                                remedy="rewrite it after each committed task"))

    declared = parse_impact_standards(text, schema)
    if declared:
        named = "\n".join(t["text"] for t in tasks)
        for p in declared:
            if p not in named:
                out.append(_finding("sp-impact-uncovered", "warn",
                                    f"{where}: `{p}` is declared under ## Impact but no task "
                                    f"names it", spec=spec_id, path=where, standard=p,
                                    remedy="add a task that writes it, or drop the declaration"))

    # A `files:` entry carrying a parenthetical that is not one of the reserved annotations
    # is a human comment the parser must not interpret — kept whole it would reach an
    # executor as a path that exists nowhere, which is the exact silence `_split_files`
    # exists to stop. Refuse it at the points that hand paths out and report it here.
    for t in tasks:
        for entry in t["files"]:
            note = _files_bad_annotation(entry)
            if note:
                out.append(_finding("sp-files-annotation", "error",
                                    f"{where}: task {t['id'] or t['index']} declares files "
                                    f"entry {entry!r} with annotation `({note})` — only "
                                    f"`(new)` and `(operational)` are reserved", spec=spec_id,
                                    path=where, task=t["id"] or t["index"], entry=entry,
                                    remedy="remove the comment — `(new)` and `(operational)` "
                                           "are the only reserved `files:` annotations"))

    # `(operational)` is EXCLUSIVE. The parser hands an operational task on as `files: []`,
    # so a path beside the marker survives here as a mixed declaration: the marker would
    # hide that path from the verifier's collection of declared paths, which is the one
    # guard that makes the annotation safe to trust. And a marker with no `verify:` has
    # nothing to prove it — the annotation would then assert a task nobody can check.
    for t in tasks:
        if t["operational"]:
            if not t["verify"]:
                out.append(_finding("sp-operational-no-verify", "warn",
                                    f"{where}: task {t['id'] or t['index']} is "
                                    f"`(operational)` with no `verify:` — its only proof is "
                                    f"missing", spec=spec_id, path=where,
                                    task=t["id"] or t["index"],
                                    remedy="declare the `verify:` the task is proved by"))
            continue
        mixed = operational_entries(t["files"])
        if mixed:
            out.append(_finding("sp-files-operational", "error",
                                f"{where}: task {t['id'] or t['index']} declares "
                                f"`(operational)` beside a path — the annotation is "
                                f"exclusive: an operational task writes no repository file",
                                spec=spec_id, path=where, task=t["id"] or t["index"],
                                remedy="keep `(operational)` as the whole `files:`, or "
                                       "declare only the paths the task writes"))

    for t in tasks:
        # `files: []` is parsed as the single entry `[]`: the explicit empty declaration.
        if any(f.strip() != "[]" for f in t["files"]) or t["checked"]:
            continue
        if _GENERATOR_RE.search(f"{t['text']} {t['verify'] or ''}"):
            out.append(_finding("sp-generator-no-files", "warn",
                                f"{where}: task {t['id'] or t['index']} runs a generator but "
                                f"declares no `files:` — the verifier fails the scope of what "
                                f"it writes", spec=spec_id, path=where,
                                task=t["id"] or t["index"],
                                remedy="declare the generator's outputs in the task's `files:`"))

    # `next` opens a task only once every `after:` label is done, so a label nothing carries or
    # a loop between tasks leaves the spec waiting forever with no line named.
    after = task_after_problems(info)
    for u in after["unknown"]:
        out.append(_finding("sp-after-unknown", "error",
                            f"{where}: task {u['task']} comes after `{u['label']}`, which is "
                            f"neither a task nor a spec item", spec=spec_id, path=where,
                            task=u["task"], dependency=u["label"], line=u["line"],
                            remedy="correct or remove the label in the task's `after:`"))
    if after["cycle"]:
        out.append(_finding("sp-after-cycle", "error",
                            f"{where}: task dependency cycle {' -> '.join(after['cycle'])}",
                            spec=spec_id, path=where, cycle=after["cycle"],
                            remedy="remove one `after:` in the reported cycle"))

    # Judged against the READY gate: a spec is "unrefined" once it could be built, not the
    # moment it is captured. Warning on every fresh capture would train the reader to
    # ignore the code.
    if ready and ready["ok"] and not fm.get("refined"):
        out.append(_finding("sp-unrefined", "warn",
                            f"{where}: ready to build, but nobody has interrogated it",
                            spec=spec_id, path=where,
                            remedy="run a refinement pass, or build as-is (never gated)"))
    if s["phase"] == "archive" and not fm.get("outcome"):
        out.append(_finding("sp-no-outcome", "warn",
                            f"{where}: archived with no `outcome:` — done and abandoned "
                            f"read alike", spec=spec_id, path=where,
                            remedy="stamp `outcome: done` or `outcome: abandoned`"))
    merge_finding = merge_record_finding(fm, where, spec_id)
    if merge_finding:
        out.append(merge_finding)
    return out


def merge_record_finding(fm: dict, where: str, spec_id: str) -> dict | None:
    """Whether a `merge:` record still says which commit carries the merge.

    TWO forms are legal and both are read forever: the current `{strategy, subject}`, and
    `{strategy, commit}` on a spec archived before the anchor became the subject. The older
    one is never rewritten — a recorded sha describes a commit that exists, and editing an
    archived spec to "fix" it would be a lie about when the record was made."""
    rec = fm.get("merge")
    if not rec:
        return None
    remedy = ("stamp `merge: {strategy: <one of " + ", ".join(MERGE_STRATEGIES) +
              ">, subject: <the merge commit's subject>}`")
    if not isinstance(rec, dict):
        return _finding("sp-bad-merge", "warn",
                        f"{where}: `merge:` is not a {{strategy, subject}} record",
                        spec=spec_id, path=where, remedy=remedy)
    strategy = str(rec.get("strategy", "")).strip().lower()
    subject = str(rec.get("subject", "")).strip()
    if strategy not in MERGE_STRATEGIES:
        return _finding("sp-bad-merge", "warn",
                        f"{where}: merge strategy `{strategy or '(unset)'}` is not one of "
                        f"{', '.join(MERGE_STRATEGIES)}", spec=spec_id, path=where,
                        remedy=remedy)
    if not subject:
        if str(rec.get("commit", "")).strip():
            return None          # the older form, read and left exactly as it was written
        return _finding("sp-bad-merge", "warn",
                        f"{where}: `merge:` names a strategy but nothing to resolve the "
                        f"merge by", spec=spec_id, path=where, remedy=remedy)
    anchorless = strategy in MERGE_ANCHORLESS_STRATEGIES
    explicit_none = bool(RECORD_NONE_RE.match(subject))
    if anchorless and not explicit_none:
        return _finding("sp-bad-merge", "warn",
                        f"{where}: `{strategy}` creates no merge commit, so `subject:` has "
                        f"nothing to point at", spec=spec_id, path=where,
                        remedy="write `subject: none — <why>`")
    if not anchorless and explicit_none:
        return _finding("sp-bad-merge", "warn",
                        f"{where}: `{strategy}` creates a merge commit, so `subject:` must "
                        f"name it rather than be an explicit none", spec=spec_id, path=where,
                        remedy=remedy)
    if str(rec.get("pr", "")).strip() and strategy in MERGE_NO_PR_STRATEGIES:
        return _finding("sp-bad-merge", "warn",
                        f"{where}: `pr:` is set but `{strategy}` has no `gh pr merge` "
                        f"equivalent — the PR route is never offered under it",
                        spec=spec_id, path=where, remedy="drop `pr:`, or record a strategy "
                        f"`gh pr merge` supports ({', '.join(s for s in MERGE_STRATEGIES if s not in MERGE_NO_PR_STRATEGIES)})")
    return None


BY_CODE_SPECS_SHOWN = 6


def epic_sweep(backend: SpecBackend, read: dict, root: str | None = None) -> list[dict]:
    """The composition findings for the specs a sweep already read (`read`: id -> info).

    Only an id the sweep did not read — a member outside a narrowed `--spec`, or one in another
    repository's store — costs one more `read_specs` call per store, so a full sweep of local
    members adds no reads at all. A reference-form `epic:` on a member is never read."""
    wanted: set[str] = set()
    for info in read.values():
        wanted.update(str(i["spec"]) for i in parse_items(info) if i["spec"])
    missing = sorted(wanted - set(read))
    got, unreadable = read_members(backend, missing, root) if missing else ({}, set())
    known = {**read, **got}
    out: list[dict] = []
    self_repo = own_repo(origin_url(root)) if root else None
    for info in read.values():
        if has_links(info):
            out.extend(composition_findings(info, known, unreadable, self_repo))
        out.extend(deprecation_findings(info))
    cycle = composition_cycle(known)
    if cycle:
        out.append({"code": "sp-link-member-cycle", "severity": "error",
                    "message": f"composition cycle {' -> '.join(cycle)}",
                    "spec": cycle[0], "cycle": cycle,
                    "remedy": "remove one member link in the reported cycle"})
    return out


def dependency_sweep(backend: SpecBackend, read: dict[str, dict]) -> list[dict]:
    """Check the swept specs against their dependency closure, reading extra ids once.

    A narrowed sweep still needs existing targets and cycle paths outside its selection.
    Extra specs supply graph context; their own findings are outside the requested scope.
    """
    known = dict(read)
    attempted = set(read)
    pending = {dep for info in read.values() for dep in deps_of(info["frontmatter"])}
    while missing := sorted(pending - attempted):
        attempted.update(missing)
        got = backend.read_specs(missing)
        pending = set()
        for spec, info in got.items():
            if info is not None:
                known[spec] = info
                pending.update(deps_of(info["frontmatter"]))

    remedies = {
        "sp-dep-missing": "correct or remove the missing id in `dependsOn`",
        "sp-dep-self": "remove the spec's own id from `dependsOn`",
        "sp-dep-cycle": "remove a dependency in the reported cycle from `dependsOn`",
        "sp-dep-dropped": "review and remove or replace the dropped dependency in `dependsOn`",
    }
    findings = dep_findings(known)
    # `dep_findings` reports one cycle; a scoped sweep may need another one, so cut the
    # reported cycle's closing edge and look again until none is left. Cutting an edge, not
    # its nodes, keeps some overlapping cycles; the scoped sweep also searches a cycle
    # through each swept spec on the uncut graph.
    edges = {spec: [d for d in deps_of(info["frontmatter"]) if d != spec]
             for spec, info in known.items()}
    original = {k: list(v) for k, v in edges.items()}
    seen = set()
    cycle = next((f["cycle"] for f in findings if f["code"] == "sp-dep-cycle"), None)
    while cycle:
        seen.add(frozenset(cycle))
        edges[cycle[-2]] = [d for d in edges[cycle[-2]] if d != cycle[-1]]
        cycle = find_cycle(edges)
        if cycle and frozenset(cycle) not in seen:
            findings.append({
                "code": "sp-dep-cycle", "severity": "error", "spec": cycle[0],
                "cycle": cycle, "message": "dependency cycle: " + " -> ".join(cycle),
            })
    for spec in sorted(read):
        cycle = find_cycle_through(original, spec)
        if cycle and frozenset(cycle) not in seen:
            seen.add(frozenset(cycle))
            findings.append({
                "code": "sp-dep-cycle", "severity": "error", "spec": cycle[0],
                "cycle": cycle, "message": "dependency cycle: " + " -> ".join(cycle),
            })
    return [_finding(**finding, remedy=remedies[finding["code"]])
            for finding in findings
            if finding["spec"] in read or set(finding.get("cycle", [])) & set(read)]


def _emit_by_code(args, root: str, specs: list, findings: list, errors: list) -> int:
    """The same sweep, one line per `(code, severity)` instead of one per finding.

    A front large enough to need this is exactly the one whose full payload will not fit: 147
    specs here produce 26 KB of findings that a reader turns into this counting anyway. The
    grouping never filters — every finding is counted, and the elision is of the ID list,
    which is why the count and the shown IDs can differ."""
    groups: dict[tuple[str, str], list[str]] = {}
    for f in findings:
        groups.setdefault((f["code"], f["severity"]), []).append(str(f.get("spec") or "—"))
    ordered = sorted(groups.items(), key=lambda kv: (kv[0][1] != "error", -len(kv[1]), kv[0][0]))
    rows = [{"code": code, "severity": sev, "count": len(ids),
             "specs": sorted(set(ids), key=str)} for (code, sev), ids in ordered]
    if args.json:
        print(json.dumps({"ok": not errors, **front_fields(root), "specs": len(specs),
                          "byCode": rows}, indent=2, ensure_ascii=False))
        return exit_for(findings)
    print(f"specs validate — {root} ({len(errors)} error(s), "
          f"{len(findings) - len(errors)} warning(s)) by code")
    if not rows:
        print("  OK — every spec conforms.")
    for r in rows:
        shown = r["specs"][:BY_CODE_SPECS_SHOWN]
        more = len(r["specs"]) - len(shown)
        tail = ", ".join(shown) + (f", … {more} more" if more else "")
        print(f"  [{r['severity']:<5}] {r['code']:<26} {r['count']:>3}  {tail}")
    return exit_for(findings)


def cmd_validate(args, root: str, out: Emitter) -> int:
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    phase = getattr(args, "phase", None)
    specs = backend.list_specs(phase)
    findings: list[dict] = []

    for ph in ([phase] if phase else PHASES):
        d = os.path.join(root, ph)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name == "index.md" or name.startswith("."):
                continue
            if os.path.isdir(os.path.join(d, name)):
                # archive/ is DELIBERATELY not migrated — it is historical and read-only, so
                # its v1 `YYYY-MM-DD-<name>/` plan folders are expected, not strays. Flagging
                # them would push a reader to migrate the one tree the design protects.
                if ph == "archive":
                    continue
                findings.append(_finding("sp-stray-dir", "warn",
                                         f"{ph}/{name}/ is a directory — v2 specs are files",
                                         path=f"{ph}/{name}",
                                         remedy="a v1 plan folder? run `cq specs migrate`"))

    target = [s for s in specs if str(s["id"]) == str(args.spec)] if args.spec else specs
    if args.spec and not target:
        out.emit(args.json, {"ok": False, "code": "sp-unknown-id", "id": args.spec,
                             "message": f"no spec with id '{args.spec}'"},
                 f"error: no spec with id '{args.spec}'")
        return 1
    read: dict[str, dict] = {}
    for s in target:
        findings.extend(validate_spec(backend, s, out, sink=read))
    findings.extend(epic_sweep(backend, read, root))
    findings.extend(dependency_sweep(backend, read))

    errors = [f for f in findings if f["severity"] == "error"]
    if getattr(args, "by_code", False):
        return _emit_by_code(args, root, specs, findings, errors)
    if args.json:
        print(json.dumps({"ok": not errors, **front_fields(root), "specs": len(specs),
                          "findings": findings}, indent=2, ensure_ascii=False))
    else:
        print(f"specs validate — {root} ({len(errors)} error(s), "
              f"{len(findings) - len(errors)} warning(s))")
        for f in findings:
            print(f"  [{f['severity']:<5}] {f['message']}  ({f['code']})")
            if f.get("remedy"):
                print(f"          remedy: {f['remedy']}")
        if not findings:
            print("  OK — every spec conforms.")
    # Errors fail; warnings are reported. This line read `1 if findings else 0` for as long as the
    # verb existed, which contradicted the `"ok": not errors` two lines above it — a warn-only run
    # printed `"ok": true` and exited 1 — and contradicted `components lint`, `components doctor`
    # and `common.output.exit_for`, all three of which count `severity == "error"` alone. Fifteen
    # warn codes reach here (`sp-unrefined`, `sp-no-outcome`, `sp-impact-uncovered`, the six
    # `sp-bad-merge` arms among them), so any conductor gating on this exit code failed on advice.
    # `exit_for` rather than a fourth copy of the predicate: the rule is one, so its implementation
    # is one.
    return exit_for(findings)
