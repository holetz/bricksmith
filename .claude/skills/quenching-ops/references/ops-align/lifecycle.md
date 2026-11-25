# Ops front lifecycle

<!-- rules -->

This file is self-contained: an ops align reads it to classify an entry point as active or
archived and to decide whether archiving may move its path.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## The two lifecycle states

Every entry point declares exactly one state in the registry document, in a hand-written line
outside the generated zone. The generated zone is derived output and is never read back as a
declaration; editing one of its cells changes nothing, and the next `--write` replaces it:

| State | Location and reachability | Registry |
| --- | --- | --- |
| `active` | In the canonical operations tree and reachable from the router. | Listed as active with its normalized name. |
| `archived` | No longer offered by the active router; its implementation stays in place or moves under `_archive/` according to the inbound-import rule below. | Listed as archived with the reason and evidence. |

An entry point does not become archived merely because it is old, infrequently used, or absent
from a README. The align needs a lifecycle declaration or evidence that the entry point has ended;
it reports an undecidable case instead of inferring one.

## The inbound-import decision

The target evidence resolves the `_archive/` open decision in favour of preserving import paths when
an archived entry point still has inbound imports. On 2026-08-30, an AST scan of the target's 2,351
tracked Python files found 13 external importer files and 23 external import references to
`scripts.*` modules, spanning 32 distinct imported names. An archived module with an inbound import
therefore keeps its original path and receives the generated registry's `archived` marker; moving
it would break consumers that the surface cannot safely rewrite as part of a read-only alignment.

An archived entry point with no inbound imports may move under `_archive/`. The move must remove it
from the active router, preserve its registry record, and leave a searchable reason for the change.
An entry point with an unresolved import scan remains in place and is reported, never moved on a
guess.

| Evidence | Lifecycle action |
| --- | --- |
| Active router reachability and an active declaration | Keep in the canonical tree; list as `active`. |
| Ended lifecycle and inbound imports found | Keep the path; mark `archived` in entry-point metadata and remove active reachability. |
| Ended lifecycle and no inbound imports found | Move under `_archive/`; mark `archived` and remove active reachability. |
| Ended lifecycle but imports cannot be determined | Keep in place and report the missing evidence. |

## The transition record

Every transition to `archived` records the entry-point name, the evidence used, the date, and the
reason. The generated registry is the searchable record; it never presents an archived entry point
as an active operation. Re-activation reverses the transition only when the router and registry
again provide an explicit active declaration. The transition is made by editing the
hand-written declaration; the generated zone then follows on the next `--write`.

<!-- rationale -->

The target's 13 external importers and 23 references make a blanket move unsafe: an entry point can
be a dependency even when it is not a public command. Preserving the path for those modules keeps
the archive decision from becoming an accidental code migration. `_archive/` remains useful for
retired entries with no consumers, where moving the implementation makes the boundary visible
without breaking an import contract.
