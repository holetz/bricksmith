# The `.agents/quenching/bin/cq specs` surface — provider operations and their payloads

This file owns the provider-facing `.agents/quenching/bin/cq specs` tool surface and the resolution contract its
commands use. The lifecycle document and the report shape live in their own focused references;
commands cite the smallest owner that answers the step they are running.

## The `.agents/quenching/bin/cq specs` tool surface

<!-- rules -->

Uniform contract: `--json` on every subcommand; strict exit codes — **0** ok · **1** findings ·
**2** refusal. A command branches on the exit code and the JSON, never on prose.

`.agents/quenching/bin/cq specs` is **stdlib-only Python**, in the same mold as `.agents/quenching/bin/cq knowledge validate`: no runtime to install
and no dependency to declare. An external backend's transport is `subprocess` over the vendor's own
`gh` / `az`, so auth, paging and API errors are not this plugin's code — and the cost of that trade
is declared rather than hidden: such a backend does not work without the binary installed, and a
missing one is a **refusal (exit 2) naming it, never a traceback**.

| Command | Use |
| --- | --- |
| `.agents/quenching/bin/cq specs new <name> [--title T] [--verification P] [--subject KEY] [--type KEY] [--tags LIST] [--complexity LEVEL]` | store a new spec in `plans` with `## Problem` as its only section by default, reporting the locator the store allocated — the ID it hands out is what every later verb takes. With a card configured the card is created first and its native number is the ID; `--card <n>` adopts an existing issue or work item instead (refused by a tracker backend). The descriptive title is supplied at capture and the date is stamped into `date:` here and never again. `--subject` applies a declared `subjects.<KEY>`'s parent (where the backend has one) and fixed tags — folded into `--tags` where both are given, never overwritten by it. `--complexity` writes the record through the same path as `record priority`. Stdin, read when it is not a tty, carries N sections in the SAME multi-heading stream `section --write` reads and writes — the stream self-declares by opening on a canonical `## <Heading>`, with no single implied heading to fall back on, so an unopened or malformed stream refuses (`sp-stray-heading`/`sp-write-duplicate-heading`) before `create_spec` ever runs. A title or tag carrying a line break or a control character refuses the same way (`sp-bad-title`/`sp-bad-scalar`), since the frontmatter reader would read it as a second key |
| `.agents/quenching/bin/cq specs list [--json] [--lean]` | every spec, by folder and derived stage. `--lean` is a tracker backend's own cheap index (ID, title, state, `spec:` labels, no document body); the git store lists the whole front locally, so it needs none. It does NOT feed the ranked table (`stage`, `tasks`, `priority` and `date` derive from the document), and says so in its own payload |
| `.agents/quenching/bin/cq specs status --spec <id> [--json]` | sections present, derived stage, task progress with recorded subjects, the records, and the outstanding gates |
| `.agents/quenching/bin/cq specs section <id> "<heading>[,<heading>…]" [--write [--clear]]` | deterministic partial read of N sections in ONE call, returned in the order asked; `--write` writes N in one call too, each created in canonical position — the bodies arrive on stdin delimited by the same `## <Heading>` lines the read prints, and the set the stream carries must equal the set declared here or the call refuses without writing any of them. A stream that does not open on a canonical heading is one raw body under the one heading declared, exactly as before. An empty body refuses (`sp-write-empty-body`) and writes nothing; `--clear` is the explicit ask that puts the template's guidance back |
| `.agents/quenching/bin/cq specs show --spec <id> [--task ID]… [--full]` | what `section` cannot say: the map of which headings and task ids exist (the default), ONE task's line and metadata, the whole document **only** under `--full`. Section bodies are `section`'s |
| `.agents/quenching/bin/cq specs artifact list\|read\|write\|rm [--spec <id>] <name>` | the one door to the artifacts tree — `specs/artifacts/<id>/<name>` on the specs branch, `specs/artifacts/_handoffs/<name>` when `--spec` is omitted; `<name>` is one path segment, UTF-8 text only, 1 MiB at most; `write` takes the body from stdin or `--from`. A backend with no such capability refuses `sp-artifact-unsupported`. Never invent a path under `/tmp` |
| `.agents/quenching/bin/cq specs record <id> <name> [--set FIELD=VALUE]…` | read or **merge** ONE frontmatter record; fields not named survive, write-once records refuse (exit 2) with the value they hold, and a value carrying a line break or control character refuses (`sp-bad-scalar`) |
| `.agents/quenching/bin/cq specs tags\|assignee\|start\|target\|end <id> [value]` | read one of the five STATE keys, or set it — never a record; `tags` **replaces** the whole list, it does not append; a value carrying a line break or control character refuses (`sp-bad-scalar`) |
| `.agents/quenching/bin/cq specs verification <id> [<policy>]` | read the policy in force — and whether anything declared it — or set it. The post-capture writer: `new --verification` answers at the one moment nobody has an opinion yet |
| `.agents/quenching/bin/cq specs title <id> [<text>]` | read the `title`, or set it: the writer quotes the scalar when ` #`, `: ` or a leading YAML indicator would otherwise corrupt it, keeps the first top-level `title:` line's place and drops later ones with their continuation lines, and refuses a line break or control character (`sp-bad-title`). The repair path for a title an older `new` stamped unquoted; the `# <TITLE>` heading in the body is not touched |
| `.agents/quenching/bin/cq specs config [--json]` | the repo's declared parameters — the backend, the specs branch, `worktreeSetup`, `azureStates`, `azurePlacement`, `azureColumns`, `subjects`, `tagCatalog` |
| `.agents/quenching/bin/cq specs promote <id> --to archive [--outcome done\|abandoned] [--force]` | the one gated transition left; **exit 2** with the missing list, else the backend's own hop — closing the issue or work item, never a `git mv`: a provider-owned front has no phase directory to move a file between |
| `.agents/quenching/bin/cq specs next --spec <id> [--json]` | THE single next action, carrying the task's `verify`/`files`/`pattern`/`[P]`; skips `[!]` |
| `.agents/quenching/bin/cq specs next --front [--json] [--table] [--columns C,C] [--order rank\|priority]` | the **ranked candidate list** — the only place ordering logic lives. `--table` prints §The spec table itself, so a command quotes a rendering instead of re-aggregating a payload; `--columns` omits columns, never reorders them; `--order priority` swaps the four-factor ranking for the human's `priority.level` alone. `--table` with `--json` refuses (exit 2) — a table IS the human rendering |
| `.agents/quenching/bin/cq specs task --spec <id> --check ID [--subject LINE] [--commit SHA] \| --uncheck ID \| --block ID --reason MSG` | flip, record, or block a checkbox mechanically; `--commit` is **additive** to `--subject`, never its replacement |
| `.agents/quenching/bin/cq specs discover <id> <text>` | append one line to `## Discoveries` |
| `.agents/quenching/bin/cq specs parallel --spec <id> [--json]` | verify each `[P]` group's `files:` sets are disjoint — **exit 1** when any group is ineligible |
| `.agents/quenching/bin/cq specs schedule [--spec <id> \| --all] [--json]` | the schedule derived from `dependsOn` plus `start`/`target`/`end` (a declared `end` is the finish) — `earliestStart`, `earliestFinish`, `slipDays`, `critical`, `criticalPath`; **exit 1** on a cycle, nothing written. `--all` schedules every spec in the store (archived and edge-less included), the read a consumer needs for both directions of the graph, and adds `composites` — member id → the composites that name it, with label and wave; with `--spec` it refuses `sp-conflicting-selection` |
| `.agents/quenching/bin/cq specs depends <id> [--add 12,34] [--remove 12] [--json]` | read or edit the `dependsOn` key (ids of this store); idempotent, **exit 2** and nothing written on an unknown id, a self-reference or an edge that would close a cycle |
| `.agents/quenching/bin/cq specs validate [--spec <id>] [--by-code]` | the canonical heading set, the stage-scoped rule, the `sp-*` vocabulary. `--by-code` renders the same sweep as one line per `(code, severity)` with the count and the specs — grouped, never filtered |
| `.agents/quenching/bin/cq specs doctor` | workspace shape — the two folders, strays, older layouts; remedies **declared** for the command to apply |
| `.agents/quenching/bin/cq specs migrate` | one-way fold to the current layout (`backlog/` + `ready/` → `plans/`, and v1 three-file folders → one file); **exit 2** when there is nothing to migrate; `specs/archive/**` never touched |
| `.agents/quenching/bin/cq specs export --spec <id> \| --all [--out DIR]` | dump the canonical markdown to disk — **write-only**; nothing reads it back and nothing syncs it, so it is a rescue copy for an external backend and never a second store |
| `.agents/quenching/bin/cq specs push [--drop <id>]…` | send every pending write of the clone in one push that leaves the store branch a single parentless commit; `--drop <id>` (repeatable) discards that spec's pending writes first. A repository that declares no git store refuses `sp-push-no-store` (exit 2) |

`--outcome` is the only content a promote ever writes.

**Archiving a spec with open tasks refuses.** `--outcome done` with unchecked `- [ ]` boxes exits 2
and lists them, overridable with `--force`; `--outcome abandoned` is always allowed, because
closing out a spec that will not be built is exactly the case where open tasks are expected.

There is no `init` (scaffold is an asset copy — the align's job). There is no `store` subcommand
either: the declared backend is read by `.agents/quenching/bin/cq specs config`, never switched by a command mid-flight.

Templates live in `assets/specs/templates/spec.md` and are stamped by `.agents/quenching/bin/cq specs new` — with the
same content embedded as a fallback constant in `.agents/quenching/bin/cq specs` itself, so an installed copy under a
target's `.claude/hooks/` with no adjacent assets stamps an identical file. **Edit both or
neither.** A template's scaffold content must stay invisible to `has_real_content()`: only headings
and HTML comments, with any example inside a comment or written as a `<placeholder>`.

### Resolving the tool

Owned by
[align/tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool: bare `.agents/quenching/bin/cq specs`, with `Bash(cq:*)` in the body's `allowed-tools` — **one
form, no second door**.
