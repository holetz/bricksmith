"""The one deterministic projection left: the glossary's abbreviation snippet.

The bundle-root glossary is the only editable vocabulary source. It used to be projected twice —
into a published `reference/glossary.md` route AND into the Python-Markdown abbreviation
definitions — because the glossary sat OUTSIDE `docs_dir` and could only reach the site as a copy.

The glossary is staged as `glossary.md` in the bounded Zensical source and the route machine is
gone. With it went the whole machine that existed to serve it: `HOMES`, the publication-map parser, and the
bundle-link -> published-route rewriting (`rewrite_link`, `_published_target`, `render_route` and
their helpers). Links inside the bundle now resolve natively, so there is nothing left to rewrite.
Removed rather than deprecated, per `standards/workflows/retiring-a-standard.md`.

The snippet survives because it is a genuine derivation, not a copy: `*[term]: definition` lines
the `abbr` extension consumes. It is `.txt` because every `.md` under `docs_dir` is a page, and a
term list is not one.

Still no Zensical dependency, so the projection can be checked before the toolchain is installed.
"""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from pathlib import Path


GLOSSARY_REL = "glossary.md"
# `assets/` is pruned from the OKF walk, and `.txt` keeps the generator from rendering the
# 14k-word term list as a page of its own.
DEFAULT_SNIPPET = "assets/glossary-abbreviations.txt"
BULLET_SPLIT = re.compile(r"^(?P<label>\[[^\]]*\]\([^)]*\)|\*\*.+?\*\*|.+?) — (?P<definition>.*)$")
LINK_LABEL = re.compile(r"^\[(?P<label>.*)\]\((?P<link>[^)]+)\)$")
# An abbr definition is plain text: a link inside one would be rendered as literal syntax
# in the tooltip, so the label survives and the target is dropped.
MARKDOWN_LINK = re.compile(r"\[([^]]+)\]\(([^)]+)\)")


@dataclass(frozen=True)
class GlossaryEntry:
    term: str
    definition: str
    link: str | None = None


def _plain_term(label: str) -> str:
    match = LINK_LABEL.match(label.strip())
    if match:
        label = match.group("label")
    elif label.strip().startswith("[") and label.strip().endswith("]"):
        # Some glossary entries use link-label styling without a destination.  Keep the
        # vocabulary term itself, not the Markdown delimiters (which break abbr syntax).
        label = label.strip()[1:-1]
    label = html.unescape(label)
    label = re.sub(r"(``?|\*\*?|__?|~~)", "", label)
    return re.sub(r"\s+", " ", label).strip()


def _flatten(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _entry_from(label: str, definition: str) -> GlossaryEntry | None:
    link = None
    match = LINK_LABEL.match(label.strip())
    if match:
        label, link = match.group("label"), match.group("link")
    term = _plain_term(label)
    if not term or not definition.strip():
        return None
    if "replace with this repo's first term" in term.lower() or "one-sentence meaning" in definition.lower():
        return None
    return GlossaryEntry(term, _flatten(definition), link)


def parse_glossary(markdown: str) -> list[GlossaryEntry]:
    """Read glossary bullets, including wrapped definition lines, in source order."""
    entries: list[GlossaryEntry] = []
    pending: tuple[str, list[str]] | None = None
    in_terms = False

    def flush() -> None:
        nonlocal pending
        if pending is not None:
            entry = _entry_from(pending[0], " ".join(pending[1]))
            if entry is not None:
                entries.append(entry)
        pending = None

    for line in markdown.splitlines():
        if re.match(r"^##\s+Terms\s*$", line, re.IGNORECASE):
            in_terms = True
            continue
        if in_terms and re.match(r"^##\s+", line):
            flush()
            in_terms = False
            continue
        if not in_terms:
            continue
        candidate = line.strip()
        split = BULLET_SPLIT.match(candidate[2:]) if candidate.startswith(("- ", "* ")) else None
        if split:
            flush()
            pending = (split.group("label"), [split.group("definition")])
            continue
        if pending is not None and line.strip() and not line.lstrip().startswith(("- ", "* ", "#")):
            pending[1].append(line.strip())
        elif pending is not None and not line.strip():
            # A blank line belongs to the preceding wrapped paragraph only when another
            # continuation follows.  Keeping it out makes the projection canonical.
            continue
    flush()
    return entries


def source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _abbr_definition(definition: str) -> str:
    definition = MARKDOWN_LINK.sub(r"\1", definition)
    definition = re.sub(r"<[^>]+>", "", definition)
    definition = re.sub(r"[`*_~]", "", definition)
    return html.unescape(_flatten(definition))


def _abbr_supported(term: str) -> bool:
    """Whether Python-Markdown's abbreviation syntax can represent this term."""
    return bool(term) and term[0].isalnum() and term[-1].isalnum() and "\\" not in term and "]" not in term


def _abbr_variants(term: str, taken: frozenset[str]) -> list[str]:
    """The exact term plus, for a Title-cased term, its running-prose lowercase form.

    Abbreviation matching is case-sensitive and glossary terms are Title-cased, so without
    the extra variant a term only ever renders as ``<abbr>`` where prose happens to start a
    sentence with it.  Acronyms (second character uppercase) and terms another glossary entry
    already claims in lowercase keep the exact form only.
    """
    variants = [term]
    if len(term) > 1 and term[0].isupper() and term[1].islower():
        lowered = term[0].lower() + term[1:]
        if lowered not in taken:
            variants.append(lowered)
    return variants


def render_abbreviations(source: Path, entries: list[GlossaryEntry]) -> str:
    """Render the complete abbreviation file, including its provenance header."""
    digest = source_sha256(source)
    lines = [
        "<!-- GENERATED FILE — do not edit; source: /docs/glossary.md -->",
        f"<!-- source_sha256: {digest} -->",
        "",
    ]
    taken = frozenset(entry.term for entry in entries)
    for entry in sorted(entries, key=lambda item: (item.term.casefold(), item.term)):
        if _abbr_supported(entry.term):
            for variant in _abbr_variants(entry.term, taken):
                lines.append(f"*[{variant}]: {_abbr_definition(entry.definition)}")
        else:
            lines.append(f"<!-- explicit glossary abbr fallback: {html.escape(entry.term)} -->")
    return "\n".join(lines) + "\n"


def projection_paths(bundle: Path, snippet_rel: str = DEFAULT_SNIPPET) -> tuple[Path, Path]:
    """`(canonical glossary, generated snippet)` — both inside the bundle, which is `docs_dir`."""
    return bundle / GLOSSARY_REL, bundle / snippet_rel


def projection_findings(bundle: Path,
                        snippet_rel: str = DEFAULT_SNIPPET) -> tuple[dict, list[dict]]:
    """Is the snippet the deterministic derivation of the glossary as it stands right now?

    An absent or empty canonical glossary is not a finding — a bundle may legitimately carry no
    vocabulary yet, and inventing one is not this module's business.
    """
    source, snippet = projection_paths(bundle, snippet_rel)
    payload: dict = {"source": str(source), "snippet": str(snippet), "findings": []}
    if not source.is_file() or not source.read_text(encoding="utf-8").strip():
        return payload, []
    entries = parse_glossary(source.read_text(encoding="utf-8"))
    expected = render_abbreviations(source, entries)
    findings: list[dict] = []
    if not snippet.is_file():
        findings.append({"severity": "error", "code": "glossary-snippet-missing",
                         "message": "generated abbreviation snippet is absent",
                         "path": str(snippet)})
    elif snippet.read_text(encoding="utf-8") != expected:
        findings.append({"severity": "error", "code": "glossary-snippet-stale",
                         "message": "abbreviation snippet differs from the canonical glossary",
                         "path": str(snippet)})
    payload.update({"source_sha256": source_sha256(source), "terms": len(entries)})
    return payload, findings


def write_projection(bundle: Path, snippet_rel: str = DEFAULT_SNIPPET) -> dict:
    """Materialize the snippet. Byte-identical on a second run, which is what makes the check
    above a comparison rather than a judgement."""
    source, snippet = projection_paths(bundle, snippet_rel)
    if not source.is_file() or not source.read_text(encoding="utf-8").strip():
        return {"source": str(source), "skipped": "canonical glossary is absent or empty"}
    entries = parse_glossary(source.read_text(encoding="utf-8"))
    snippet.parent.mkdir(parents=True, exist_ok=True)
    snippet.write_text(render_abbreviations(source, entries), encoding="utf-8")
    return {"source": str(source), "snippet": str(snippet),
            "source_sha256": source_sha256(source), "terms": len(entries)}
