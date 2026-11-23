"""The conformance core — one function per file kind, returning (severity, code, msg).

Moved verbatim out of the pre-refactor OKF validator script. The ONE mechanical change is the
frontmatter call shape: `parse_frontmatter` now returns the bare dict, and the two bits
these checks also need — does the file open a `---` fence, does that fence close — come
from the `frontmatter_block` sidecar beside it.

PARSE HONESTY (per-doc; WARN — this checker naming its own misread)
- **`okf-frontmatter-unparsed`** the frontmatter held something the parser could not
  represent faithfully: a stripped trailing comment, an unterminated quote, an indented
  continuation read as empty, or a duplicate top-level key that silently last-wins. It
  reports a suspicion it cannot resolve rather than letting the consequence surface as a
  content finding (`missing-type`, a missing recommended field). The YAML subset, the
  comment rule and the canonical case list are `/docs/standards/code/frontmatter-parser.md`.

  The set it reports SHRANK when the three parsers collapsed into
  `quenching.common.frontmatter`: that parser reads inline lists, block lists, block
  records, flow maps and block scalars, none of which this checker's own copy could read,
  so the `indented-continuation` it used to raise on each of them is gone. Nothing became
  invisible — it stopped denouncing forms it can now read.
"""
from __future__ import annotations

import re

from quenching.common.frontmatter import frontmatter_anomalies, frontmatter_block, parse_frontmatter
from quenching.knowledge.schema import (
    ACTOR_RE,
    ISO_DATETIME,
    LEGACY_HOMES,
    LEGACY_QUADRANTS,
    LIFECYCLE_STATUSES,
    OKF_VERSION,
    READABLE_OKF_VERSIONS,
    RECOMMENDED,
    SOURCE_MAX_WORDS,
    TYPES_WITHOUT_RESOURCE,
    WRAPPER_QUADRANTS,
    _nonempty,
    recency,
)


_YAML_INDICATOR_STARTS = ("`", "@", "%", "*", "&", "!")
_YAML_PLAIN_OPENERS = ('"', "'", "[", "{", "|", ">", "-", "#")


def _strict_yaml_findings(text: str) -> list[tuple[str, str, str]]:
    """Top-level plain scalars a strict YAML loader rejects, which this tolerant parser reads.

    Stdlib-only heuristic: a plain scalar containing `: ` (or ending in `:`), or opening with a
    reserved indicator, is not valid YAML — zensical's strict loader fails the whole build on it."""
    out: list[tuple[str, str, str]] = []
    block = text.split("\n---", 1)[0].splitlines()[1:]
    for line in block:
        if not line or line[0] in " \t#" or ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        if not val or val[0] in _YAML_PLAIN_OPENERS:
            continue
        if val.startswith(_YAML_INDICATOR_STARTS) or ": " in val or val.endswith(":"):
            out.append(("ERROR", "frontmatter-not-yaml",
                        f"`{key.strip()}` is an unquoted value strict YAML rejects (`: ` inside it, or it "
                        "opens with a reserved character) — wrap the value in double quotes"))
    return out


def _frontmatter_lines(text: str) -> list[str] | None:
    """The raw lines inside the leading `---` fences, or None when there is no closed block."""
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    if lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i]
    return None


def _scalar(value: str) -> str:
    """A frontmatter scalar stripped of surrounding whitespace and one matching quote pair."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _split_flow(inner: str) -> list[str]:
    """Split the inside of a YAML flow collection on its top-level commas only.

    A comma nested in `{…}` or `[…]`, or inside a quoted scalar, belongs to its item.
    """
    parts: list[str] = []
    depth = 0
    quote = ""
    start = 0
    for i, ch in enumerate(inner):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(inner[start:i])
            start = i + 1
    parts.append(inner[start:])
    return parts


def _flow_map(value: str) -> dict | None:
    """A `{ k: v, ... }` flow map as a dict, or None when the text is not one."""
    value = value.strip()
    if not (value.startswith("{") and value.endswith("}")):
        return None
    out: dict = {}
    for part in _split_flow(value[1:-1]):
        if ":" in part:
            key, _, val = part.partition(":")
            out[_scalar(key)] = _scalar(val)
    return out


def _read_mapping_list(text: str, key: str) -> list[dict] | None:
    """Read a top-level `key:` whose value is a sequence of mappings.

    The shared frontmatter parser flattens a block sequence of mappings — `sources:` and
    `verified:` below their first pair — because it reads the first level only. The v0.2
    families need the pairs, so this reads that one block from the raw text instead of
    widening a parser every pillar shares. Returns None when the text is not that shape;
    callers then fall back to whatever the shared parser produced and warn only if that is
    not a usable list either.
    """
    body = _frontmatter_lines(text)
    if body is None:
        return None
    start = None
    for i, raw in enumerate(body):
        if raw[:1] in (" ", "\t") or ":" not in raw:
            continue
        name, _, value = raw.partition(":")
        if name.strip() == key:
            start = i
            inline = value.strip()
            break
    if start is None:
        return None
    if inline:
        if not (inline.startswith("[") and inline.endswith("]")):
            return None
        items: list[dict] = []
        for part in _split_flow(inline[1:-1]):
            if not part.strip():
                continue
            mapped = _flow_map(part)
            if mapped is None:
                return None
            items.append(mapped)
        return items or None

    items = []
    current: dict | None = None
    dash_indent = None
    j = start + 1
    while j < len(body):
        raw = body[j]
        if not raw.strip() or raw.lstrip().startswith("#"):
            j += 1
            continue
        if raw[:1] not in (" ", "\t"):
            break
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if stripped.startswith("- "):
            if current is not None:
                items.append(current)
            content = stripped[2:].strip()
            mapped = _flow_map(content)
            if mapped is not None:
                current = mapped
            elif ":" in content:
                name, _, value = content.partition(":")
                current = {_scalar(name): _scalar(value)}
            else:
                return None
            dash_indent = indent
        elif current is not None and ":" in stripped:
            if dash_indent is not None and indent <= dash_indent:
                break
            name, _, value = stripped.partition(":")
            current[_scalar(name)] = _scalar(value)
        else:
            break
        j += 1
    if current is not None:
        items.append(current)
    return items or None


def _mapping_entries(text: str, key: str, fm: dict) -> list[dict] | None:
    """The entries of a mapping sequence, from the raw block first and the shared parse second."""
    raw = _read_mapping_list(text, key)
    if raw is not None:
        return raw
    value = fm.get(key)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        mapped = [_flow_map(v) if isinstance(v, str) else v for v in value]
        if all(isinstance(v, dict) for v in mapped):
            return mapped  # type: ignore[return-value]
    return None


def _check_generated(fm: dict, out: list[tuple[str, str, str]]) -> None:
    """OKF v0.2 `generated` — how the current content was produced (`by`, `at`)."""
    generated = fm.get("generated")
    if generated is None:
        return
    if isinstance(generated, str):
        # `generated: true` is this plugin's own projection marker, not the v0.2 family;
        # the name collision is accepted so an already-generated file keeps validating.
        return
    if not isinstance(generated, dict):
        out.append(("WARN", "generated-malformed",
                    "`generated` must be a mapping `{ by, at }` (OKF v0.2 §5.2)"))
        return
    by = str(generated.get("by", "")).strip()
    if not by:
        out.append(("WARN", "generated-malformed",
                    "`generated` has no `by` — OKF v0.2 requires an actor (§5.2, §7)"))
    elif not ACTOR_RE.match(by):
        out.append(("WARN", "generated-by-invalid",
                    f"`generated.by` `{by}` is not an actor — use `<producer>/<version>`, "
                    "`human:<id>` or `process:<id>` (OKF v0.2 §7)"))
    at = str(generated.get("at", "")).strip()
    if at and not ISO_DATETIME.match(at):
        out.append(("WARN", "generated-at-invalid",
                    f"`generated.at` `{at}` is not an ISO 8601 datetime with an explicit UTC "
                    "offset (OKF v0.2 §5)"))


def _check_verified(text: str, fm: dict, out: list[tuple[str, str, str]]) -> None:
    """OKF v0.2 `verified` — one mapping or a list of `{ by, at }` verification events."""
    verified = fm.get("verified")
    if verified is None:
        return
    entries = _mapping_entries(text, "verified", fm)
    if not entries:
        out.append(("WARN", "verified-malformed",
                    "`verified` must be one `{ by, at }` mapping or a list of them "
                    "(OKF v0.2 §5.2)"))
        return
    for entry in entries:
        by = str(entry.get("by", "")).strip()
        at = str(entry.get("at", "")).strip()
        if not by:
            out.append(("WARN", "verified-malformed",
                        "a `verified` entry has no `by` (OKF v0.2 §5.2)"))
        elif not ACTOR_RE.match(by):
            out.append(("WARN", "verified-by-invalid",
                        f"`verified.by` `{by}` is not an actor (OKF v0.2 §7)"))
        if not at:
            out.append(("WARN", "verified-malformed",
                        "a `verified` entry has no `at` (OKF v0.2 §5.2)"))
        elif not ISO_DATETIME.match(at):
            out.append(("WARN", "verified-at-invalid",
                        f"`verified.at` `{at}` is not an ISO 8601 datetime with an explicit "
                        "UTC offset (OKF v0.2 §5)"))


def _check_lifecycle(fm: dict, out: list[tuple[str, str, str]]) -> None:
    """OKF v0.2 `status` and `stale_after` (absent `status` means stable)."""
    status = fm.get("status")
    if status is not None:
        value = str(status).strip()
        if value not in LIFECYCLE_STATUSES:
            out.append(("WARN", "status-invalid",
                        f"`status` `{value}` is not one of {', '.join(LIFECYCLE_STATUSES)} "
                        "(OKF v0.2 §5.4)"))
    stale = fm.get("stale_after")
    if stale is not None:
        value = str(stale).strip()
        if not ISO_DATETIME.match(value):
            out.append(("WARN", "stale-after-invalid",
                        f"`stale_after` `{value}` is not an ISO 8601 datetime with an explicit "
                        "UTC offset (OKF v0.2 §5.5)"))


def _check_sources(text: str, fm: dict, out: list[tuple[str, str, str]]) -> None:
    """OKF v0.2 `sources` — each entry names the material it derives from."""
    sources = fm.get("sources")
    if sources is not None:
        entries = _mapping_entries(text, "sources", fm)
        if not entries:
            out.append(("WARN", "sources-malformed",
                        "`sources` must be a list of `{ resource, ... }` mappings (OKF v0.2 §5.1)"))
        else:
            seen: set[str] = set()
            for entry in entries:
                resource = str(entry.get("resource", "")).strip()
                if not resource:
                    out.append(("WARN", "source-missing-resource",
                                "a `sources` entry has no `resource` — OKF v0.2 requires one per entry"))
                source_id = str(entry.get("id", "")).strip()
                if source_id:
                    if source_id in seen:
                        out.append(("WARN", "source-id-duplicate",
                                    f"`sources` id `{source_id}` is repeated; a footnote cannot tell "
                                    "the entries apart (OKF v0.2 §5.1)"))
                    seen.add(source_id)
                last_modified = str(entry.get("last_modified", "")).strip()
                if last_modified and not ISO_DATETIME.match(last_modified):
                    out.append(("WARN", "source-last-modified-invalid",
                                f"`sources[].last_modified` `{last_modified}` is not an ISO 8601 datetime "
                                "with an explicit UTC offset (OKF v0.2 §5.1)"))
    usage = fm.get("usage_window")
    if usage is not None:
        if (not isinstance(usage, dict)
                or not str(usage.get("from", "")).strip()
                or not str(usage.get("to", "")).strip()
                or not ISO_DATETIME.match(str(usage.get("from", "")).strip())
                or not ISO_DATETIME.match(str(usage.get("to", "")).strip())):
            out.append(("WARN", "usage-window-invalid",
                        "`usage_window` must be a `{ from, to }` pair of ISO 8601 datetimes "
                        "(OKF v0.2 §5.1)"))


def _check_attested_computation(fm: dict, out: list[tuple[str, str, str]]) -> None:
    """OKF v0.2 `Attested Computation` — `runtime` is required for the type (§10.2)."""
    if str(fm.get("type", "")).strip() != "Attested Computation":
        return
    if not _nonempty(fm, "runtime"):
        out.append(("WARN", "attested-computation-no-runtime",
                    "`type: Attested Computation` requires a non-empty `runtime` "
                    "(OKF v0.2 §10.2)"))
    for key in ("executor", "attester"):
        value = fm.get(key)
        if value is None:
            continue
        if not isinstance(value, dict):
            out.append(("WARN", "attested-computation-malformed",
                        f"`{key}` must be a mapping with a `resource` (OKF v0.2 §10.2)"))
        elif not str(value.get("resource", "")).strip():
            out.append(("WARN", "attested-computation-malformed",
                        f"`{key}.resource` is empty — it names the code or instructions "
                        "the consumer runs (OKF v0.2 §10.2)"))


def _check_v02(text: str, fm: dict, out: list[tuple[str, str, str]]) -> None:
    """The optional OKF v0.2 families. Absence is never a finding; a present-but-malformed
    value is (WARN — they narrow the spec's SHOULDs, they are not conformance errors)."""
    _check_generated(fm, out)
    _check_verified(text, fm, out)
    _check_lifecycle(fm, out)
    _check_sources(text, fm, out)
    _check_attested_computation(fm, out)


def check_concept(text: str) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    fm = parse_frontmatter(text)
    has_block, well_formed = frontmatter_block(text)
    if not has_block:
        out.append(("ERROR", "no-frontmatter",
                    "concept doc has no YAML frontmatter (needs a `---` block with a non-empty `type`)"))
        return out
    if not well_formed:
        out.append(("ERROR", "broken-frontmatter",
                    "frontmatter opens with `---` but never closes"))
        return out
    # BEFORE the content checks, so a misread is never presented as a content gap: a
    # value this parser could not represent used to surface as `missing-type` or a
    # missing recommended field, naming the absence rather than the misread.
    out.extend(_strict_yaml_findings(text))
    for a in frontmatter_anomalies(text):
        out.append(("WARN", "okf-frontmatter-unparsed", f"`{a['key']}`: {a['detail']}"))
    if not _nonempty(fm, "type"):
        out.append(("ERROR", "missing-type",
                    "frontmatter has no non-empty `type` (OKF requires it on every concept doc)"))
    recommended = RECOMMENDED
    if str(fm.get("type", "")).strip() in TYPES_WITHOUT_RESOURCE:
        recommended = tuple(k for k in RECOMMENDED if k != "resource")
    for key in recommended:
        if key == "generated":
            # v0.2 supersedes `timestamp` with `generated.at`, but a v0.2 consumer MAY fall
            # back to the legacy key. Accept it silently: rewriting 200 docs on an upgrade
            # would invent an actor for every one of them.
            if recency(fm) is not None:
                continue
            out.append(("WARN", "missing-generated",
                        "recommended recency field `generated` is absent (OKF v0.2; a legacy "
                        "`timestamp` is accepted)"))
            continue
        if not _nonempty(fm, key):
            out.append(("WARN", f"missing-{key}",
                        f"recommended field `{key}` is absent (OKF recommends it)"))
    source = str(fm.get("source", "")).strip()
    if len(source.split()) > SOURCE_MAX_WORDS:
        out.append(("WARN", "source-too-long",
                    f"`source:` is {len(source.split())} words (max {SOURCE_MAX_WORDS}) — keep spec ids "
                    "and move the lineage into a `decisions/` entry"))
    _check_v02(text, fm, out)
    return out


# Entries open with `- ` or `* ` (the published skeleton writes `* `); the term is bold,
# bold inside a link, or a plain link (`* [Term](path.md) —`).
GLOSSARY_ENTRY_RE = re.compile(r"^[-*] (?:\[?\*\*(.+?)\*\*|\[([^\]]+)\]\()")
GLOSSARY_MAX_WORDS = 70


def _glossary_key(term: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", term.casefold())


def check_glossary(text: str) -> list[tuple[str, str, str]]:
    """Glossary form: no duplicate term, entries in alphabetical order, one short entry per term.
    Reads only the `## Terms` section (the core-vocabulary block above it repeats terms by design)."""
    out: list[tuple[str, str, str]] = []
    start = text.find("\n## Terms")
    if start == -1:
        return out
    end = text.find("\n## ", start + 4)
    body = text[start:end if end != -1 else len(text)]
    entries = re.split(r"\n(?=[-*] )", body)[1:]
    seen: set[str] = set()
    prev = ""
    for entry in entries:
        m = GLOSSARY_ENTRY_RE.match(entry)
        if not m:
            continue
        term = m.group(1) or m.group(2)
        key = _glossary_key(term)
        if key in seen:
            out.append(("WARN", "glossary-duplicate", f"term `{term}` is defined more than once"))
        seen.add(key)
        if prev and key < prev:
            out.append(("WARN", "glossary-order", f"`{term}` is out of alphabetical order"))
        prev = key
        if len(entry.split()) > GLOSSARY_MAX_WORDS:
            out.append(("WARN", "glossary-entry-long",
                        f"`{term}` is {len(entry.split())} words (max {GLOSSARY_MAX_WORDS}) — one sentence plus a link"))
    return out


def check_index(text: str, is_root: bool) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    fm = parse_frontmatter(text)
    has_block, well_formed = frontmatter_block(text)
    if has_block and not well_formed:
        out.append(("ERROR", "broken-frontmatter", "frontmatter opens with `---` but never closes"))
        return out
    if _nonempty(fm, "type"):
        out.append(("ERROR", "index-has-type",
                    "`index.md` is a reserved listing and must not carry a concept `type`"))
    if is_root:
        if not has_block:
            out.append(("WARN", "root-no-okf-version",
                        f"root `index.md` should declare `okf_version: \"{OKF_VERSION}\"`"))
        else:
            ver = str(fm.get("okf_version", "")).strip()
            if not ver:
                out.append(("WARN", "root-no-okf-version",
                            "root `index.md` frontmatter should declare `okf_version`"))
            elif ver not in READABLE_OKF_VERSIONS:
                out.append(("WARN", "root-okf-version-mismatch",
                            f"root `index.md` declares okf_version `{ver}` (this plugin writes "
                            f"`{OKF_VERSION}` and reads {', '.join(READABLE_OKF_VERSIONS)})"))
            extra = [k for k in fm if k != "okf_version"]
            if extra:
                out.append(("WARN", "root-extra-keys",
                            f"root `index.md` frontmatter should carry only `okf_version` (found: {', '.join(extra)})"))
    else:
        if has_block:
            out.append(("ERROR", "index-has-frontmatter",
                        "a non-root `index.md` must have no frontmatter (it is a listing, not a concept)"))
    return out


# --------------------------------------------------------------------------- #
# PRE-RENAME LAYOUT DEBT (whole-bundle; ERROR — decided in ## Open Decisions,
# task renomear-docs-para-knowledge 2.1: the PreToolUse hard block never reads
# these findings, so `error` cannot deny a write)
#
# Each function below checks exactly one site and is idempotent — a migrated
# site simply stops appearing in its input. Unlike `check_concept`/`check_index`,
# these return complete (severity, rel, code, msg) tuples instead of leaving
# `rel` to the caller: a single call can name more than one site (`okf-legacy-home`
# on both `knowledge/` and `reference/`) or a site the caller has no single path
# for (`okf-legacy-glossary` on however many nested `glossary.md` exist).
# --------------------------------------------------------------------------- #
def check_legacy_root(legacy_root_rels: list[str]) -> list[tuple[str, str, str, str]]:
    """`okf-legacy-root` — call only once the new root is already known absent (the
    `no-bundle` branch); fires once per pre-rename sibling present beside it. There is more
    than one: the root has been renamed twice, and a target may sit at either former name."""
    return [("ERROR", rel, "okf-legacy-root",
             f"`{rel}` is a pre-rename bundle root — migrate it to the OKF root (`docs/`)")
            for rel in legacy_root_rels]


def check_okf_signature(has_root_index: bool, has_okf_version: bool,
                        root_rel: str) -> list[tuple[str, str, str, str]]:
    """`not-an-okf-bundle` — the directory exists but never claims to be a bundle. The
    signature is the root `index.md` carrying `okf_version`; nothing else in the tree can
    stand in for it, because every other file is exactly what is in question.

    This check earns its place from the root's own name. While the root was dotted, no
    repository had one by accident and pointing the validator at a non-bundle was operator
    error. `docs/` is the commonest documentation folder in existence, so the same mistake is
    now the default case — and without this gate it answers with one ERROR per file."""
    if has_root_index and has_okf_version:
        return []
    missing = "has no `index.md`" if not has_root_index else "`index.md` declares no `okf_version`"
    return [("ERROR", root_rel, "not-an-okf-bundle",
             f"`{root_rel}/` {missing} — it is a directory, not an OKF bundle; "
             f"`/quenching-knowledge align` converts one, and per-file OKF findings are "
             f"withheld until it does")]


def check_legacy_home(root_entries: set[str]) -> list[tuple[str, str, str, str]]:
    """`okf-legacy-home` — a pre-rename home (`knowledge/`, `reference/`) sits at the
    bundle root instead of its OKF name (`concepts/`, `external/`)."""
    return [
        ("ERROR", f"{old}/", "okf-legacy-home", f"`{old}/` is the pre-rename home — rename it to `{new}/`")
        for old, new in LEGACY_HOMES.items() if old in root_entries
    ]


def check_legacy_documentation_home(root_entries: set[str],
                                    documentation_entries: set[str] | None = None,
                                    ) -> list[tuple[str, str, str, str]]:
    """`okf-legacy-documentation-home` — the retired `documentation/` wrapper still sits at the
    bundle root. Its Diátaxis quadrants became homes in their own right (`tutorials/`, `how-to/`,
    `project/`) once the bundle root itself became the site's source, and the explanation quadrant
    became the canonical `documentation/` home (2026-11-04).

    Because `documentation/` is a canonical home again, the wrapper is told apart **by
    structure**: it is the `documentation/` that still holds a quadrant subfolder
    (`WRAPPER_QUADRANTS`). A flat `documentation/` is the home and is silent.

    Independent and idempotent like its siblings: dissolving this one site clears it, whether or
    not any other has migrated."""
    if "documentation" not in root_entries:
        return []
    held = sorted(set(documentation_entries or ()) & set(WRAPPER_QUADRANTS))
    if not held:
        return []
    return [("ERROR", "documentation/", "okf-legacy-documentation-home",
             "`documentation/` still holds the retired wrapper's quadrants ("
             + ", ".join(f"`{name}/`" for name in held)
             + ") — move `tutorials/`, `how-to/` and `project/` to the bundle root and flatten "
               "`explanation/` into `documentation/` itself")]


def check_legacy_doc_quadrant(quadrant_entries: set[str]) -> list[tuple[str, str, str, str]]:
    """`okf-legacy-doc-quadrant` — a pre-rename Diátaxis quadrant under `documentation/`.

    Deliberately still aimed at `documentation/`, never at the bundle root: `LEGACY_QUADRANTS`
    names `concepts`, and `concepts/` is a canonical home at the root. Re-aiming this check upward
    would report the bundle's own `concepts/` as migration debt. It fires only under a surviving
    `documentation/` wrapper, which `okf-legacy-documentation-home` already reports."""
    return [
        ("ERROR", f"documentation/{old}/", "okf-legacy-doc-quadrant",
         f"`documentation/{old}/` is the pre-rename Diátaxis name — its pages belong in `{new}/` "
         f"at the bundle root")
        for old, new in LEGACY_QUADRANTS.items() if old in quadrant_entries
    ]


def check_legacy_glossary(glossary_rels: list[str], canonical: str,
                          generated: set[str] | None = None) -> list[tuple[str, str, str, str]]:
    """`okf-legacy-glossary` — a `glossary.md` sitting inside a home instead of at the
    bundle root, where the OKF contract pins it. A generated documentation projection is the
    one intentional nested copy: it is a published route, not a second canonical glossary."""
    generated = generated or set()
    return [
        ("ERROR", rel, "okf-legacy-glossary",
         f"`{rel}` — the glossary lives at the bundle root (`{canonical}`), not inside a home")
        for rel in glossary_rels if rel != canonical and rel not in generated
    ]
