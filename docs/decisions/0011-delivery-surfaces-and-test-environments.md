# 0011. Three delivery surfaces and explicit test environments

- Status: accepted
- Date: 2026-10-13
- Amends: [0004](0004-functional-tests-on-free-edition.md) and [0005](0005-copier-template.md)
- Extends: [0009](0009-one-library-shared-task-scopes.md) and
  [0010](0010-behavior-derived-independent-development.md)

## Context

The product must work as an installable Python library, a starting point for new projects and a
set of organized agent workflows. The epic mentioned template and skills but did not specify
plugin packaging or their independent installation contracts. It also assumed a single free test
workspace without a way to validate capabilities unavailable there.

## Decision

- Deliver one Python distribution plus an updatable Copier template and a Claude Code plugin.
  Keep their initial versions coordinated; the plugin is optional to install and is not a Python
  runtime dependency. Its workflows use the library's public APIs/CLI, not a duplicate framework.
- Keep library code in `src/bricksmith`, template sources in `template/`, rendered synthetic
  examples in `examples/` and the plugin in `plugins/bricksmith`. A template may provide thin
  agent guidance, but it does not duplicate the plugin's skills into every project.
- Verify template updates and plugin local loading/installation before release. Marketplace
  publication is a separate action, not a prerequisite for developing or testing the plugin.
- Treat the generic epic and delivery contracts as the implementation handoff. The private
  reference path and local evidence are not required inputs; refine child specifications from
  public contracts and independent tests when details remain open.
- Use Free Edition for primary development and tests where feasible. Use a deliberately selected
  corporate test environment for supplementary proof of unavailable required platform behavior.
- Allow both environments in ignored `.env` configuration using profile names, expected hosts,
  test catalogs, schema prefixes and compute policy. Select one environment per execution context;
  bundle target and authentication profile are separate concepts.
- Load dotenv only explicitly through a command/test entrypoint. Keep imports pure and clients
  contextual. Map the selected profile into native Databricks unified auth without silent defaults,
  conflicting environment credentials or process-global profile mutation.
- Verify authentication and unattended CI feasibility rather than assuming M2M, federation or PAT
  support. Missing configuration, quota exhaustion or an unsupported feature never activates
  another environment automatically.
- Record setup and test evidence by stage, feature and environment. Corporate tests use isolated
  synthetic objects in the configured test scope and require no production data or private source.

## Consequences

- [Epic 0001](https://github.com/holetz/bricksmith/blob/quenching/specs/5.md) includes explicit plugin slice BS-18 and expanded
  configuration, template and release tasks.
- [Delivery contracts](../planning/delivery-contracts.md),
  [test setup](../development/databricks-tests.md) and [.env.example](../../.env.example) specify
  the implementation and setup expectations.
- The current skeleton does not yet implement the test-context loader, remote harness, template
  or plugin. Creating configuration files is preparation, not proof of those capabilities.
- A fresh checkout must implement and validate the product without opening the private reference.
  Release evidence distinguishes actual assertions from unsupported or not-run cases.
