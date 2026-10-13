# Library, project template and Claude Code plugin

- Status: accepted product direction; implementation belongs to the epic's child specifications
- Date: 2026-10-13
- Owners: BS-00, BS-16, BS-18 and BS-17 in [Epic 0001](../epics/0001-bricksmith-library.md)

## Three delivery surfaces

bricksmith is one Python library with two companion artifacts. A user can install and use the
library directly, initialize a project from the template, and optionally install the Claude Code
plugin to operate it. The plugin is a release deliverable; installing an agent application is
not a prerequisite for using the library or generated projects.

| Surface | Planned home | Contract | Release evidence |
|---|---|---|---|
| Python library | `src/bricksmith`, root `pyproject.toml` | One wheel/source archive, stable public API and CLI; optional external-integration extras | Clean install, offline imports, deterministic plans and verified runtime behavior |
| Copier template | `template/` | Project structure, configuration examples, deployment/tests/CI and thin agent guidance; one pinned library dependency | Fresh render, upgrade of an edited project, deploy/run/cleanup and no private source access |
| Claude Code plugin | `plugins/bricksmith/` | Namespaced skills operating the library's public API/CLI; no duplicate runtime engine | Manifest validation, local load, skill discovery and example workflows with and without the plugin |

Use one coordinated release version for the library and companion artifacts initially. Store the
supported library and Claude Code version ranges in release metadata and test them. Separate
installation surfaces do not introduce another Python distribution or independent framework.

## Project initialization and update

The template must ask for a project name, logical catalog/schema configuration and the optional
agent integration. It renders a minimal runnable synthetic example, deployment target `ci`,
test configuration, `.env.example`, unit/remote-test separation and setup instructions. Workspace
hosts, actual profile names and credentials are local configuration, not template answers shipped
in examples or tracked update metadata.

The default example mixes data, model and monitoring tasks in a shared scope; a second example
shows optional dedicated scopes. A minimal pipeline is delivered first, then grows as capabilities
land. The examples use the installed library and never embed framework implementations.

Choose agent integration `none` or `claude` at initialization. With `none`, all documented workflows
remain available through the API/CLI. With `claude`, render thin project instructions that point
to the public contracts and plugin installation instructions; do not copy all plugin skill bodies
into every generated project. Plugin installation is a separate explicit user action.

`copier update` must preserve edited tasks, local `.env`, user metadata and answers while detecting
conflicts. Tests render the same answers twice, upgrade an intentionally edited project, and
install the library from the release wheel. Publishing template tags or marketplace entries is
separate from preparing those artifacts.

## Claude Code plugin

The planned plugin layout uses `.claude-plugin/plugin.json` for metadata and `skills/<name>/SKILL.md`
for each workflow. Skills and any supporting resources live under the plugin root. This follows
the [official plugin manifest reference](https://code.claude.com/docs/en/plugins-reference).

The initial workflow groups are initialization, registry/naming review, planning and execution,
comparison and diagnosis, experiment/model development, monitoring, change and retirement.
Descriptions identify when a skill applies; content uses project configuration and public CLI
contracts. Capability installation and a workflow's preconditions must be checked before action.

The plugin may include lightweight supporting scripts. Runtime planning, data writes, model
selection and statistical calculations belong to the Python library. Agents, hooks and MCP servers
are optional additions justified by a workflow; they are not required packaging boilerplate.
The plugin never performs login, creates accounts or switches to the corporate environment in
the background.

Local development must validate the plugin manifest and discover/load the skills without a
marketplace. A repository-local marketplace descriptor can be added to test installation and
update, but is not needed for the first local-load proof. Claude Code supports loading a plugin
directory during development; see [plugins overview](https://code.claude.com/docs/en/plugins).

Actual validation and behavioral testing require a supported Claude Code installation. Offline
CI can check packaging, relative paths, frontmatter, release versions and CLI examples; it must
not claim that those checks prove live skill execution. Record the manual or automated live
smoke evidence separately.

## Independent implementation handoff

The epic, these delivery contracts, the [test environment contract](../development/databricks-tests.md)
and public platform documentation are sufficient planning inputs for a new implementation context.
The private reference path, local evidence map and its availability are not prerequisites.

The epic defines outcomes and acceptance, not finalized APIs for every slice. Before coding, a
child specification closes that slice's listed API/behavior questions and designs its synthetic
test fixtures. If a generic requirement is ambiguous, resolve it in that specification; do not
request the private source as the default way to fill gaps.

Release tests must succeed in a clean checkout with no private source, `CLAUDE.local.md` or local
research notes. A contributor who has only the repository and its configured test environments
must be able to reproduce the documented evidence.
