# Ops front target structure

<!-- rules -->

This file is self-contained: an ops align reads it and needs no other reference to decide the
target tree, the active entry points, or the canonical router.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## 1. One declared operations root

An adopting repository has one operations root, `scripts/` by default. A different root is valid
only when the repository declares it unambiguously in the configuration the verifier reads. The
align reports an absent or ambiguous root as a finding; it does not infer one from whichever folder
happens to contain executable files.

## 2. The canonical tree

The operations root converges to this shape:

```text
scripts/
├── bootstrap
├── setup
├── update
├── check
├── test
├── deploy
├── registry.md       # generated inventory of reachable entry points
├── <domain>/         # domain packages, never lifecycle folders
│   └── ...
└── _archive/         # entry points whose lifecycle has ended
    └── ...
```

The six normalized names are active entry points when the repository supports them: `bootstrap`,
`setup`, `update`, `check`, `test`, and `deploy`. The tree may contain additional domain entry
points, but each one must still be reachable from the router and named in `registry.md`.

`registry.md` is generated, not hand-maintained. It lists every reachable entry point and its
lifecycle. A stale or absent registry is drift, not a reason to preserve a hand-written list.

## 3. Domain placement

Files under the root are grouped by the domain they own, never by the lifecycle action that invokes
them. A package that serves deployment remains in its domain package when `deploy` calls it; adding
parallel `bootstrap/`, `setup/`, or `test/` copies of that package is non-convergent. The normalized
entry point is the outer interface, not a second home for domain logic.

## 4. Exactly one canonical router

The repository declares exactly one canonical router for the normalized interface. It is one of:

- a Python `[project.scripts]` console-script declaration;
- a `justfile`; or
- a `Taskfile.yml`.

The router maps normalized names to the domain operations that implement them. A convenience
wrapper may call the router, but it is not a second router and must not duplicate domain logic. The
align reports more than one declared router as a finding and does not choose between them.

## 5. Lifecycle placement

An entry point declares one lifecycle state: `active` or `archived`. Active entry points stay in the
canonical tree and are listed in `registry.md`. Archived entry points move under `_archive/` and
remain visible there; they are not reachable through the active router or presented as active in
the registry.

<!-- rationale -->

The root and router are declarations rather than guesses because the same executable file can be
an imported domain helper, a one-shot migration, or a public operation. A stable outer tree gives
the verifier a decidable surface and gives contributors names that do not depend on the target's
internal package layout.

The registry is generated so adding an entry point cannot silently create a second, stale inventory.
Keeping retired work under `_archive/` preserves evidence for an align and a reviewer while the
active interface remains small. Grouping by domain keeps lifecycle names at the boundary, where
they can stay normalized without relocating the logic that owns them.
