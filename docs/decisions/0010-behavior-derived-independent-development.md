# 0010. Independently implement behavior-derived requirements

- Status: accepted
- Date: 2026-10-13
- Amends: [0006](0006-english-everywhere.md), [0007](0007-registry-mechanism-presets-seed.md),
  [0008](0008-monitor-succeeds-psi-spark.md) and
  [0009](0009-one-library-shared-task-scopes.md)

## Context

A private reference project contains useful engineering behavior and documented failure cases.
bricksmith needs its own generic design and implementation. Reusing the reference's architecture,
classes, fixtures, naming vocabulary or domain conventions would carry unnecessary coupling and
private content into the new project. The requested development approach derives requirements
from observed behavior and starts implementation from an empty library.

## Decision

- Inspect the private source only to identify capabilities and behavioral contracts, in read-only
  mode. Do not run its code, tests, jobs, services or deployments for this review.
- Keep source paths, evidence locations and research notes in ignored local files. Public planning
  documents contain independently written, generic requirements and distinguish observed evidence,
  documented limitations and proposed additions.
- Implement from the generic contracts, public primary documentation and independently authored
  synthetic examples and tests. Do not copy or translate implementation bodies, inherit reference
  classes, reuse fixtures, transplant directories, import Git history or depend on private code.
- Define public APIs, task identities, configuration and project layout for bricksmith's needs.
  Internal modules and dedicated task scopes remain optional organizational choices under decision
  0009; no capability requires a specific catalog or schema.
- Ship a neutral, configurable naming policy and an empty project vocabulary. Prefixes, semantic
  categories, business conditions and naming facet lists from the private project are not the
  default preset. Mechanism, preset and project seed remain distinct under decision 0007.
- Implement monitoring inside the single library from independently defined statistical contracts.
  This replaces the earlier plan to import a predecessor repository with history. Any future
  third-party source adoption requires a separate explicit decision; it is not part of this epic.
- During implementation, use the public requirement documents as the handoff. If strict separation
  between observer and implementer is required, assign implementation to a fresh context with only
  those documents; this review itself does not establish that separation.
- Continue enforcing the private-content gate for all additions. Neither the source nor its
  organization-specific data enters example projects or release artifacts.

## Consequences

- The [capability review](../planning/capability-review.md) and
  [development epic](https://github.com/holetz/bricksmith/blob/quenching/specs/5.md) govern the next implementation work.
- Their generic requirements are the implementation handoff. The private source path and local
  evidence map are not requested inputs, and their absence does not block implementation. Close
  remaining API questions in child specifications using public contracts and independent tests.
- The previous monitoring history-import/archive steps and reference-derived naming preset are
  retired. Release of bricksmith does not depend on migrating or archiving another project.
- Behavioral similarity is evaluated through independently written acceptance tests. Public APIs
  and file layout need not match the reference, and no compatibility with its private artifacts
  is promised.
- Planning can preserve useful constraints such as idempotence, lineage, protected outputs and
  reproducibility while improving documented failure modes and avoiding source-specific policies.
