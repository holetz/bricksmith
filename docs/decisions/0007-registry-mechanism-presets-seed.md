# 0007. The registry splits mechanism, preset and seed

- Status: accepted
- Date: 2026-10-11

## Context

In the reference implementation the column registry, the naming vocabulary and the naming checks
were methods of the catalog registry, with the policy hard-coded: which catalogs are analyzed,
which findings fail the build, the prefix list in three copies. Agent skills and CI need to run
these checks in a lightweight environment, without Spark.

## Decision

`bricksmith-registry` is pure Python and holds three kinds of content, kept apart:

- **Mechanism**, in the package: the column registry (`col`, `column`, overrides), column
  conditions rendered as CHECK constraints, the vocabulary builder and tokenizer, the name
  analyzers, the ratchet and dependency exemptions with expiry dates.
- **Preset**, in the package and overridable by a project: column prefixes and their physical
  types, table types, facet order, sensitivity categories and which findings fail the build.
- **Seed**, in the template: only the framework's own columns and an empty vocabulary.

The core reads the registry when it writes a table (COMMENT, NOT NULL, CHECK); the registry never
imports the core. Registry data stays in data files edited by people: decision 0002 removes
generated YAML for bundle resources, not hand-maintained data.

## Consequences

- `uvx bricksmith-registry check` runs in pre-commit, CI and agent skills with no Spark install.
- A project adopts the reference grammar by default and can tighten or replace it without
  forking the package.
