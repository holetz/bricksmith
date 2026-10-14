"""Pure SCD2 invariant checker over plain rows; the SQL post-checks in ``plan`` enforce the same rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bricksmith.storage.spec import WriteSpec


def scd2_violations(rows: Sequence[Mapping[str, object]], spec: WriteSpec) -> list[str]:
    """Return stable findings; empty means one current row per key and well-formed, disjoint intervals.

    Intervals are half-open ``[valid_from, valid_to)``; the current row has no ``valid_to``.
    """
    by_key: dict[tuple[object, ...], list[Mapping[str, object]]] = {}
    for row in rows:
        by_key.setdefault(tuple(row[k] for k in spec.keys), []).append(row)
    findings: list[str] = []
    for key in sorted(by_key, key=repr):
        history = sorted(by_key[key], key=lambda r: r[spec.valid_from])  # type: ignore[arg-type, return-value]
        currents = [r for r in history if r[spec.current]]
        if len(currents) != 1:
            findings.append(f"{key!r}: {len(currents)} current rows")
        for row, nxt in zip(history, [*history[1:], None], strict=True):
            end = row[spec.valid_to]
            if row[spec.current] != (end is None):
                findings.append(f"{key!r}: current flag and valid_to disagree at {row[spec.valid_from]!r}")
            if end is not None and end <= row[spec.valid_from]:  # type: ignore[operator]
                findings.append(f"{key!r}: empty or inverted interval at {row[spec.valid_from]!r}")
            if nxt is not None and (end is None or end > nxt[spec.valid_from]):  # type: ignore[operator]
                findings.append(f"{key!r}: overlap after {row[spec.valid_from]!r}")
    return findings
