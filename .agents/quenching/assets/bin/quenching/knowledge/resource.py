"""`resource` — what a doc claims to govern, how that claim is parsed, and whether it holds.

Moved verbatim out of the pre-refactor OKF validator script; only `parse_frontmatter`'s call
shape changed, from the `(fm, has_block, well_formed)` tuple to the bare dict
`quenching.common.frontmatter` returns.

RESOURCE INTEGRITY (per-doc; WARN — a doc that is provably lying about itself)
- **`resource-unresolved`** a path- or glob-shaped `resource` entry matching nothing
  on disk. `uri` entries are never resolved, and an entry carrying glob syntax this
  validator does not implement is classified `unknown` and never reported.
- **`resource-self`** the doc's own path falls inside the scope its `resource`
  declares. Such a doc governs nothing and is eternally fresh, which is what makes
  the rule load-bearing rather than cosmetic.
"""
from __future__ import annotations

import fnmatch
import glob
import os

from quenching.common.frontmatter import parse_frontmatter
from quenching.knowledge.schema import (RESOLVABLE_KINDS, UNSUPPORTED_GLOB_CHARS,
                                        _nonempty)


def _is_uri(value: str) -> bool:
    """True for a scheme-bearing target (`https://…`, `mailto:…`) — something that
    points outside the checkout and is never resolved against disk."""
    return "://" in value or value.lower().startswith(("mailto:", "tel:"))


def parse_resource(value: str) -> list[tuple[str, str]]:
    """Split a `resource` value into `(entry, kind)` pairs, in written order.

    A comma-separated list of globs and paths is a *plugin convention*, not an OKF
    rule — three of the five real values in this repo's own bundle are lists and
    nothing documented the format, so it is parsed here and stated in
    `/docs/standards/quality/bundle-verification.md`.

    kind is one of:
      `path`    a plain repo-root-relative path.
      `glob`    a path carrying `*` / `**`, the only two wildcards implemented.
      `uri`     a scheme-bearing target — outside the checkout, never resolved.
      `unknown` carries glob syntax this validator does not implement (braces,
                character classes, `?`). Reported as unknown, NEVER as a
                violation: no observed value uses them, and guessing at syntax
                nobody writes would turn a WARN into noise.
    """
    return [(entry, _resource_kind(entry)) for entry in _split_entries(value)]


def _split_entries(value: str) -> list[str]:
    """The comma-separated list, split WITHOUT cutting inside a `{…}` group.

    A plain `.split(",")` defeated the `unknown` classification it feeds. Brace expansion is
    glob syntax this validator does not implement, and `_resource_kind` answers `unknown` for it
    precisely so it is never judged — but splitting first shreds one written entry into pieces,
    and the INNER pieces of a group carry no brace at all. `a/{x.md,y.md,z.md}` became `a/{x.md`
    (unknown, skipped), `y.md` (a bare `path`, reported as matching nothing on disk) and `z.md}`
    (unknown, skipped). Measured on the plugin's own bundle the day it was fixed: `ops-front.md`
    and `ops`, two WARNs against entries nobody wrote, on a doc whose real `resource` resolves.
    """
    entries: list[str] = []
    depth, current = 0, []
    for char in str(value or ""):
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            entries.append("".join(current))
            current = []
        else:
            current.append(char)
    entries.append("".join(current))
    return [entry.strip() for entry in entries if entry.strip()]


def _resource_kind(entry: str) -> str:
    if _is_uri(entry):
        return "uri"
    if any(ch in entry for ch in UNSUPPORTED_GLOB_CHARS):
        return "unknown"
    return "glob" if "*" in entry else "path"


def _project_root(bundle_root: str) -> str:
    """The checkout root a `resource` entry is written relative to — the bundle's
    parent. Observed values are repo-root-relative (`plugins/…/SKILL.md`); the
    bundle-aggregate `/docs/**` is absolute and never resolves as a path here —
    the leading slash turns the join absolute — so the aggregate is exempted by
    name (resource-self, stale-doc) rather than by resolution."""
    return os.path.dirname(os.path.abspath(bundle_root))


def _resource_resolves(entry: str, kind: str, project_root: str) -> bool:
    """True when the entry matches at least one path in the checkout.

    Stops at the FIRST hit (`iglob`, not `glob`): answering "does this resolve?"
    runs on every concept doc in the hook path, and materializing every match of a
    `/docs/**` would walk the whole tree once per doc under the Stop deadline.
    `glob.escape` covers a checkout whose own path contains a glob metacharacter.
    """
    entry = _root_relative(entry)
    if kind == "glob":
        pattern = os.path.join(glob.escape(project_root), entry)
        return next(glob.iglob(pattern, recursive=True), None) is not None
    return os.path.exists(os.path.join(project_root, entry))


def _root_relative(entry: str) -> str:
    """An entry as a path relative to the CHECKOUT root.

    A leading slash means the checkout root, not the filesystem root. That is the form the OKF
    prose and the shipped skeleton both write — `/docs/**`, `/.specs/**`, `/.design/**` — and the
    containment half of this module has always read it that way, since `_entry_contains` and
    `_glob_contains` both `strip("/")`. Resolution did not: `os.path.join(root, "/docs/**")`
    discards the root and globs from `/`, so the SAME entry `matched nothing on disk` two lines
    above where it counted as a bundle aggregate. The two halves of one check disagreed about
    what a leading slash means; this is the half that was wrong. Measured on the plugin's own
    bundle the day it was fixed: 8 of 12 `resource-unresolved` were only this asymmetry.

    `lstrip`, not `strip`: a TRAILING slash is a real claim — `docs/standards/naming/` asks for a
    directory — and resolution keeps it.
    """
    return entry.lstrip("/")


def _glob_contains(pattern: str, rel_path: str) -> bool:
    """Segment-wise match of a `*`/`**` glob against a forward-slash relative path.

    `fnmatch` is deliberately NOT used for this: its `*` also matches `/`, so
    `/docs/*` would claim to contain any deeper path, `/docs/standards/<subject>/<doc>.md`
    included, and raise a false `resource-self` — and since the skills treat every WARN as must-fix, a false
    positive here costs more than a missed one. `*` matches inside one segment;
    `**` matches any number of segments, including none.
    """
    pat = [p for p in pattern.strip("/").split("/") if p]
    parts = [p for p in rel_path.strip("/").split("/") if p]

    def walk(pi: int, si: int) -> bool:
        while pi < len(pat):
            if pat[pi] == "**":
                if pi + 1 == len(pat):
                    return True                     # a trailing `**` swallows the rest
                return any(walk(pi + 1, k) for k in range(si, len(parts) + 1))
            if si >= len(parts) or not fnmatch.fnmatch(parts[si], pat[pi]):
                return False
            pi += 1
            si += 1
        return si == len(parts)

    return walk(0, 0)


def _entry_contains(entry: str, kind: str, rel_doc: str) -> bool:
    """True when `rel_doc` falls inside the scope the entry declares."""
    if kind == "glob":
        return _glob_contains(entry, rel_doc)
    ent = entry.strip("/")
    return rel_doc == ent or rel_doc.startswith(ent + "/")


def _rel_to_project(target: str, project_root: str) -> str:
    """`target` as a forward-slash path relative to the checkout root."""
    return os.path.relpath(os.path.abspath(target), project_root).replace(os.sep, "/")


def _is_bundle_aggregate(entry: str, kind: str, bundle_rel: str) -> bool:
    """True when the entry's scope covers the whole bundle rather than merely this doc.

    Both `resource-self` and `stale-doc` exempt these, for the same reason: a scope
    containing the bundle root contains every doc in it, so it is touched whenever the
    doc itself is. Named once so the two checks cannot drift on what "aggregate" means.
    """
    return _entry_contains(entry, kind, bundle_rel)


def check_resource(text: str, path: str, bundle_root: str) -> list[tuple[str, str, str]]:
    """`resource` integrity for one concept doc, as (severity, code, message).

    `uri` and `unknown` entries are skipped rather than reported: neither can be
    resolved against the checkout, and flagging a glob syntax this validator never
    implemented would be exactly the noise `parse_resource` classifies it to avoid.

    **The bundle-aggregate exemption.** An entry whose scope contains the bundle
    ROOT is an aggregate, not a mistake, and never raises `resource-self`. This is
    `TYPES_WITHOUT_RESOURCE` generalized — from "types with nothing to point at" to
    "docs whose honest scope is bundle-wide" — so the front keeps ONE exemption
    mechanism rather than two. `glossary.md` really does govern the whole
    bundle, so `resource: /docs/**` is truthful and inventing a narrower scope to
    silence the check would be the fabrication. The discrimination is mechanical
    and needs no hardcoded path: a scope containing the bundle root contains every
    doc in it, while a narrower scope that still contains the doc
    (`/docs/standards/**` on a standards doc) stays a real finding.
    """
    fm = parse_frontmatter(text)
    if not _nonempty(fm, "resource"):
        return []          # absent, empty, or unparseable frontmatter — nothing to resolve
    project_root = _project_root(bundle_root)
    rel_doc = _rel_to_project(path, project_root)
    bundle_rel = _rel_to_project(bundle_root, project_root)
    out: list[tuple[str, str, str]] = []
    for entry, kind in parse_resource(fm["resource"]):
        if kind not in RESOLVABLE_KINDS:
            continue
        if not _resource_resolves(entry, kind, project_root):
            out.append(("WARN", "resource-unresolved",
                        f"`resource` entry `{entry}` matches nothing on disk"))
        elif _entry_contains(entry, kind, rel_doc) and \
                not _is_bundle_aggregate(entry, kind, bundle_rel):
            out.append(("WARN", "resource-self",
                        f"`resource` entry `{entry}` contains this doc — a doc that points at "
                        "itself governs nothing and is eternally fresh"))
    return out
