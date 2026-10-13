# 0007. The registry splits mechanism, preset and seed

- Status: accepted; packaging and dependency boundaries amended by
  [0009](0009-one-library-shared-task-scopes.md) on 2026-10-13
- Default preset amended by [0010](0010-behavior-derived-independent-development.md)
  on 2026-10-13
- Date: 2026-10-11

## Context

In the reference implementation the column registry, the naming vocabulary and the naming checks
were methods of the catalog registry, with the policy hard-coded: which catalogs are analyzed,
which findings fail the build, the prefix list in three copies. Agent skills and CI need to run
these checks in a lightweight environment, without Spark.

## Decision

`bricksmith.registry` holds three kinds of content, kept apart. Its checks run in pure Python
without loading Spark integrations:

- **Mechanism**, in the package: the column registry (`col`, `column`, overrides), column
  conditions rendered as CHECK constraints, the vocabulary builder and tokenizer, the name
  analyzers, the ratchet and dependency exemptions with expiry dates.
- **Preset**, in the package and overridable by a project: neutral defaults for naming and
  validation. Projects configure prefixes, physical types, table categories, facet order,
  sensitivity categories and which findings fail the build; no private vocabulary ships by default.
- **Seed**, in the template: only the framework's own columns and an empty vocabulary.

Table writes read the registry for COMMENT, NOT NULL and CHECK. Registry and table code may
share abstractions within the library. Registry data stays in data files edited by people:
decision 0002 removes generated YAML for bundle resources, not hand-maintained data.

## Consequences

- The planned `uvx bricksmith check` runs in pre-commit, CI and agent skills with no Spark install.
- A project defines or replaces its grammar without forking the package. Reference-derived
  naming policies are not the default.
