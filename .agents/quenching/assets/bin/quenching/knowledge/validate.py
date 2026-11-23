"""The three entry points every caller validates through — one file, one text, one tree.

Moved verbatim out of the pre-refactor OKF validator script. This is the dispatch that decides
which conformance function a filename earns: exempt harness pointers, the reserved
`index.md`, the retired `log.md`, the migration `README.md`, and everything else as a
concept doc.
"""
from __future__ import annotations

import os
import pathlib

from quenching.knowledge.checks import (
    check_concept,
    check_glossary,
    check_index,
    check_legacy_doc_quadrant,
    check_legacy_documentation_home,
    check_legacy_glossary,
    check_legacy_home,
    check_legacy_root,
    check_okf_signature,
)
from quenching.knowledge.corpus import _build_corpus
from quenching.knowledge.resource import check_resource
from quenching.knowledge.schema import (
    CONCEPT_DOC_CODES,
    EXEMPT,
    GLOSSARY_REL,
    LEGACY_ROOT_NAMES,
)
from quenching.knowledge.structure import validate_generated_listing, validate_structure


def validate_file(path: str, bundle_root: str) -> list[tuple[str, str, str, str]]:
    """Return findings for one file as (severity, rel, code, message). Reads the
    file itself — the single-file entry point (PostToolUse); whole-tree callers
    go through `_build_corpus` + `_validate_text` instead."""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [("ERROR", os.path.basename(path), "unreadable", f"cannot read file: {exc}")]
    return _validate_text(path, text, bundle_root)


def _validate_text(path: str, text: str, bundle_root: str) -> list[tuple[str, str, str, str]]:
    """Findings for one file whose text is already in hand (no disk read)."""
    rel = os.path.relpath(path, bundle_root).replace(os.sep, "/")
    base = os.path.basename(path)
    if base in EXEMPT:
        raw = []
    elif base == "index.md":
        is_root = os.path.dirname(os.path.abspath(path)) == os.path.abspath(bundle_root)
        raw = check_index(text, is_root)
    elif base == "log.md":
        raw = []  # retired: reserved, recognized, never judged — see THE CONFORMANCE CORE
    elif base == "README.md":
        # OKF-strict uses `index.md` as the reserved listing; a README in the bundle
        # is a migration nudge, not a hard failure (OKF does not reserve README).
        raw = [("WARN", "readme-not-index",
                "OKF-strict uses `index.md` as the reserved listing — convert this README.md to index.md")]
    elif base.endswith(".md"):
        raw = check_concept(text) + check_resource(text, path, bundle_root)
        if rel == GLOSSARY_REL:
            raw += check_glossary(text)
        # `stale-doc` was emitted here until 2026-08-27. RETIRED, NOT DELETED — the same shape
        # `log.md` above carries: the code stays named and explained, and nothing emits it.
        # It compared a doc's `generated.at` (then `timestamp`) against the last commit touching
        # its `resource:`, which
        # measures ACTIVITY IN THE RADIUS OF THE GLOB and not drift of what the doc describes —
        # so a doc correct about a stable rule was reported as possibly wrong whenever anything
        # near it moved. Measured on this repo the day it was retired: 50 of 95 docs. The
        # measurement survives as a figure with no code beside it (`--activity`,
        # `quenching.knowledge.stale.resource_activity`); the verdict does not.
    else:
        raw = []
    return [(sev, rel, code, msg) for (sev, code, msg) in raw]


def _frontmatter_block(text: str) -> str:
    """The raw YAML block between the opening and closing `---`, or `""`. Deliberately not a
    parse: the signature question is only whether the key is declared, and a bundle whose root
    listing has broken YAML must not read as a non-bundle."""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:] if end == -1 else text[3:end]


def validate_tree(bundle_root: str, deadline: float | None = None,
                   ignore_globs: tuple[str, ...] = ()):
    """Validate the whole bundle from ONE read pass. Returns the findings list,
    or **None** when `deadline` (a `time.monotonic()` instant) expired mid-walk —
    hook mode aborts silently; CLI mode passes no deadline."""
    findings: list[tuple[str, str, str, str]] = []
    root = pathlib.Path(bundle_root)
    if not root.is_dir():
        parent = os.path.dirname(bundle_root) or "."
        legacy = check_legacy_root([
            os.path.join(parent, name) for name in LEGACY_ROOT_NAMES
            if os.path.isdir(os.path.join(parent, name))
        ])
        return legacy or [("ERROR", str(bundle_root), "no-bundle", "bundle root is not a directory")]
    corpus = _build_corpus(bundle_root, deadline, ignore_globs)
    if corpus is None:
        return None
    for path in sorted(corpus):
        text = corpus[path]
        if text is None:
            findings.append(("ERROR", os.path.basename(path), "unreadable", "cannot read file"))
        else:
            findings.extend(_validate_text(path, text, bundle_root))
    # bundle-level SHOULDs
    if not (root / "index.md").exists():
        findings.append(("WARN", "index.md", "bundle-no-index", "bundle root has no `index.md`"))
    # Does this directory claim to be a bundle at all? Structural and legacy checks below still
    # run — they are what `/quenching-knowledge align` reads to migrate a target — but the
    # per-file concept-doc verdicts are withheld, because they judge files against a contract
    # this tree never adopted.
    # `_build_corpus` keys by ABSOLUTE path; joining onto the caller's (possibly relative)
    # bundle_root would miss and read every bundle as unsigned.
    root_index = corpus.get(os.path.abspath(os.path.join(bundle_root, "index.md")))
    signature = check_okf_signature(
        (root / "index.md").exists(),
        bool(root_index) and "okf_version" in _frontmatter_block(root_index),
        os.path.basename(os.path.normpath(bundle_root)) or bundle_root,
    )
    if signature:
        findings = [f for f in findings if f[2] not in CONCEPT_DOC_CODES]
        findings.extend(signature)
    # pre-rename layout debt — each site independent and idempotent (## Design §A migração
    # detecta por estrutura, sítio a sítio — nunca por versão)
    root_entries = {p.name for p in root.iterdir() if p.is_dir()}
    findings.extend(check_legacy_home(root_entries))
    doc_dir = root / "documentation"
    quadrant_entries = {p.name for p in doc_dir.iterdir() if p.is_dir()} if doc_dir.is_dir() else set()
    findings.extend(check_legacy_documentation_home(root_entries, quadrant_entries))
    if quadrant_entries:
        findings.extend(check_legacy_doc_quadrant(quadrant_entries))
    glossary_rels = sorted({
        os.path.relpath(p, bundle_root).replace(os.sep, "/")
        for p in corpus if os.path.basename(p) == "glossary.md"
    })
    generated_glossaries = {
        rel for path, rel in ((p, os.path.relpath(p, bundle_root).replace(os.sep, "/"))
                              for p in corpus if os.path.basename(p) == "glossary.md")
        if corpus[path] and "\ngenerated: true\n" in corpus[path]
        and "\nsource: /docs/glossary.md\n" in corpus[path]
    }
    findings.extend(check_legacy_glossary(glossary_rels, GLOSSARY_REL, generated_glossaries))
    # whole-tree structural integrity (missing/broken/orphaned listings)
    findings.extend(validate_structure(bundle_root, corpus))
    # the one listing derived from disk, checked against it
    findings.extend(validate_generated_listing(bundle_root, corpus))
    return findings
