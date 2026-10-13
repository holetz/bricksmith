# 0004. Free-first functional tests with supplementary corporate proof

- Status: accepted; environment and authentication policy amended by
  [0011](0011-delivery-surfaces-and-test-environments.md) on 2026-10-13
- Date: 2026-10-11
- Remote authentication, compute and deployment: pending BS-00 proof

## Context

The library and rendered examples need proof against Databricks. Free Edition is the primary
personal development/test environment, but it cannot validate every platform capability. Its
current constraints include serverless-only compute, a maximum of five concurrent job tasks,
quota restrictions and unavailable account-level administration. These are checked against
[official limits](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations);
feature availability is recorded per actual workspace, not assumed.

## Decision

| Layer | Scope | Execution policy |
|---|---|---|
| Unit | Registry, metadata/resources, graph, configuration and planning | Every pull request, no workspace or credentials |
| Functional | Writes/history, metadata, models, monitoring and other platform behavior | Free Edition where supported; deliberately selected corporate test scope for unavailable required capabilities |
| End to end | Render/update the template, deploy target `ci`, run synthetic jobs, inspect results and destroy | Free Edition as the primary scenario; supplementary corporate scenarios run explicitly |

- `.env` may register both profile names, expected workspace hosts and test destinations. The
  selected environment is explicit and independent of the bundle target; `free` is the initial
  test selection. Missing configuration never uses `DEFAULT` or another configured workspace.
- Authentication uses the selected Databricks unified-auth configuration. Local interactive OAuth
  is attempted and verified on the actual workspace. Unattended CI OAuth, federation or PAT support
  is a feasibility question, not a guaranteed Free Edition capability or an automatic fallback.
- Functional and end-to-end runs serialize per workspace and start with two concurrent tasks.
  Quota exhaustion, missing permissions and unsupported features are reported distinctly. A failed
  free run never silently retries on corporate Databricks.
- Unit CI remains available without remote credentials. Free remote CI is enabled after its auth
  proof. Corporate CI has a separate environment, credentials and explicit run selection.
- Every remote run creates unique, owned synthetic schemas and artifacts in an explicitly
  configured test catalog. Cleanup targets only those resources and reports leftovers on failure.
- Corporate testing is supplementary product-validation work in a test scope; no production jobs,
  corporate source data, private models or enterprise identifiers are required by the examples.
- A required feature skipped on Free Edition needs recorded passing evidence in the selected
  corporate environment or an explicit narrowing of supported behavior. A skip is not proof.
- Serverless is the default compute policy. Classic compute is a separately configured corporate
  validation case, never an inferred fallback.

## Consequences

- The [test environment guide](../development/databricks-tests.md) and [.env.example](../../.env.example)
  define local configuration and the required setup proofs.
- A configured profile, a successful identity request, a working compute query and a successful
  deploy/run/cleanup are separate states. Only actual test assertions establish feature coverage.
- Free Edition's usage conditions and unsupported capabilities remain visible in documentation;
  support claims are limited to independently measured free/corporate evidence.
