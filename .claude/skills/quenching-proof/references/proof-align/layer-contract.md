# Proof front layer contract

<!-- rules -->

This file is self-contained: a proof align reads it to classify tests, derive markers, place
fixtures and decide when a declared layer is empty.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## The four layer rules

### 1. Directory classification

Every collected test belongs to one declared layer directory. The directory's name is the
classification; a test left at the proof root or outside any declared layer is not silently
assigned to a default.

`pf-unlayered` is raised when a test has no layer directory or when its path cannot be mapped to
one declared layer.

### 2. Derived marker

The collector derives the runner marker from the layer directory. A hand-applied marker is not a
second authority: if a test's explicit marker disagrees with the directory projection, the
projection wins and the disagreement is reported for removal.

`pf-unmarked` is raised when collection cannot attach the derived marker to a test, or when the
test carries a layer marker that does not match its directory.

### 3. Fixture ownership and reach

A fixture used by one module stays local to that module. A fixture shared by more than one module
belongs in `tests/fixtures/` and declares one of the following reaches. `tests/fixtures/` is the
fixture library; a `tests/fixtures/conftest.py` is part of that library and is exempt from the
root-bootstrap rule below.

| Reach | Boundary |
| --- | --- |
| `nothing` | Pure values or factories; no repository, process or service state. |
| `tree` | Filesystem state inside the test tree or a temporary checkout. |
| `session` | A process, database session, Spark session or equivalent runtime. |
| `workspace` | A shared external workspace, service, catalog or environment boundary. |
| `custom:<reason>` | An explicit escape reported without default grading. |

`pf-loose-fixture` is raised when the same fixture name is defined in two or more test modules
instead of being defined once in the shared library. The inventory records the library's declared
reach for the later gate; this check does not infer reach from pytest's `scope` field. `custom:<reason>`
is reported, not graded as a new reach by this contract.

### 4. Thin bootstrap and declared layers

`tests/conftest.py` contains only suite-wide bootstrap, registration and genuinely suite-wide
fixtures. Layer-specific setup, data builders and integration clients live with the layer or in
the shared fixture library with an explicit reach.

`pf-fat-conftest` is raised when the root `tests/conftest.py` owns layer-specific fixtures or setup,
duplicates the fixture library, or carries environment work that a layer can own directly.
`tests/fixtures/conftest.py` is not the root conftest and is therefore the allowed library home.

`pf-empty-layer` is decided by one question: **did the target declare this layer as required
evidence?** If yes, an empty directory is an error because the declaration promises a proof kind
the suite does not exercise. If no, the absent or empty optional layer is not a finding; the target
records its non-applicability rather than creating a placeholder.

## Minting one declared layer

`/quenching-proof layer-new` applies the five coupled edits as one unit for the layer a human names:

| Edit | Contract it closes |
| --- | --- |
| Create `tests/<layer>/` | Gives the layer's tests one stable classification path. |
| Register `<layer>` in `[tool.pytest.ini_options].markers` | Makes the derived marker accepted by strict pytest collection. |
| Add the named path-to-marker block to `tests/conftest.py` | Makes collection derive the marker from the directory rather than a second hand label. |
| Create `tests/fixtures/<layer>.py` when the declared reach needs shared setup | Gives reused fixtures one library home with the declared reach as its ceiling. |
| Regenerate `tests/README.md` | Publishes the layer's meaning, reach, budget, marker, test count, and fixture module from the inventory. |

The layer's reach and budget are declarations in `.agents/quenching.json` (or the legacy
`.agents/quenching.json`) under `layers`; the
fixture module does not become a second configuration store. A newly minted layer may be empty
until the owner separately accepts a proposed test migration, but its five structural edits are
not split across partially declared states. The collection-hook and fixture templates under
`.agents/quenching/assets/templates/proof/` are the canonical shapes for those two generated
files.

## Finding codes

The layer contract owns these codes and no other reference defines them:

| Code | Meaning | Severity |
| --- | --- | --- |
| `pf-unlayered` | A collected test cannot be assigned to one declared layer. | `error` |
| `pf-unmarked` | The derived layer marker is absent or disagrees with the test path. | `error` |
| `pf-loose-fixture` | A fixture name is defined in multiple test modules instead of once in the library. | `error` |
| `pf-fat-conftest` | Root conftest contains layer-specific or duplicative setup; the fixture-library conftest is exempt. | `error` |
| `pf-empty-layer` | A layer declared as required evidence has no collected tests. | `error` |

An optional layer is not made green by suppressing `pf-empty-layer`; it is omitted from the
declaration and recorded as not applicable. That deciding question is what keeps a genuinely
missing proof kind distinct from a repository that never claimed it.

<!-- rationale -->

The directory-to-marker projection removes a second label that can drift while still looking
plausible to a runner. Fixture reach remains orthogonal because isolation is a property of setup,
not of the assertion layer that happens to consume it.

The empty-layer decision is declaration-sensitive by design. A universal error would force every
target to claim data or integration evidence; a universal waiver would let a target promise a layer
without exercising it. The deciding question makes the boundary explicit.
