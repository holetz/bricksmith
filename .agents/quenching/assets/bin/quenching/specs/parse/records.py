"""The frontmatter records a spec may carry, and the native tag surface they project
onto — the `spec:` rendering, what the document itself declared, and the catalogue check.

Moved verbatim out of the pre-refactor specs script."""
from __future__ import annotations

from quenching.specs.schema import load_schema


def record_keys(schema: dict | None = None) -> list[str]:
    """The optional frontmatter records, in the schema's declared order.

    Read from the schema rather than listed here, so adding a record is a schema edit and
    never also a code edit — and so `status` cannot surface a different set than `validate`
    and the templates describe."""
    fmspec = (schema or load_schema()).get("frontmatter", {})
    return [k for k in fmspec.get("records", {}) if k != "note"] \
        or list(fmspec.get("optional", []))


def spec_records(fm: dict, schema: dict | None = None) -> dict:
    """Every declared record this spec actually carries, `None` where it does not.

    Reading the frontmatter top to bottom narrates the spec's history in order, so the
    order here is the schema's, not the file's."""
    return {k: (fm.get(k) or None) for k in record_keys(schema)}


def derive_labels(info: dict, schema: dict | None = None) -> list[str]:
    """The `spec:` labels this document projects onto its tracker issue / work item —
    read from the schema's `label:` map and nothing else.

    One label per present record (`record_keys(schema)` order, skipping any without a
    `label:`), plus `spec:built` when the already-derived `info["stage"]` matches the
    labelled stage rule. A unidirectional projection: recomputed here on every write, never
    read back — see /docs/standards/architecture/spec-backend.md §Granular reading is about
    context, not I/O for the sibling rule this one extends.

    MEASURED on 2026-08-05, against this repository's own `github` backend, per
    spec-backend.md's own rule that a backend is proved by the documents it will actually be
    given: all 96 real specs run through this calculation, each cross-checked — independently
    of this function, straight off its frontmatter and its own already-derived stage — against
    the records it actually carries. 0 disagreed."""
    s = schema or load_schema()
    fm = info.get("frontmatter", {})
    records = s.get("frontmatter", {}).get("records", {})
    labels = [records[k]["label"] for k in record_keys(s)
              if records[k].get("label") and fm.get(k)]
    for rule in s.get("stages", {}).get("derived", []):
        if rule.get("label") and info.get("stage") == rule["id"]:
            labels.append(rule["label"])
    # The presentation level is a second, orthogonal projection: a composite's `spec:feature`
    # or `spec:epic` is what a board filter reads, and it is derived from the document's wave
    # structure. A story writes nothing, so a leaf spec carries no extra label. Deferred
    # import: `epics` reads this module's siblings, and the card layer is the only caller that
    # needs the level.
    from quenching.specs.parse.epics import presentation_level
    level = presentation_level(info)
    if level != "story":
        labels.append(f"spec:{level}")
    return labels


# The prefix `derive_labels` renders under, RESERVED so that storage and rendering can share
# one native surface. `tags` is stored in the very field the `spec:` labels render onto —
# issue `labels`, `System.Tags` on a work item — so without a reserved half the two mechanisms
# would each read the other's writes as the spec's own content. See
# docs/standards/architecture/spec-backend.md §Stored is not projected, §The `spec:` prefix
# is reserved.
SPEC_LABEL_PREFIX = "spec:"


def declared_tags(names: list[str], discovery_tag: str | None = None) -> list[str]:
    """`names` minus everything the tool owns — the `spec:` rendering and, on `azure-boards`,
    the discovery tag — leaving only what the SPEC ITSELF declared.

    The one filter every reader of a native tag surface goes through, so `_native_fields`
    (reassembly), `tags_outside_catalog` (the catalogue check) and `doctor`'s own findings
    cannot drift into three different ideas of which names belong to the document. A name the
    tool renders is not a tag the spec claims, and reading one back would make the next write
    reaffirm it as declared content — the exact duplication a reserved prefix exists to
    prevent."""
    return [n for n in names
            if not n.startswith(SPEC_LABEL_PREFIX) and n != discovery_tag]


def reconcile_label_set(current: list[str], desired: list[str], prefix: str = "spec:") -> list[str]:
    """The label set a native tracker surface should carry next, given what it carries now.

    Every label OUTSIDE `prefix` in `current` survives untouched — a human's own label is
    never this tool's to remove. Every label UNDER `prefix` is replaced wholesale by
    `desired`: a stale one a stage regression left behind is dropped, a newly-earned one is
    added, in the same pass a PATCH can afford — see `derive_labels`."""
    foreign = [l for l in current if not l.startswith(prefix)]
    return foreign + list(desired)


def tags_outside_catalog(tags: list[str], catalog: dict) -> list[str]:
    """Which of `tags` are not a `tagCatalog` key — pure, so the write-time proposal
    (`/quenching-specs create`, §4.1) and `doctor`'s `sp-az-tag-uncatalogued` finding (§2.13) share
    ONE answer rather than two parsers that could disagree.

    No catalog declared flags nothing: `tagCatalog` is optional, and a repository that never
    declared one has not opted into this validation at all — the same `## Absence is the
    normal case` every other key here gets."""
    if not catalog:
        return []
    return [t for t in tags if t not in catalog]
