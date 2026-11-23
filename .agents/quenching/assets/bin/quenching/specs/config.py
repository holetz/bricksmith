"""Where the workspace is, and what `.agents/quenching.json` declares about it.

Moved verbatim out of the pre-refactor specs script. `AZ_SPEC_TYPE` is imported INSIDE the two
functions that read it: it belongs to `quenching.specs.backends.azure`, which
imports this module at its top, so a module-level edge would close a cycle."""
from __future__ import annotations

import os
import sys

from quenching.common.config import (CONFIG_FILE, LEGACY_CONFIG_FILE,
                                     detect_provider as _common_detect_provider,
                                     find_repo_root as _common_find_repo_root,
                                     infer_base_branch,
                                     load_config as load_envelope, namespace)
from quenching.common.git import _git
from quenching.common.remote import remote_host as _remote_host
from quenching.specs.parse.spec import PHASE_DIRS, PHASES


def find_specs_root(root_arg: str | None) -> str:
    if root_arg:
        return os.path.abspath(root_arg)
    env = os.environ.get("SPECS_ROOT")
    if env:
        return os.path.abspath(env)
    cur = os.path.abspath(os.getcwd())
    if os.path.basename(cur) == ".specs":
        return cur
    d = cur
    while True:
        cand = os.path.join(d, ".specs")
        if os.path.isdir(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.path.join(cur, ".specs")   # default (created by `new`)


def is_root_too_high(root: str) -> bool:
    """True when `root` is the CONTAINER sitting one level above a phased `.specs/` workspace,
    rather than the workspace itself — the mis-levelled `--root`/`SPECS_ROOT` that today reads
    as merely empty. `root` must hold none of `PHASE_DIRS` itself (that is the correctly
    levelled case `_holds_phase_folder` already answers, `specs/worktree.py:190-191`) AND
    `root/.specs` must hold at least one — the same phase-folder idea reused rather than
    reimplemented a third time.

    Only ever true over an EXPLICIT `--root`/`SPECS_ROOT`. The default nearest-upward search in
    `find_specs_root` above stops the moment it finds a directory literally named `.specs`, so
    the root it returns already IS that directory — it can never be the container this predicate
    looks for, by construction, with no extra branch needed to keep the default search exempt."""
    if any(os.path.isdir(os.path.join(root, p)) for p in PHASE_DIRS):
        return False
    nested = os.path.join(root, ".specs")
    return any(os.path.isdir(os.path.join(nested, p)) for p in PHASE_DIRS)


ROOT_TOO_HIGH_REMEDY = "point --root at the .specs/ directory itself"


def root_too_high_message(root: str) -> str:
    """The one message text for `sp-root-too-high`, written here and cited by both call sites
    (`open_backend`'s refusal, `doctor`'s finding) rather than duplicated at each."""
    return (f"'{root}' has no phase folder of its own, but "
            f"'{os.path.join(root, '.specs')}' does — this --root/SPECS_ROOT points at the "
            f"workspace's container, not the workspace itself")


CONFIG_KEYS = ("backend", "worktreeSetup", "sharedPaths", "azureStates",
               "hooks", "profiles",
               "azurePlacement", "azureColumns", "subjects", "tagCatalog",
               "workItemTypes")
BACKENDS = ("github", "azure-boards", "git")
# The specs-axis keys the `git` store's branch `quenching.json` may declare. With `"backend":
# "git"`, each one present on the branch replaces the same key from `.agents/quenching.json`;
# what describes the code tree stays in `.agents/quenching.json`.
BRANCH_CONFIG_KEYS = ("subjects", "tagCatalog", "workItemTypes", "azurePlacement",
                      "azureColumns", "azureStates", "artifactLanguage", "card", "cardAt")
# `azurePlacement`'s recognised sub-keys. Only `areaPath` is required, and its absence is a
# REFUSAL rather than a default — the same argument `azureStates` already carries, applied to
# where a spec is born rather than what state it reads as. `open_azure_backend` is where that
# refusal lives; this tuple only says which sub-keys `load_config` keeps.
AZURE_PLACEMENT_KEYS = ("areaPath", "workItemType", "discoveryTag", "team",
                        "iterationPath", "boardColumn", "defaultSubject", "repository")
# `discoveryTag`'s default. Unlike `areaPath`, this name is the TOOL's, not the project's —
# so it defaults rather than refuses; configurable only to resolve a collision with a tag the project already uses.
AZ_DEFAULT_DISCOVERY_TAG = "quenching-spec"
DEFAULT_BACKEND = None
# These two default to `None` in `load_config`'s own return — never
# to the literal "develop"/"main" below. The base-inference chain
# (`infer_base_branch`) must tell "declared" from "not declared" to know whether it may
# skip `origin/HEAD`; baking the default into `load_config` would erase that distinction
# for every repo that never opted into the develop/main flow. Callers that need an actual
# branch name once a value IS missing — `cq specs release` among them — apply these two
# constants themselves, at the point of use.
DEFAULT_INTEGRATION_BRANCH = "develop"
DEFAULT_RELEASE_BRANCH = "main"
# `azureStates` has NO default, and that is the decision rather than an omission. GitHub's
# open/closed is universal, so `github` needs no such key; an Azure Boards state is defined
# by the project's PROCESS — Basic says To Do/Doing/Done, Agile says New/Active/Resolved/
# Closed, Scrum says New/…/Done/Removed, and a customised process says whatever it likes.
# Guessing would not fail loudly: it would read every archived spec as active in half the
# projects it ran against.

# `hooks` defaults to {} — an absent key declares no events, and that is the normal case.
# The shape is checked at the read: an event must map to a list of hook objects each
# carrying a non-empty string `command`; anything else is left out of the read. One filter
# rides on that shape check — `enabled: false` never leaves here, per extension-points.md —
# and `condition` is carried along untouched for the owning executor, never evaluated by the
# parser. `optional` is normalised to a boolean so a hook runner can report its failure policy
# without guessing what an absent field means.

# `profiles` has NO default — an absent key declares nothing, which install-profiles.md reads
# as all seven local fronts eligible for the root align, the ordinary case. Like `hooks`, its
# shape is checked at the read (`installed` must be a list of non-empty strings) and its content is
# carried as data: the align conductor owns the scope decision, not this loader.

# The backends that ship without ever having run against a real target. Empty now:
# `azure-boards` was the one name here, and the provar-e-posicionar-o-backend-azure-boards
# plan's §6 ran it end to end against a real Azure DevOps project (a real
# organization and team) — `new --subject`, every `section --write`, `record`, `task --check`, `status`,
# `show` and `promote --outcome done`, each compared against `files` for the same state and
# matching, plus the board's own column tracking the de-para through every transition. This
# tuple losing the name is what retires the caveat everywhere at once: the doctor finding
# and the write-time line (`announce_unproved`) both go quiet together.
UNPROVED_BACKENDS = ()

_UNPROVED_ANNOUNCED: set[str] = set()


def announce_unproved(name: str) -> None:
    """One line on stderr, once per process, before an unproved backend's first WRITE.

    THE DECISION IS "WARN, BUT NOT ON EVERY OPERATION". A line per operation is honest and
    becomes a per-call tax on the agent reading this CLI, paid forever for a fact that never
    changes between calls. Silence is not defensible either, because the unproved paths do
    not all fail the same way: a wrong `AZ_SPEC_TYPE` fails LOUDLY — `az` answers with an
    API error and the transport turns it into an exit-2 refusal — but a field that accepts a
    value and silently coerces it to another fails QUIETLY, and a write that fails halfway
    leaves work items behind on somebody's real board. So the line lands on the
    writes, where an unproved path can cost something that does not announce itself, and
    reads — the overwhelming majority of a build loop's calls — stay silent. The permanent,
    zero-noise half of the same answer is `sp-backend-unproved` in `doctor`.

    stderr and never stdout: every caller branches on the `--json` payload, and a warning
    printed into it would break the parse it is trying to inform."""
    if name not in UNPROVED_BACKENDS or name in _UNPROVED_ANNOUNCED:
        return
    _UNPROVED_ANNOUNCED.add(name)
    print(f"warning: backend '{name}' ships without an end-to-end run against a real "
          f"target — its writes have never seen a live response, so this one may fail, or "
          f"half-succeed and leave items behind. `tests/test_specs_backends.py`'s "
          f"`BackendEquivalence` proves the five primitives and their refusals for "
          f"`files`/`memory`; nothing proves this call.", file=sys.stderr)


def find_repo_root(specs_root: str) -> str:
    """Compatibility export for callers that still import the old specs boundary."""
    return _common_find_repo_root(specs_root)


def detect_provider(root: str) -> tuple[str | None, str | None]:
    """Compatibility export for callers that still patch the old specs boundary."""
    # Keep the injectable `_git` seam while the common loader owns all production reads.
    repo = find_repo_root(root)
    remote = _git(repo, "remote", "get-url", "origin").strip()
    if not remote:
        return _common_detect_provider(root)
    host = _remote_host(remote)
    if host == "github.com" or host.endswith(".github.com"):
        return "github", host
    if (host == "dev.azure.com" or host.endswith(".dev.azure.com")
            or host == "visualstudio.com" or host.endswith(".visualstudio.com")):
        return "azure-boards", host
    return None, host


def _read_backend(out: dict, obj: dict, values: dict, root: str) -> None:
    backend = obj.get("backend")
    # What the file DECLARES, apart from `out["backend"]`, which is inferred from the remote
    # whenever the file is silent: `doctor` reads this to tell a chosen store from a default.
    out["backendDeclared"] = isinstance(backend, str) and bool(backend.strip())
    if isinstance(backend, str) and backend.strip() and backend.strip() not in BACKENDS:
        out["unknownBackend"] = backend.strip()
    if isinstance(backend, str) and backend.strip() == "git":
        from quenching.specs.backends.git import branch_config   # deferred: git imports parse
        out["backend"] = "git"
        branch_values = branch_config(root)
        for key in BRANCH_CONFIG_KEYS:
            if key in branch_values:
                values[key] = branch_values[key]
        value = values.get("artifactLanguage")
        if isinstance(value, str) and value.strip():
            out["artifactLanguage"] = value.strip()
        # `card` is `{"provider": github|azure-boards|none, "at": "capture"}`; the bare string
        # (`"card": "github"`) and the sibling `cardAt` are read as the same declaration. An
        # absent provider is left out, so the card layer can default it from the remote.
        raw_card = values.get("card")
        card = dict(raw_card) if isinstance(raw_card, dict) else (
            {"provider": raw_card} if isinstance(raw_card, str) else {})
        if values.get("cardAt") and "at" not in card:
            card["at"] = values["cardAt"]
        out["card"] = {k: str(v).strip() for k, v in card.items()
                       if k in ("provider", "at") and isinstance(v, str) and v.strip()}


def _read_scalars(out: dict, values: dict) -> None:
    val = values.get("worktreeSetup")
    if isinstance(val, str) and val.strip():
        out["worktreeSetup"] = val.strip()
    paths = values.get("sharedPaths")
    if isinstance(paths, list):
        out["sharedPaths"] = [v.strip() for v in paths if isinstance(v, str) and v.strip()]


def _read_azure_states(out: dict, values: dict) -> None:
    # Both phases or neither. A half-declared mapping is worse than none: it would archive a
    # spec into a state the project has and then fail to recognise it on the way back.
    states = values.get("azureStates")
    if isinstance(states, dict):
        named = {p: str(states.get(p, "")).strip() for p in PHASES}
        if all(named.values()):
            out["azureStates"] = named


def _read_hooks(out: dict, values: dict) -> None:
    events = values.get("hooks")
    if isinstance(events, dict):
        parsed: dict[str, list[dict]] = {}
        for event, entries in events.items():
            if not isinstance(entries, list):
                continue
            kept: list[dict] = []
            for hook in entries:
                if not isinstance(hook, dict):
                    continue
                command = hook.get("command")
                if not (isinstance(command, str) and command.strip()):
                    continue
                if hook.get("enabled") is False:
                    # extension-points.md: filtered at the read, never announced.
                    continue
                row: dict = {"command": command.strip(), "optional": hook.get("optional") is True}
                for field, kind in (("optional", bool), ("condition", str), ("prompt", str)):
                    value = hook.get(field)
                    if isinstance(value, kind):
                        row[field] = value
                kept.append(row)
            if kept:
                parsed[event] = kept
        out["hooks"] = parsed


def _read_profiles(out: dict, values: dict) -> None:
    profiles = values.get("profiles")
    if isinstance(profiles, dict):
        installed = profiles.get("installed")
        if (isinstance(installed, list)
                and all(isinstance(front, str) and front.strip() for front in installed)):
            out["profiles"] = {"installed": [front.strip() for front in installed]}


def _read_placement(out: dict, values: dict) -> None:
    # `azurePlacement` describes the PROJECT, not the backend — `subjects` and `tagCatalog`
    # apply equally to `github`, so they are read here unconditionally, the same as
    # `azurePlacement` and `azureColumns` themselves; a repository on `files` or `github`
    # simply never has anything ask for them. Every sub-key is independently optional at this
    # layer — `areaPath`'s absence is a REFUSAL, but that refusal belongs to
    # `open_azure_backend`, which is the one caller in a position to say no spec was read or
    # written; `load_config` only ever reports.
    placement_raw = values.get("azurePlacement")
    if isinstance(placement_raw, dict):
        out["azurePlacement"] = {k: placement_raw[k].strip()
                                 for k in AZURE_PLACEMENT_KEYS
                                 if isinstance(placement_raw.get(k), str)
                                 and placement_raw[k].strip()}

    # A board-state → lane de-para, consulted by the backend and never derived by it. Any
    # subset is legal — `boardColumn` in `azurePlacement` is the declared fallback for a
    # state absent from this table, so the table itself carries no all-or-nothing rule.
    columns_raw = values.get("azureColumns")
    if isinstance(columns_raw, dict):
        out["azureColumns"] = {str(k): v.strip() for k, v in columns_raw.items()
                               if isinstance(k, str) and k.strip()
                               and isinstance(v, str) and v.strip()}


def _read_subjects(out: dict, values: dict) -> None:
    # One entry per subject a spec may be born under. `name` and `description` are required
    # for an entry to exist at all — a nameless or description-less subject cannot be
    # proposed to a human, which is the whole point of declaring one — `parent` (the Feature
    # id a human already created) and `tags` (fixed tags applied at creation) are optional.
    subjects_raw = values.get("subjects")
    if isinstance(subjects_raw, dict):
        subjects: dict[str, dict] = {}
        for key, val in subjects_raw.items():
            if not (isinstance(key, str) and key.strip() and isinstance(val, dict)):
                continue
            name = val.get("name")
            description = val.get("description")
            if not (isinstance(name, str) and name.strip()
                    and isinstance(description, str) and description.strip()):
                continue
            entry = {"name": name.strip(), "description": description.strip()}
            parent = val.get("parent")
            if isinstance(parent, int) and not isinstance(parent, bool):
                entry["parent"] = parent
            tags = val.get("tags")
            if isinstance(tags, list):
                entry["tags"] = [t.strip() for t in tags if isinstance(t, str) and t.strip()]
            subjects[key.strip()] = entry
        out["subjects"] = subjects


def _read_tag_catalog(out: dict, values: dict) -> None:
    # A catalogue of tag → description, read by an agent to PROPOSE a tag at creation time —
    # the description is prompt material, never documentation, which is why an empty one is
    # dropped rather than kept as a nameless tag nobody could ever choose correctly.
    catalog_raw = values.get("tagCatalog")
    if isinstance(catalog_raw, dict):
        out["tagCatalog"] = {tag.strip(): desc.strip() for tag, desc in catalog_raw.items()
                             if isinstance(tag, str) and tag.strip()
                             and isinstance(desc, str) and desc.strip()}


def _read_work_item_types(out: dict, values: dict) -> None:
    # The abstract key a spec's `workItemType:` and `--type` carry — `{description, azure,
    # github, default}`. `description` is prompt material exactly like a `tagCatalog` value,
    # so an entry without one cannot be proposed and is dropped at the read, same as there.
    # `azure`/`github` are each independently optional: an entry may name only one backend
    # without breaking the other. `default` marks the repo's fallback entry.
    types_raw = values.get("workItemTypes")
    if isinstance(types_raw, dict):
        types: dict[str, dict] = {}
        for key, val in types_raw.items():
            if not (isinstance(key, str) and key.strip() and isinstance(val, dict)):
                continue
            description = val.get("description")
            if not (isinstance(description, str) and description.strip()):
                continue
            entry = {"description": description.strip()}
            for backend_key in ("azure", "github"):
                name = val.get(backend_key)
                if isinstance(name, str) and name.strip():
                    entry[backend_key] = name.strip()
            if val.get("default") is True:
                entry["default"] = True
            types[key.strip()] = entry
        out["workItemTypes"] = types


def load_config(root: str, *, detect_provider_info: bool = True) -> dict:
    """`.agents/quenching.json` — the plugin's declared parameters, read as data and never
    as a refusal.

    THE FILE MOVED, AND THE MOVE IS THE POINT. It used to be `/.specs/config.json`, at the
    root of the specs workspace, holding one key. Two things broke that home: a repo whose
    backend is external may have no `/.specs/` folder at all, so a config that lives inside
    the workspace cannot say where the workspace is; and the config stopped being the specs
    front's alone. `.claude/` is the one directory every front already shares.

    A leftover `/.specs/config.json` comes back as `legacyPath` rather than being read. Merging
    the two silently would leave a repo with a config that half-works and no way to tell which
    file won; `doctor` names it instead.

    Absent file, absent key, malformed JSON: all yield the defaults, because a repo that
    declares nothing is the normal case and must cost nothing. Every way the file can be
    *wrong* — an unrecognised key, an unrecognised backend, unparseable JSON — comes back as
    a field rather than as a finding, so `doctor` decides what each is worth and every other
    caller is spared the question.

    Whether `worktreeSetup` actually resolves is deliberately NOT answered here: it is judged
    relative to the freshly created worktree, whose path this tool never learns.
    `/quenching-specs execute`'s inline isolation offer runs it there and reports the exit code."""
    envelope = load_envelope(root, detect_provider_info=detect_provider_info)
    path = envelope["path"]
    raw = envelope["data"]
    values: dict = {}
    for name in ("shared", "specs"):
        values.update(namespace(envelope, name))
    provider = envelope["provider"]
    provider_host = envelope["providerHost"]
    out = {"path": path, "present": envelope["present"], "unparseable": None,
           "configSource": envelope["source"], "configProviderRefusal": envelope["providerRefusal"],
           "unknownKeys": envelope["unknownKeys"],
           "unknownNamespaces": envelope["unknownNamespaces"],
           "migrationRefusal": envelope["migrationRefusal"],
           "backend": provider, "provider": provider, "backendDeclared": False,
           "unknownProvider": provider_host if provider is None else None,
           "unknownBackend": None,
           "worktreeSetup": None, "sharedPaths": [],
           "azureStates": None, "hooks": {}, "profiles": None,
           "azurePlacement": {}, "azureColumns": {}, "subjects": {}, "tagCatalog": {},
           "workItemTypes": {},
           "legacyPath": envelope["legacyPath"]}
    if envelope["unparseable"]:
        out["unparseable"] = envelope["unparseable"]
        return out
    if not out["present"]:
        return out
    from quenching.specs.store import declared_store   # store imports backends, which import this
    store, refusal = declared_store(root)
    if store:
        out["store"] = store
    if refusal:
        out["storeRefusal"] = refusal
    obj = raw
    _read_backend(out, obj, values, root)
    _read_scalars(out, values)
    _read_azure_states(out, values)
    _read_hooks(out, values)
    _read_profiles(out, values)
    _read_placement(out, values)
    _read_subjects(out, values)
    _read_tag_catalog(out, values)
    _read_work_item_types(out, values)
    return out


def resolve_subject(cfg: dict, key: str | None) -> tuple[dict | None, dict]:
    """`subjects.<key>` — explicit or `azurePlacement.defaultSubject` — or the refusal
    `## Open Decisions` names for each way it can fail. Generic over every backend: `subjects`
    describes the PROJECT, not `azure-boards` alone (`plugin-configuration.md` §The recognised
    keys), so `github`'s `create_spec` calls this too, for the fixed tags a subject carries
    even where there is no parent to apply them alongside.

    `subjects` undeclared is NOT a refusal WHEN NOTHING WAS ASKED FOR — the feature is simply
    not in use, the same `## Absence is the normal case` every other key in this file gets. An
    EXPLICIT `--subject` still refuses against nothing declared: a human typed a key on
    purpose, and silently ignoring it would build the spec they did not ask for. Declaring
    subjects at all is what makes an unresolved one — explicit or defaulted — a refusal
    instead: a spec born with no resolved subject fails the team's own checklist (no Parent),
    and `## Open Decisions` calls refusing the only honest end."""
    subjects = cfg.get("subjects") or {}
    if key is None and not subjects:
        return None, {}
    resolved_key = key or (cfg.get("azurePlacement") or {}).get("defaultSubject")
    if not resolved_key:
        return None, {
            "code": "sp-no-subject", "exit": 2,
            "message": f"subjects are declared in {CONFIG_FILE} but none resolved — pass "
                       f"`--subject <key>`, or declare `azurePlacement.defaultSubject`; "
                       f"declared: {', '.join(sorted(subjects))}",
        }
    if resolved_key not in subjects:
        return None, {
            "code": "sp-subject-unknown", "exit": 2, "subject": resolved_key,
            "message": f"subject '{resolved_key}' is not declared in {CONFIG_FILE}'s "
                       f"`subjects` — declared: {', '.join(sorted(subjects))}",
        }
    return subjects[resolved_key], {}


def resolve_work_item_type(cfg: dict, frontmatter_type: str | None) -> str:
    """The `azure-boards` create's `--type`, first link that answers: the frontmatter's own
    `workItemType:` key, the catalog's `default` entry, or `AZ_SPEC_TYPE` as the floor. Never
    `azurePlacement.workItemType` — that key is the same fact said worse (§Design), retired
    from this chain and read only by its own aposentada-key finding.

    An entry that exists but names no `azure` type is treated exactly like an unresolved
    key — a repository that only translated a key for `github` still reaches the floor,
    because `azure-boards`'s create REQUIRES a `--type` and has nowhere else to fall."""
    from quenching.specs.backends.azure import AZ_SPEC_TYPE   # deferred: see the module docstring
    types = cfg.get("workItemTypes") or {}
    entry = types.get(frontmatter_type) if frontmatter_type else None
    if entry and entry.get("azure"):
        return entry["azure"]
    for candidate in types.values():
        if candidate.get("default") and candidate.get("azure"):
            return candidate["azure"]
    return AZ_SPEC_TYPE


def resolve_type_key(cfg: dict, key: str | None) -> tuple[str | None, dict]:
    """`--type <key>` against `workItemTypes` — the same two refusals `resolve_subject`
    already carries for `--subject`: an explicit key against an entry the catalog does not
    have, declared or not. Omitted is never a refusal — a spec born with no `workItemType:`
    still resolves one at build time, through `resolve_work_item_type`'s own default/floor
    chain, so there is no `defaultSubject`-shaped fallback to ask for here."""
    types = cfg.get("workItemTypes") or {}
    if key is None:
        return None, {}
    if key not in types:
        return None, {
            "code": "sp-type-unknown", "exit": 2, "type": key,
            "message": f"type '{key}' is not declared in {CONFIG_FILE}'s `workItemTypes`" + (
                f" — declared: {', '.join(sorted(types))}" if types
                else " — nothing is declared there"),
        }
    return key, {}


def azure_workitemtype_retirement(cfg: dict, frontmatter_type: str | None) -> dict | None:
    """`azurePlacement.workItemType` is retired from `resolve_work_item_type`'s chain but
    stays recognised in `AZURE_PLACEMENT_KEYS`, so a repository that declared it gets a
    graduated signal rather than the generic unknown-key finding.

    Declared beside a catalog that already resolves a type, the old key is dead
    configuration — the same fact said better now lives in `workItemTypes`, and ignoring
    the old key changes nothing a create would write. Declared as the only entry that WOULD
    have answered, ignoring it changes the item a create writes, so this refuses instead of
    silently substituting `AZ_SPEC_TYPE` for what a human actually declared."""
    from quenching.specs.backends.azure import AZ_SPEC_TYPE   # deferred: see the module docstring
    old = (cfg.get("azurePlacement") or {}).get("workItemType")
    if not old:
        return None
    resolved = resolve_work_item_type(cfg, frontmatter_type)
    types = cfg.get("workItemTypes") or {}
    entry = types.get(frontmatter_type) if frontmatter_type else None
    answered_by_catalog = bool(entry and entry.get("azure")) or any(
        c.get("default") and c.get("azure") for c in types.values())
    if answered_by_catalog:
        return {
            "code": "sp-az-workitemtype-retired-unused", "severity": "warn", "exit": 0,
            "message": f"{CONFIG_FILE}'s `azurePlacement.workItemType` ('{old}') is retired "
                       f"and unread — `workItemTypes` already resolves '{resolved}' for this "
                       f"create; dead configuration, changing nothing",
        }
    return {
        "code": "sp-az-workitemtype-only-answer", "severity": "error", "exit": 2,
        "message": f"{CONFIG_FILE}'s `azurePlacement.workItemType` ('{old}') is retired and "
                   f"was the only declared answer for this create's type — migrate it into "
                   f"a `workItemTypes` entry (`default: true`, or matching `--type`); "
                   f"writing '{AZ_SPEC_TYPE}' in its place would not be what was declared",
    }
