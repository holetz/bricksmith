# Configure Databricks test environments

- Status: configuration contract and setup guide; remote harness still planned in BS-00/BS-01
- Documentation checked: 2026-10-13

## Test policy

Unit and metadata tests run locally without Databricks credentials. Free Edition is the primary
environment for development, functional tests and rendered-project end-to-end tests where the
capability is available. Corporate Databricks is a supplementary environment for platform
features that Free Edition cannot validate.

Free Edition currently provides serverless compute, permits at most five concurrent job tasks,
restricts network access and has quota and administrative limits. Plan tiny synthetic runs with
at most two concurrent tasks by default. See the
[official limits](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations).

Never silently retry on the corporate environment after a quota failure, missing permission,
unsupported feature or unavailable profile. Record the reason and select the other environment
deliberately. Corporate tests use only the configured test catalog and unique test-owned schemas;
they do not depend on corporate data or production pipelines.

## Local configuration

Copy [.env.example](../../.env.example) to `.env` once. `.env` stays ignored by Git. It can register
both test environments using the following names; unused environment fields may remain blank.

| Field | Meaning |
|---|---|
| `BRICKSMITH_TEST_ENV` | Selected environment: `free` by default, or explicit `corporate` |
| `BRICKSMITH_RUN_REMOTE_TESTS` | Remote-test execution opt-in; initial value `false` |
| `BRICKSMITH_TARGET` | Bundle target, initially `ci`; separate from profile/environment selection |
| `BRICKSMITH_TEST_MAX_CONCURRENT_TASKS` | Test concurrency budget, initially `2` |
| `BRICKSMITH_FREE_PROFILE` / `BRICKSMITH_CORPORATE_PROFILE` | Actual named profiles in the user's external Databricks configuration |
| `BRICKSMITH_FREE_WORKSPACE_HOST` / `BRICKSMITH_CORPORATE_WORKSPACE_HOST` | Expected workspace endpoints; preflight compares these with resolved authentication |
| `BRICKSMITH_FREE_CATALOG` / `BRICKSMITH_CORPORATE_CATALOG` | Explicit disposable-test destinations, configured after checking available catalogs and permissions |
| `BRICKSMITH_FREE_SCHEMA_PREFIX` / `BRICKSMITH_CORPORATE_SCHEMA_PREFIX` | Test schema prefix; harness adds a unique run ID and records ownership |
| `BRICKSMITH_FREE_COMPUTE` / `BRICKSMITH_CORPORATE_COMPUTE` | `serverless` initially; any corporate classic-compute proof needs its own explicit configuration/specification |

These `BRICKSMITH_*` values describe the planned test harness. The current Databricks CLI does not
interpret them, and simply creating `.env` does not authenticate or configure a workspace.

Only the selected environment is validated and activated. Its profile maps to the standard
`DATABRICKS_CONFIG_PROFILE` setting or an explicit SDK `Config(profile=...)`; selected serverless
compute maps to `DATABRICKS_SERVERLESS_COMPUTE_ID=auto` or the explicit Connect builder.
The [unified-auth field reference](https://docs.databricks.com/aws/en/dev-tools/auth/env-vars) and
[Connect compute configuration](https://docs.databricks.com/aws/en/dev-tools/databricks-connect/cluster-config)
document those settings.

Do not set a single permanent `DATABRICKS_CONFIG_PROFILE` or serverless variable in this dual
configuration: it could accidentally apply one workspace's settings to the other. Activation
belongs to the selected execution context. Project overrides affect a new invocation, not an
already-created global client or session.

Profile names and hosts are not credentials. Keep credentials in the authentication tooling's
external secure storage or CI secret environment, and never copy the corporate connection file,
tokens or OAuth secrets into the template or release artifacts.

## Set up named profiles

1. Create the Free Edition workspace through the
   [official signup flow](https://docs.databricks.com/aws/en/getting-started/free-edition).
2. With the installed Databricks CLI, create a dedicated profile using your workspace URL:

   ```bash
   databricks auth login --host "<free-workspace-url>" --profile bricksmith-free
   ```

3. For the supplementary environment, reuse an explicitly selected existing profile or log in
   under a dedicated name:

   ```bash
   databricks auth login --host "<corporate-workspace-url>" --profile bricksmith-corporate
   ```

4. Put the actual profile names and expected host URLs in `.env`. Do not rename or overwrite an
   existing profile without choosing to do so. Set the test catalogs only after verifying access.
5. In a terminal without conflicting exported credentials/hosts, check each intended profile:

   ```bash
   databricks current-user me --profile bricksmith-free
   databricks current-user me --profile bricksmith-corporate
   ```

   Run only the profiles you configured. These commands contact the workspace; their output may
   contain identity information and stays local. A successful identity call is not a compute or
   deployment test.

The local flow uses the CLI's interactive OAuth mechanism when supported by the chosen workspace.
The CLI manages authentication outside the repository. See
[CLI authentication](https://docs.databricks.com/aws/en/dev-tools/cli/authentication).
Do not infer workspace support for service-principal OAuth, token federation or PATs from the
presence of a setting. Validate the actual method; leave it pending when unavailable.

`.env` is not automatically read by Python or the Databricks CLI. `uv run --env-file .env ...`
can load it for a command; before the harness exists, CLI commands above use explicit profile
flags. The library must not discover/load dotenv files or authenticate as an import side effect.

## Planned preflight and setup proof

BS-00/BS-01 must implement an environment check and remote-test runner with these behaviors:

| Stage | Required proof |
|---|---|
| Offline configuration | Explicit selected profile/host/catalog, valid compute and concurrency, no conflicting active auth mode, no profile fallback |
| Authentication | Resolve and confirm the expected workspace; verify current identity without printing credentials |
| Compute | Connect to the selected compute and run `select 1`; record compatible Python/Connect/runtime versions |
| Disposable resources | Create a unique synthetic test schema and volume, check permissions, then remove exactly those owned resources |
| Bundle deployment | Validate, deploy and run the minimal Python-resource job under target `ci`; inspect result and destroy test resources |
| Recovery | Inject failure, stop dependent work and perform teardown; report resources left over rather than declaring cleanup successful |

Report configured, authenticated, compute-tested and deployment-tested as separate states. This
guide and a filled `.env` alone prove none of the remote stages. Running the preflight requires
the selected environment's fields; the unused environment is not a setup blocker.

The planned precedence for bricksmith invocation is explicit command/API parameters, process
environment, explicitly loaded `.env`, then non-secret project defaults. Authentication remains
Databricks unified authentication. In local profile mode, reject conflicting exported host/token/
client credentials rather than mixing them with another workspace's profile. In CI credential
mode, validate the injected expected host and auth mode instead of requiring a developer profile.
Never silently use `DEFAULT` when a selected named profile is absent.

## Capability coverage and corporate escalation

Maintain a feature-level capability matrix for each environment. Each required release scenario
records its environment, versions, date, synthetic inputs, result and cleanup evidence. Use:

- **Passed**: assertions ran successfully in the named environment.
- **Failed**: assertions or execution failed; investigate rather than changing environments silently.
- **Unsupported**: the environment lacks the feature; record the reason and the chosen alternative.
- **Not run**: no proof, including missing credentials or exhausted quota.

Validate ordinary discovery/execution, Delta writes, model lifecycle, monitoring and template flows
on Free Edition whenever feasible. Validate unavailable permissions/governance, enterprise
authentication, special networking or compute in the selected corporate test scope when required.
A skip on Free Edition is not release evidence for that feature; the corporate result must be
recorded or the support claim reduced explicitly.

CI unit checks need no `.env`. Remote workflows use separate `databricks-free` and
`databricks-corporate` environments with independent auth, destinations and one serialized
concurrency group per workspace. The free workflow is the primary candidate once unattended
authentication is proven. Corporate runs start on demand or through an explicitly configured
workflow; they are never a fallback hidden in the free workflow.

Prefer a verified unattended OAuth method where available; its setup is BS-00 work, not an assumed
property of Free Edition. Do not export an interactive developer token cache into CI. If Free
Edition cannot support the chosen unattended flow, retain local functional proof and report CI
coverage honestly; use separately selected corporate CI for the needed proof.
