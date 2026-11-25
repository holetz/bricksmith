# Proof front target structure

<!-- rules -->

This file is self-contained: a proof align reads it to decide the target root, the required
bootstrap files and the recommended layer directories.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## 1. One declared proof root

An adopting repository has one proof root, `tests/` by default. A different root is valid only
when the repository declares it unambiguously in the proof configuration or harness. The align
reports an absent or ambiguous root; it does not infer one from whichever directory happens to
contain test files.

## 2. The canonical tree

The recommended proof tree is:

```text
tests/
├── README.md             # generated scope, layers and gate record
├── conftest.py           # thin bootstrap and shared registration only
├── fixtures/             # fixtures reused by more than one test module
├── unit/                 # isolated functions and classes
├── contract/             # repository-facing interfaces and schemas
├── data/                 # optional; only when the repo declares a data boundary
└── integration/          # sessions, services and external boundaries
```

`unit`, `contract`, `data` and `integration` are recommended names, not a demand that every
repository invent evidence it does not produce. A target may use another name when its proof
contract declares the layer's meaning and its collector derives one stable marker from it.

The `data/` directory is conditional. It exists when the target declares data transformations,
data products or a comparable data boundary; otherwise its absence is conformant. A declared
layer cannot remain empty merely because its directory exists in the recommendation.

## 3. Root files

`tests/README.md` is the generated front door. It records the measured source roots, declared
layers, fixture reach, gate command and any conditional layer that is not applicable. Authored
scope and rationale remain outside its generated zone.

`tests/conftest.py` is the thin bootstrap. It may register the collector, stable test-wide
configuration and fixtures whose ownership is genuinely suite-wide. It is not a second home for
layer-specific tests or a catch-all fixture library.

`tests/fixtures/` contains only reusable fixtures. A fixture used by one module remains beside
that module; moving every fixture to the root hides ownership and inflates the shared state.

## 4. Layer placement

Each test module is placed in exactly one layer directory. A test that crosses a boundary is
classified by the contract it asserts, while its fixture reach is declared independently. The
collector projects the directory into the runner marker; authors do not maintain a second layer
label by hand.

## 5. CI boundary

The CI workflow may live outside `tests/`, but it invokes the proof gate declared by the generated
README and preserves the same measured roots, layer collection and order check. A workflow is a
consumer of this front, not another proof root.

<!-- rationale -->

The tree gives a proof align one stable place to find scope and layer evidence. The optional data
directory keeps a target without data transformations from carrying an empty ceremonial branch.

The generated README is the front door because the source of truth is distributed between the
tree, the measured roots and the gate command. Keeping the authored surface outside the generated
zone preserves local context without allowing a hand-edited inventory to drift.

The root bootstrap stays thin so a fixture's reach and a layer's meaning remain visible where they
are used. CI stays outside the root because execution policy belongs to the repository's delivery
system while the proof contract belongs to the test surface.
