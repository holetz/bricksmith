"""The hybrid serialisation — how a spec document is stored inside a remote issue tracker.

Moved verbatim out of the pre-refactor specs script, where these helpers were defined inside the `github` block
even though `azure-boards` consumes them at ten call sites. They belong to neither: they are
the shared format both external backends read and write, so they live here and both import
them, which is also what keeps `azure` from importing `github`."""
from __future__ import annotations

import re

from quenching.common.frontmatter import parse_frontmatter


# HYBRID SERIALISATION — the WHOLE canonical document is one issue body, and a document too
# large for one body spills into continuation comments on that same issue.
#
# IT USED TO PUT `## Tasks` IN SUB-ISSUES, one per task, and that mapping is retired. It was
# chosen because `## Tasks` is the one section with a native tracker counterpart carrying its
# own state and identity, which is true and turned out not to be the question. The question is
# whether anything READS the native state, and nothing ever did: `parse_tasks` takes a task's
# checked/blocked from the raw block on both ends, never from the sub-issue, so the issue's own
# open/closed was a projection written and never consulted — the status the issue TITLE also
# held for its whole life, but costing one to three API calls per task per write instead of
# nothing. The title was fixed rather than retired, by making a read consult it
# (`hybrid_title_split`); a sub-issue could not be, because nothing about it was ever free.
# Measured on this repository on 2026-08-03: 68 spec issues against 689 task
# sub-issues, a listing of 8 pages and 4.4 MB taking 8.4s that EVERY specs command pays, and a
# `write_spec` on a ten-task spec spending twelve round trips where one would do.
#
# What the collapse buys, beyond the calls: the round-trip obligation `spec-backend.md` puts on
# an external backend stops being a property to check and becomes the thing that is stored. No
# anchors, no positional keys, no two-part retirement of a removed task's marker — the three
# mechanisms that produced this backend's only two measured defects (every `### N.` group
# heading dropped; a checked task's sub-issue born open). `- [ ]` in an issue body is a native
# GitHub task list anyway: it renders as a checkbox with a progress count, and ticking it in the
# web UI edits the document, which closing a sub-issue never did.
#
# THE MARKER ON THE BODY CARRIES NO IDENTITY. It used to carry `YYYY-MM-DD-<slug>.md`, and that
# prefix was the ONLY reason this marker held a date: the files backend read a spec's capture
# date out of its basename, so a store with no filenames had to keep a synthetic one to stay
# equal to it. The date is a frontmatter field now, read by the one shared derivation in every
# backend, and the marker is back to doing its one job.
#
# That job is what makes a spec issue distinguishable from an ordinary one, and it is why the
# marker did not simply disappear with the date: a repo's issue tracker belongs to its humans,
# and a backend that treated every open issue as a spec would list the bug reports and then
# write over them. An HTML comment is the one place in a markdown body that survives a round
# trip through GitHub's issue editor while staying invisible to a human reading the issue, and
# the same reasoning applies one level down to a continuation comment's own marker, below.
HYBRID_MARKER_RE = re.compile(
    r"\A<!--\s*quenching-spec(?:\s*:\s*\S+)?(?:\s+parts=(\d+))?"
    r"\s*-->[ \t]*\r?\n")

# `azure-boards`'s own marker shape — measured (task 6.1) that `System.Description` strips any
# HTML comment on write, in every position tried, which the comment form above cannot survive.
# A `display:none` div is the nearest equivalent that does: invisible once the field renders,
# present in the raw value every read gets. `hybrid_wrap`'s `fmt='div'` writes it; this is what
# reads it back.
#
# THAT INVISIBILITY HOLDS UNDER `Markdown` TOO, and it was not obvious that it would: the
# original measurement was taken while `System.Description` was `html`, where a div is
# unremarkable. Once the field carries `multilineFieldsFormat: Markdown` the renderer could
# just as well have escaped the tag and printed the marker as the first visible line of every
# card. Verified by inspection of WI 961489 on 2026-08-06, ten sections in and already
# converted: the div stays invisible and the frontmatter's `---` renders as a rule nobody
# minded. So the storage format does NOT change, and the `[//]: # (…)` comment form
# `## Alternatives Considered` holds in reserve stays unbuilt.
#
# The `;?` below is not decoration: Azure normalises `display:none` to `display:none;`, so a
# marker written by this tool and a marker read back from the API differ by one character.
HYBRID_DIV_MARKER_RE = re.compile(
    r'\A<div style="display:\s*none;?"\s*>\s*quenching-spec(?:\s*:\s*\S+)?'
    r'(?:\s+parts=(\d+))?'
    r"\s*</div>[ \t]*\r?\n")


def hybrid_wrap(text: str, parts: int = 1, fmt: str = "comment") -> str:
    """The payload-free marker line a spec issue is recognised by, and the document under it.

    `parts=` is written ONLY when there is more than one. The single-part form — every document
    but the two largest this repository holds — is therefore byte-identical to the marker as it
    was before continuations existed, and one regex reads both. The marker carries NO payload:
    it says "this issue is a spec" and nothing else, because identity is the provider-native
    ID and a marker that also named the spec was a second, competing answer to the same
    question.

    `fmt='div'` is `azure-boards`'s own variant — measured (task 6.1) that `System.Description`
    STRIPS any HTML comment on write, in any position, which makes the default form invisible
    the instant it round-trips. A `display:none` div survives the same round trip intact and
    reads the same way `github`'s comment does: invisible once the field renders as rich text,
    present in the raw value every read gets. `github`/`files` keep the comment — it already
    round-trips there, in 69 real specs, and changing it would mean migrating every one."""
    count = f" parts={parts}" if parts > 1 else ""
    if fmt == "div":
        # WRITTEN IN THE FORM AZURE STORES, character for character: `display:none;` with
        # the semicolon and a space before `</div>`, both of which its HTML normaliser adds
        # on write. A marker written any other way comes back different from what went out,
        # and `System.Description` then differs on every diff — so the one field a write
        # always carries could never be elided. Measured live 2026-08-06; the round-trip case
        # in `hybrid_split_failures` has carried this exact shape since task 6.1 of the
        # previous plan. The reader stays tolerant (`;?`, `\s*`) for documents written before
        # this.
        return (f'<div style="display:none;">quenching-spec{count} </div>\n'
                f'{text}')
    return f"<!-- quenching-spec{count} -->\n{text}"


def hybrid_unwrap(body: str) -> tuple[str, int]:
    """`(chunk, parts)` for a spec issue, or `("", 0)` for anything else.

    Line endings are normalised on the way in. GitHub stores and returns issue bodies with
    CRLF, so a document written as LF comes back different from what was stored — every section
    parse, every diff and the round-trip equality would all read that as content having changed.

    `parts` is what tells a reader whether the chunk it just got IS the document or only its
    head, and it is read from the body already in hand. A one-part spec — the normal case —
    never pays a call to discover there is nothing more to fetch.

    BOTH marker shapes are tried, comment first — the reader does not know which backend wrote
    what it was handed, and never needs to: exactly one of the two ever matches a given body.
    A legacy `: <slug>.md` payload still MATCHES and is discarded unread — the two regexes
    keep it optional so the 154 specs captured before the native ID landed stay readable
    without being rewritten. Identity belongs to the provider-native ID, never to this
    marker, so there is no third element left to hand back."""
    body = (body or "").replace("\r\n", "\n")
    for pattern in (HYBRID_MARKER_RE, HYBRID_DIV_MARKER_RE):
        m = pattern.match(body)
        if m:
            return body[m.end():], int(m.group(1) or 1)
    return "", 0


HYBRID_PART_MARKER_RE = re.compile(r"\A<!--\s*quenching-spec-part:\s*(\d+)/(\d+)"
                                   r"(?:\s+eol=(\d))?\s*-->[ \t]*\r?\n")


def hybrid_wrap_part(index: int, total: int, chunk: str, eol: bool) -> str:
    """One continuation chunk, marked with its own position.

    `eol` describes THE CUT THAT PRECEDES THIS CHUNK, not the one after it — that is why the
    flag can live on the continuation comment and the body marker needs no second field. The
    rebuild reads it as "the part before me ended at a line boundary", which is what lets it
    restore a newline the store may have trimmed off the end of a body without ever inventing
    one at a cut that landed mid-line."""
    return f"<!-- quenching-spec-part: {index}/{total} eol={1 if eol else 0} -->\n{chunk}"


def hybrid_unwrap_part(body: str) -> tuple[int, str, bool]:
    """`(index, chunk, eol)` for a continuation comment, or `(0, "", False)` for a comment a
    human wrote. Ordinary discussion on a spec issue must stay possible."""
    body = (body or "").replace("\r\n", "\n")
    m = HYBRID_PART_MARKER_RE.match(body)
    if not m:
        return 0, "", False
    return int(m.group(1)), body[m.end():], m.group(3) == "1"


def hybrid_split(text: str, limit: int | None) -> list[tuple[str, bool]]:
    """The document as `(chunk, preceding-cut-was-at-a-line-boundary)` pairs, each within
    `limit`. The first pair's flag is always False: nothing precedes it.

    ONE PART IS THE NORMAL CASE and the only one most repositories will ever take: `limit` is
    None for a store with no published body ceiling, and a document under the ceiling comes back
    as a single pair whatever the store. Measured on this repository's 69 specs on 2026-08-03,
    two exceed GitHub's 65,536 — one of them an ACTIVE plan, not an archived one — so the
    alternative to spilling is refusing to store a spec somebody is building.

    Cuts are made at line boundaries, falling back to a mid-line cut only for the pathological
    single line longer than a whole body. That fallback exists so the loop cannot fail to make
    progress; nothing in this repository takes it."""
    if limit is None or len(text) <= limit:
        return [(text, False)]
    chunks: list[tuple[str, bool]] = []
    rest = text
    pending_eol = False
    while len(rest) > limit:
        # `limit` and not `limit + 1`: the slice below keeps the newline it cuts on, so a
        # boundary found AT `limit` would produce a chunk one character over the very ceiling
        # this loop exists to respect.
        cut = rest.rfind("\n", 0, limit)
        if cut <= 0:
            chunks.append((rest[:limit], pending_eol))
            rest, pending_eol = rest[limit:], False
            continue
        chunks.append((rest[:cut + 1], pending_eol))
        rest, pending_eol = rest[cut + 1:], True
    chunks.append((rest, pending_eol))
    return chunks


def hybrid_join(chunks: list[tuple[str, bool]]) -> str:
    """The document back from its parts.

    A chunk whose flag says the cut before it was a line boundary nudges its PREDECESSOR to end
    in a newline. `hybrid_split` slices verbatim and every such predecessor already ends that
    way; the nudge is there because an issue tracker is free to trim trailing whitespace off a
    body it stores, and a chunk that came back one newline short would weld itself to the line
    after it. A cut made MID-line is never nudged, because the newline would be this function's
    invention rather than the document's."""
    out: list[str] = []
    for chunk, eol in chunks:
        if out and eol and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(chunk)
    return "".join(out)


# The two ceilings a tracker imposes on the fields this serialisation writes. Named here,
# beside the helpers both external backends share, rather than inside `GitHubBackend`: the
# hybrid helpers stopped being GitHub's the moment `azure-boards` started using them.
#
# TITLE: 255, the SMALLER of GitHub's 256-character issue title and Azure Boards'
# 255-character `System.Title`. One number for one shared helper — the alternative is a cap
# per backend threaded through code that is deliberately backend-agnostic, to buy one
# character. Neither figure appears in the REST reference either vendor publishes; GitHub's
# is the widely documented UI limit and Azure's is from its field documentation, unproven
# here like the rest of that backend.
#
# BODY: GitHub's 65,536-character issue body. Azure Boards' `System.Description` has no
# comparable published limit, so this ceiling is enforced only where it is known to exist.
HYBRID_TITLE_MAX = 255
GH_BODY_MAX = 65_536

# What one CHUNK of a document may hold, as opposed to what GitHub will accept. The margin
# covers the marker line a chunk is wrapped in and leaves room for a longer one later; a
# chunk sized to the ceiling itself would put the wrapped body one marker over it, which is
# the off-by-a-header every "just use the limit" split makes once.
GH_PART_MAX = GH_BODY_MAX - 1_024


def hybrid_short_title(text: str) -> str:
    """A title that fits, cut on a word boundary and marked with an ellipsis.

    CUTTING LOSES NOTHING **ON THE PATH THAT STILL CUTS**, and that is what makes it the
    right answer there rather than a compromise. That path is `hybrid_title`: the document is
    stored whole, keeping its own `title:`, so the tracker's copy stays a PROJECTION and a cut
    one loses nothing the document does not still hold.

    IT IS ALSO THE REFUSAL TEST, and that is the other half. Where `hybrid_title_split`
    projects, the native title is STORAGE — read back on every read — so a cut title would be
    a renamed spec. `split` therefore refuses any title this function would touch, which is
    why the two live next to each other: one cuts where cutting is free, the other declines to
    project where it would not be.

    The same reasoning holds one level down: `parse_tasks` reads a task's BODY, never a title,
    so the block stays whole no matter what the title says.

    The alternative was to send it raw and let the tracker answer. Measured on this
    repository on 2026-08-02: 15 tasks across 12 specs carry a checkbox line longer than the
    cap, the longest at 946 characters — so "send it and see" is not a hypothetical branch,
    it is what a migration would have hit twelve times."""
    text = " ".join((text or "").split())
    if len(text) <= HYBRID_TITLE_MAX:
        return text
    cut = text[:HYBRID_TITLE_MAX - 1]
    space = cut.rfind(" ")
    if space > HYBRID_TITLE_MAX // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"


def hybrid_title(text: str) -> str:
    """What a human sees in the issue list, for a document stored WHOLE.

    This is the fallback half of `hybrid_project`, and the sentence that used to be written
    here — the title is a projection, rewritten on every write, so a web edit is undone by
    the next one — is now true only of this path. Where the projection applies, the native
    title is the STORAGE: editing it in the web UI renames the spec, exactly as ticking a
    `- [ ]` in the body edits the document. That is the deliberate consequence of making
    something read the mapping back, and it is why `hybrid_title_split` refuses a title the
    tracker would cut.

    THE FALLBACK IS THE DOCUMENT'S OWN `# ` HEADING, never a name derived from an identity.
    It used to be `titleize(slug)`, which only ever worked because the slug was a second
    place the title was kept; with the native ID as identity there is no such string, and
    inventing one from an issue number would put a label on the tracker that the document
    never said. A spec with neither `title:` nor a heading is malformed — `validate` says so
    — and this returns empty rather than covering for it."""
    fm_title = str(parse_frontmatter(text).get("title") or "").strip()
    if not fm_title:
        fm_title = next((ln[2:].strip() for ln in text.splitlines()
                         if ln.startswith("# ")), "")
    return hybrid_short_title(fm_title)


def hybrid_title_split(text: str) -> tuple[str, str] | None:
    """`(the document without its title, the title)` — or None when it must be stored whole.

    THE ONE NATIVE MAPPING THAT PAYS FOR ITSELF. `spec-backend.md` allows an external backend
    to use its host's constructs where a mapping exists, and holds it to one test: something
    must READ the native value back. The issue title failed that test for its whole life — it
    was rewritten from the frontmatter on every write and never once consulted — which made it
    a projection, and a projection is duplicated truth. Reading it back is what turns it into
    storage, and it costs nothing: the title was already being written on every write.

    Returning None is the safety, and it is checked rather than assumed. The document is only
    projected when it is in the shape `new` stamps — `title:` immediately after `slug:`, and
    the `# <TITLE>` heading alone between the frontmatter and the first section — because the
    reassembly has to put both back at an exact offset and the store keeps no note of where
    they were. A title the tracker would cut is also refused: cutting used to lose nothing
    precisely because nothing read it, and the moment something does, a cut title is a
    renamed spec."""
    title = str(parse_frontmatter(text).get("title", "")).strip()
    if not title or hybrid_short_title(title) != title:
        return None
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        return None
    # BOTH FRONTMATTER SHAPES, because the reader is tolerant and the writer must not
    # degrade what the reader accepted. A spec captured today opens on `title:`; the 154
    # captured before the native ID landed open on the legacy `slug:` and carry `title:`
    # second. Recognising only the new shape would silently stop projecting every legacy
    # spec the moment it was next written — the title would fall back into the body and the
    # native mapping would be lost on a spec nobody asked to migrate.
    keys = [ln.split(":", 1)[0].strip() for ln in lines[1:close]]
    at = 2 if keys[:2] == ["slug", "title"] else 1 if keys[:1] == ["title"] else None
    if at is None:
        return None
    # THE RAW LINE, not the parsed value. A quoted title — `title: "…"` — parses to the same
    # string with the quotes gone, so a rebuild from the value alone silently drops them:
    # measured on this repository, 4 of 73 specs quote their title and each came back two
    # characters short. The projection stores a VALUE and can only reproduce a line it would
    # have written itself, so anything else is stored whole.
    if lines[at] != f"title: {title}\n":
        return None
    if lines[close + 1:close + 4] != ["\n", f"# {title}\n", "\n"]:
        return None
    return "".join(lines[:at] + lines[at + 1:close + 2] + lines[close + 4:]), title


def hybrid_project(text: str) -> tuple[str, str]:
    """`(what goes in the body, what goes in the title)`, for every external backend.

    ONE place, for two reasons. Within a backend, a create that projected and an update that
    did not would leave the title reading as the spec's while the body carried a second,
    competing one. Across backends, `github` and `azure-boards` storing the title differently
    is exactly the drift `spec-backend.md` forbids — the canonical document is the contract,
    and two external stores disagreeing about what it holds is that contract broken twice."""
    proj = hybrid_title_split(text)
    return proj if proj else (text, hybrid_title(text))


def hybrid_title_join(stored: str, title: str) -> str:
    """Put `title:` and the `# <TITLE>` heading back, at the offsets `hybrid_title_split` cut
    them from. The inverse, and asserted as one by the round-trip case.

    A stored document that still HAS a `title:` was never projected — an older issue, or one
    whose shape `split` refused — and comes back untouched. That is the whole signal: `title`
    is a required frontmatter key, so its absence can only mean the store is holding it."""
    if str(parse_frontmatter(stored).get("title", "")).strip():
        return stored
    lines = stored.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return stored
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        return stored
    # THE OFFSET `split` CUT FROM, re-derived the same way it was chosen: after `slug:`
    # for a legacy document that still leads on it, first otherwise. Inserting at a fixed
    # offset would reorder the frontmatter of every legacy spec on its next write, which is
    # the round trip this function exists to close.
    at = 2 if lines[1].split(":", 1)[0].strip() == "slug" else 1
    return "".join(lines[:at] + [f"title: {title}\n"] + lines[at:close + 2]
                   + [f"# {title}\n", "\n"] + lines[close + 2:])
