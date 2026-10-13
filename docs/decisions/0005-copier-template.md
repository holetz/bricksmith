# 0005. The project template is rendered with copier

- Status: accepted
- Companion-plugin delivery amended by [0011](0011-delivery-surfaces-and-test-environments.md)
  on 2026-10-13
- Date: 2026-10-11

## Context

bricksmith must also work as the starting point of a new data project: the folder structure,
`databricks.yml`, `pyproject.toml`, CI workflows, agent settings and standards. A template that
evolves has to reach the projects already created from it. `databricks bundle init` renders once
and has no update path.

## Decision

The template lives in `template/` and is rendered with copier. `copier update` merges template
changes into existing projects. The `bricksmith` library arrives as a pinned dependency. The
optional Claude Code plugin packages the skills; local loading/installation is verified before
any marketplace publication. The template carries structure, configuration and thin agent
guidance, without duplicating library code or skill bodies.

`examples/` holds the template rendered with fixed answers. CI fails when the committed examples
drift from a fresh render, and the end-to-end tests deploy the examples, which tests the template
itself.

## Consequences

- One source for structure, three uses: new projects, the examples and the end-to-end tests.
- The template's answers file becomes part of every generated project and must stay stable
  across versions.
- Actual workspace hosts, profile names and credentials stay in ignored local configuration or
  CI settings, outside tracked template answers. Both test environments may be configured.
