# 0001. One repository, four distributions, one namespace

- Status: accepted
- Date: 2026-10-11

## Context

The project bundles a core framework, a column registry, a model development flow and a
monitoring library. The model flow depends directly on the core, and the core API will churn
while it is being built. Separate repositories would turn every core change into a release plus
a bump in each dependent repository.

## Decision

One repository, a uv workspace with four distributions released as separate wheels:
`bricksmith-registry`, `bricksmith`, `bricksmith-mlops` and `bricksmith-monitor`. They share the
PEP 420 namespace `bricksmith`, so the registry reads as a module (`bricksmith.registry`) while
remaining its own distribution.

Dependencies point one way: `registry ← core ← mlops`. `monitor` depends on none of them;
`mlops` uses it through an optional extra.

## Consequences

- A cross-cutting change is one pull request.
- The boundaries are enforced by `tests/test_workspace_layout.py`, not by repository walls: no
  `bricksmith/__init__.py` in any distribution, and no import against the dependency direction.
- Splitting a distribution into its own repository later stays cheap, since its tree is already
  self-contained.
