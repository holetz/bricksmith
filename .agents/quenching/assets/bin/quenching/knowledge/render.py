"""One rendering of one finding list — the operator's report.

Moved verbatim out of the pre-refactor OKF validator script, with ONE adjustment: `VERSION` is
imported from `quenching.common.version` rather than declared here.

The agent's proposal rendering (`_render_proposal`, feeding a hook's `additionalContext`) was
retired along with the `hook` mode it served.

`_split` is the severity partition every caller branches on, and it is why this pillar
never grew a refusal step: findings are errors or warnings, and the exit code
`quenching.common.output` names is `OK` or `FINDINGS` and nothing else.
"""
from __future__ import annotations

from quenching.common.version import VERSION


def _split(findings):
    errors = [f for f in findings if f[0] == "ERROR"]
    warns = [f for f in findings if f[0] == "WARN"]
    return errors, warns


def _render_text(findings, bundle_root: str) -> str:
    errors, warns = _split(findings)
    # `cq knowledge`, not the pre-refactor validator's filename — see this pillar's `main`. This
    # header prints on EVERY text-mode validation, so it was the most-seen citation of a deleted
    # file anywhere in the product.
    lines = [f"OKF conformance — {bundle_root} (cq knowledge v{VERSION})",
             f"  {len(errors)} error(s), {len(warns)} warning(s)"]
    for sev, rel, code, msg in errors + warns:
        lines.append(f"  [{sev:<5}] {rel}: {msg}  ({code})")
    if not findings:
        lines.append("  OK — bundle conforms.")
    return "\n".join(lines)


def _render_activity(rows: list[tuple[str, dict]], bundle_root: str) -> str:
    """The resource-activity figure — one line per `resource:` entry, widest interval first.

    NO FINDING CODE APPEARS HERE, and that is the contract, not the formatting. `stale-doc` was
    retired because the comparison cannot support a verdict: a wide glob measures activity in its
    radius, not drift of what the doc describes. The same numbers are worth publishing — a reader
    asking *which docs sit next to the most movement* gets a ranking — as long as nothing on the
    line tells them a doc is wrong.

    A doc whose scope cannot be measured is PRINTED AS SUCH rather than dropped: every entry a
    `uri`, an `unknown`, or a bundle aggregate leaves nothing to ask git about, and a silent
    omission reads exactly like a doc that was measured and found quiet.
    """
    lines = [f"resource activity — {bundle_root} (cq knowledge v{VERSION})",
             "  a figure, not a verdict: a wide `resource:` measures activity in its radius,",
             "  never drift of what the doc describes"]
    measurable, unmeasured = [], []
    for rel, activity in rows:
        if not activity.get("scopeMeasured"):
            unmeasured.append(rel)
            continue
        for entry in activity["entries"]:
            measurable.append((entry.get("days"), rel, activity["timestamp"], entry))
    # None sorts last: an entry no commit ever touched has no interval to rank by.
    measurable.sort(key=lambda r: (r[0] is None, -(r[0] or 0)))
    if not measurable and not unmeasured:
        lines.append("  nothing to measure — no doc carries both a `generated.at` "
                     "(or legacy `timestamp`) and a `resource:`")
        return "\n".join(lines)
    for days, rel, stamped, entry in measurable:
        interval = f"{days:>5}d" if days is not None else "    —"
        lines.append(f"  {interval}  {rel}  {stamped} → {entry['lastCommit'] or 'never'}  "
                     f"[{entry['kind']}] {entry['entry']}")
    for rel in unmeasured:
        lines.append(f"      ?  {rel}: scope not measured — no resolvable `resource:` entry")
    return "\n".join(lines)
