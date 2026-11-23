# `.agents/quenching.json` — the envelope and recognised namespaces

The specs front's own copy of what `.agents/quenching.json` recognises, with `.agents/quenching.json`
read as legacy while it exists — the root metadata and
namespaced declarations `.agents/quenching/bin/cq specs config` reads, their defaults, and the refusals a malformed value
earns. Specs live on the `quenching` branch: the file selects that store and configures the tracker card
and, for deprecated tracker backends, their transport and placement, never a repository specs
tree. Other namespaces are read by their owning front.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## The envelope and recognised namespaces

<!-- rules -->

| Key | Values | Default | Read by |
| --- | --- | --- | --- |
| `backend` | `git` · `github` · `azure-boards` (the last two deprecated) | derived from the repository remote | store selection and legacy-value refusal |
| `specs.card` | `{provider: github \| azure-boards \| none, at: capture}` | `github` on a GitHub remote, else `none` | the card layer, on the branch or here |
| `shared.worktreeSetup` | a shell command, run as written | none | the isolation cut, after `git worktree add` |
| `shared.worktreeSlots` | `{min, max, waitMinutes, pollSeconds}`, positive integers, and `setupIdempotent: boolean` | `{min: 4, max: 12, waitMinutes: 30, pollSeconds: 30, setupIdempotent: false}`, key by key; `max` never below `min` | `.agents/quenching/bin/cq git worktree acquire`, `slots` and `cq git stale` (the floor); `setupIdempotent` silences `cq specs doctor`'s `sp-config-setup-idempotence` |
| `shared.sharedPaths` | a list of repo-relative paths | none — an absent or empty list declares no shared area | `.agents/quenching/bin/cq git worktree link`, from the isolation flow |
| `shared.regenerablePaths` | a list of repo-relative paths added to the built-in Python caches (`.venv`, `venv`, `__pycache__`, `*.py[cod]`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.pyright`, `.tox`, `.nox`, `htmlcov`, `.coverage`, `.coverage.*`, `*.egg-info`, `.eggs`, `pip-wheel-metadata`, `.cache`, `.ipynb_checkpoints`, `.hypothesis`, `node_modules`); a slash-less entry matches that name at any depth and a `*` in it matches with `fnmatch`; an entry with a slash (or a trailing slash, `data/`) anchors literally at the root | none — the built-in list still applies, and `build/` and `dist/` stay out on purpose | `.agents/quenching/bin/cq git worktree retire`, which discards matching ignored files without `--discard-ignored`, and `.agents/quenching/bin/cq git stale`, which classifies them as `ignored.regenerable` |
| `shared.hooks` | `{"<event>": [{"command": "<cmd>", ...}]}` | none — an absent key declares no events | the command that owns the event |
| `shared.profiles` | `{"installed": ["knowledge", "specs", "design", "components", "ops", "proof", "toolchain", "delivery", "opensource", "security", "git"]}` | none — an absent key leaves all eight local fronts eligible | the align conductor's conduction scope |
| `shared.gitConventions` | `{commitSubject, branchName, prTitle, prBody, mergeSubject}` — prose directives, never templates | `{}` | the whole `git` pillar, through `.agents/quenching/bin/cq git conventions`; see [git/conventions.md](.agents/quenching/references/git/conventions.md) §The declared-directive layer |
| `shared.autonomy` | `{runAloneUpTo: none \| low \| medium \| high \| xhigh, merge: policy \| never \| dependencies \| all, maxWorkers: 1-8, maxLaunches: ≥ 1, livenessMinutes: ≥ 1}` — a bad leaf falls back to its default and is named in `warnings` | `{runAloneUpTo: low, merge: policy, maxWorkers: 3, maxLaunches: 40, livenessMinutes: 20}`, leaf by leaf | `/quenching-specs orchestrate`, the `orchestrator` and `/quenching-specs autopilot`, through `.agents/quenching/bin/cq config show --effective` |
| `shared.workHost` | `{target, dockerContext, runner, remoteRoot, syncFlags, syncExclude, pullBack, protectedPaths, minFreeGb}` — a bad leaf falls back to its default and is named in `warnings` | disabled until `target` is set | `.agents/quenching/bin/cq config host --plan`, which prints the dispatch steps and runs nothing |
| `shared.okfValidate` | `{warnAsError: boolean, ignoreGlobs: list of strings}` | `{warnAsError: false, ignoreGlobs: []}`, key by key; wins over `.claude/hooks/hooks-config*.json` | `.agents/quenching/bin/cq knowledge validate` |
| `shared.contextBudgets` | `{main, worker, conductor}`, positive integers | `{main: 200000, worker: 200000, conductor: 150000}` | `.agents/quenching/bin/cq components session context`: `worker` is a **size estimate**, never a stop, and `conductor` is the stop checked between waves; `worker` is also the default of `.agents/quenching/bin/cq specs estimate --worker-budget` |
| `shared.cache` | `{rebuildLimitUsd: number ≥ 0, warnMinutes: integer ≥ 1, ttlMinutes: auto \| 5 \| 60, costStyle: dollars \| times}` — a bad leaf falls back to its default and is named in `warnings` | `{rebuildLimitUsd: 0.5, warnMinutes: 15, ttlMinutes: auto, costStyle: dollars}`, leaf by leaf; a long chat is `shared.contextBudgets.main` | the `quenching-cache` plugin's bar above the prompt, through `.agents/quenching/bin/cq config show --effective` |
| `shared.codex` | `{maxUsedPercent: {primary, secondary}, timeoutSeconds, executor}` — a bad leaf falls back to its default and is named in `warnings`, and so does a leftover `crossReview` or `model` of the removed cross review | `{{80, 90}, 900}` and `executor.enabled: false`, leaf by leaf — Codex is opt-in | `cq codex` |
| `shared.gitIdentity` | `{allowedEmails: [address or fnmatch pattern, …]}` | none — nothing is judged | the identity gate (`cq git commit`), failing closed on a malformed value |
| `shared.mcp` | `{mount: "specs", command: [strings]}` | none — the MCP is registered with the default mount and a resolved Python prefix | `cq deploy`, from the repository document only |
| `delivery.provider` | a provider name, declared explicitly | none — absent declares no provider; it chooses no pipeline policy | the delivery front (`delivery/config.py`) |
| `specs.artifactLanguage` | a non-empty string | none | `.agents/quenching/bin/cq specs`, on the branch or here |
| `specs.cardAt` | the sibling spelling of `specs.card.at` | none | the card layer, on the branch or here |
| `specs.chat` | `{model, budgetUsd, timeoutSeconds}` | `{sonnet, 0.50, 300}` | the portal's `--chat`; `--chat-budget-usd` wins |
| `specs.estimate.writeOnly` | a list of repo-relative paths, exact or `fnmatch` globs | `[]` — nothing is discounted | `.agents/quenching/bin/cq specs estimate`: a matching task file is `writeOnly: "declared by repo"`, sized but not counted; an Impact standard is always counted |
| `specs.azureStates` | `{"plans": "<state>", "archive": "<state>"}` | **none, deliberately** — refuses (exit 2, `sp-az-no-states`) rather than guess | the `azure-boards` backend only |
| `specs.azurePlacement` | `{areaPath, workItemType, discoveryTag, team, iterationPath, boardColumn, defaultSubject}` — `workItemType` retired, see `workItemTypes` below | per sub-key — `areaPath` **none, deliberately**, the rest default | the `azure-boards` backend only |
| `specs.azureColumns` | `{"<board state>": "<lane>", …}` — any subset | `{}` — falls back to `azurePlacement.boardColumn` per state | the `azure-boards` backend only |
| `specs.subjects` | `{"<key>": {name, description, parent, tags}, …}` | `{}` | `/quenching-specs create`'s subject proposal |
| `specs.tagCatalog` | `{"<tag>": "<description>", …}` | `{}` | `/quenching-specs create`'s tag proposal |
| `specs.workItemTypes` | `{"<key>": {description, azure, github, default}, …}` | `{}` | `/quenching-specs create`'s type proposal, `.agents/quenching/bin/cq specs new --type`, and every backend's `create_spec` |
| `ops.opsRoot`, `ops.router` | repository-relative paths | none — `op-config-missing` when absent | `.agents/quenching/bin/cq ops` |
| `ops.registry` | a repository-relative path | `<ops.opsRoot>/README.md` | `.agents/quenching/bin/cq ops registry` |
| `proof.proofRoot` | a repository-relative path | `tests` | `.agents/quenching/bin/cq proof` |
| `proof.layers`, `proof.measuredRoots`, `proof.proofExclusions`, `proof.ratchetPath`, `proof.cost` | proof declarations | documented proof defaults | `.agents/quenching/bin/cq proof` |

`specs.azureStates` is the one key whose absence is a refusal rather than a
default — a guessed state mapping would not fail loudly, it would read every archived spec as
active in half the projects it ran against.

`shared.gitConventions` is read by the `git` pillar rather than by `.agents/quenching/bin/cq specs`, and it sits here
because this table is the one home for what the file recognises. `shared` is its namespace for the
same reason `worktreeSetup` and `hooks` have it: the pillar runs inside every front's build loop.
Its two malformed shapes earn their own findings — `sp-config-unknown-git-convention` for a sub-key
outside the five, `sp-config-bad-git-convention` for a recognised one whose value cannot direct
anything.

Root-level front-owned keys such as `opsRoot`, `router`, `proofRoot`, `layers`, `hooks` and
`worktreeSetup` are legacy flat declarations. A flat or mixed document is refused with
`sp-config-unscoped` and exit 2, naming each key and its destination namespace; no namespace wins
and no value is merged.

The checker's `warnAsError` and `ignoreGlobs` are `shared.okfValidate`; `.claude/hooks/hooks-config.json`
stays the layer below, and the bundle root is the fixed `/docs/` convention no configuration names.

`.agents/quenching/bin/cq config init --write` seeds the file with every default above spelled out (the `shared.codex`,
`autonomy`, `contextBudgets`, `okfValidate` and `workHost` blocks and `specs.chat`); `.agents/quenching/bin/cq config check`
and `cq doctor` list the seeded keys a file lacks. They only report: an existing file is never
rewritten.

## The local layer, the scopes and the one writer

<!-- rules -->

`.agents/quenching.local.json` is this repository on this machine: the same envelope, kept out of
git through `.git/info/exclude`. Each key carries a **scope** in the registry
(`.agents/quenching/bin/cq config schema` prints it), and the scope places the local value:

| Scope | The local layer | Keys |
| --- | --- | --- |
| `machine` | wins over every file | `shared.worktreeSlots` (but `setupIdempotent`), `shared.workHost`, `shared.contextBudgets`, `shared.cache.ttlMinutes`, the Codex quota ceilings |
| `either` | fills a key the project leaves out; the project wins | `shared.autonomy`, the rest of `shared.codex`, the rest of `shared.cache`, `shared.okfValidate`, `specs.chat` |
| `project` | ignored and named in `localConfig.ignored` | everything else — the team's contract |

`.agents/quenching/bin/cq config set <key> <value> --layer project|local` and `unset <key> --layer …`
are the only writers: each validates the value against the registry, refuses a `project` key in
the local layer (`cq-config-scope-project`), keeps every other key and its order, and adds the
local file to `.git/info/exclude` on its first write. `.agents/quenching/bin/cq config show
--effective` names the layer each value came from, `local` included.

`cq doctor --setup` previews a minimal file from the detected provider and `--write` confirms it; it records the deprecated root `backend`, so prefer `.agents/quenching/bin/cq config init`.
