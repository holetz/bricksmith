"""`config` and `doctor` — the repository's declared parameters and provider health.

The two verbs that ask about the WORKSPACE rather than about a spec, which is why `config`
never opens a backend and `doctor` opens one only where the configured backend holds a finding
nothing else can ever catch: `azure-boards` for a card a human untagged, `github` for a listing
that came back empty. Every such check is a `warn` at worst — `doctor`'s contract is to reach
the end and report, never to refuse."""
from __future__ import annotations

import json
from quenching.common.config import repository_document
from quenching.config.schema import unknown_leaves
from quenching.specs.backends.azure import open_azure_backend
from quenching.specs.backends.base import BackendRefusal
from quenching.specs.backends.github import (GH_LISTING_SUSPECT_REMEDY, listing_is_suspect,
                                             listing_suspect_message, open_github_backend)
from quenching.specs.commands.output import Emitter, front_fields
from quenching.specs.commands.validate import _finding
from quenching.specs.config import (BACKENDS, CONFIG_FILE, CONFIG_KEYS,
                                    LEGACY_CONFIG_FILE,
                                    UNPROVED_BACKENDS, azure_workitemtype_retirement,
                                    load_config)


def cmd_config(args, root: str, out: Emitter) -> int:
    """The workspace's declared parameters, as data. Exit 0 even with nothing declared —
    a missing config is the normal case, and `doctor` is where a malformed one is judged."""
    cfg = load_config(root)
    lines = [f"quenching config — {cfg['path']}",
             f"  backend: {cfg['backend']}"
             + (" (default)" if not cfg["present"] else ""),
             "  worktreeSetup: " + (cfg["worktreeSetup"] or "(none declared)"),
             "  sharedPaths: " + (", ".join(cfg["sharedPaths"]) or "(none declared)"),
             "  azureStates: " + (", ".join(f"{p}={s}" for p, s in cfg["azureStates"].items())
                                  if cfg["azureStates"] else "(none declared)"),
             "  hooks: " + (", ".join(f"{event}: {len(entries)}" for event, entries in cfg["hooks"].items())
                            if cfg["hooks"] else "(none declared)"),
             "  profiles: " + (", ".join(cfg["profiles"]["installed"])
                               if cfg["profiles"] else "(none declared)")]
    if cfg["legacyPath"]:
        lines.append(f"  legacy config still on disk, unread: {cfg['legacyPath']}")
    out.emit(args.json, {"ok": True, **front_fields(root), **cfg}, "\n".join(lines))
    return 0


def cmd_doctor(args, root: str, out: Emitter) -> int:
    findings: list[dict] = []
    # Configuration is the only local state this diagnostic owns. The specs front lives in
    # the provider, so there is no local phase tree, root, lock or migration shape to inspect.
    # The real failure mode of a machine-read config is `worktree_setup` written where
    # `worktreeSetup` was expected, followed by silence — the file is valid JSON, the key
    # is simply never looked at, and the setup that was declared never runs. Both findings
    # exist so that silence cannot happen; neither is an error, because a workspace with a
    # malformed config is still a workspace and every other command still works.
    cfg = load_config(root)
    if cfg["unparseable"]:
        findings.append(_finding("sp-config-unparseable", "warn",
                                 f"{CONFIG_FILE} is not valid JSON: {cfg['unparseable']}",
                                 path=CONFIG_FILE,
                                 remedy=f"fix the JSON, or remove {CONFIG_FILE} — an absent "
                                        f"config declares nothing and is not a finding"))
    for key in cfg["unknownKeys"]:
        findings.append(_finding("sp-config-unknown-key", "warn",
                                 f"{CONFIG_FILE} declares `{key}`, which nothing reads",
                                 path=CONFIG_FILE, key=key,
                                 remedy=f"the recognised key(s): {', '.join(CONFIG_KEYS)}"))
    document = repository_document(root)[0]
    for key in unknown_leaves(document) if isinstance(document, dict) else []:
        findings.append(_finding("sp-config-unknown-leaf", "warn",
                                 f"{CONFIG_FILE} declares `{key}`, which nothing reads — "
                                 f"its default applies",
                                 path=CONFIG_FILE, key=key,
                                 remedy="compare it with the keys `cq config schema` lists, "
                                        "or remove it"))
    if cfg["unknownBackend"]:
        findings.append(_finding("sp-config-unknown-backend", "warn",
                                 f"{CONFIG_FILE} declares backend `{cfg['unknownBackend']}`, "
                                 f"which is not supported and will be refused — no backend "
                                 f"is in effect",
                                 path=CONFIG_FILE, backend=cfg["unknownBackend"],
                                 remedy=f"the implemented backend(s): {', '.join(BACKENDS)}"))
    # The permanent half of the "warn or stay silent" answer, and the reason it is a finding
    # and not a line on every call: a backend that was never run against a real target is a
    # fact about the CONFIGURATION, unchanged between operations, so it belongs where a
    # human goes to ask what is wrong with this workspace rather than in the output of every
    # command. The write-time line in `announce_unproved` is the other half. A warning on
    # every operation would be noise nobody reads twice; silence would let the untested
    # guesses (AZ_SPEC_TYPE, the state mapping) surface only when they are
    # already wrong in a real project. `doctor` is the middle the Open Decision asked for.
    if cfg["backend"] in UNPROVED_BACKENDS:
        findings.append(_finding("sp-backend-unproved", "warn",
                                 f"{CONFIG_FILE} declares backend `{cfg['backend']}`, which "
                                 f"ships without ever having been run against a real target "
                                 f"— its writes are unproved",
                                 path=CONFIG_FILE, backend=cfg["backend"],
                                 remedy="verify AZ_SPEC_TYPE matches this "
                                        "project's process template before relying on it, "
                                        "or declare a proven backend: "
                                        f"{', '.join(b for b in BACKENDS if b not in UNPROVED_BACKENDS)}"))
    # The tracker backends keep working, and their removal is a future major. Said here, where
    # a human asks what is wrong with the workspace, rather than on every command.
    if cfg["backend"] in ("github", "azure-boards"):
        # The backend is INFERRED from the remote whenever the config does not declare it — no
        # file, or a file without the `backend` key: the shapes where a project lands on the
        # deprecated store without ever choosing it.
        inferred = not cfg.get("backendDeclared", False)
        origin = " — inferred from the git remote, since no config declares it" if inferred else ""
        remedy = ("run `cq specs migrate --to git` to preview the move, then `--write`, then "
                  "set `backend: git` as it says")
        if inferred:
            remedy = ("run `cq specs migrate --to git` to preview the move, then `--write`; "
                      f"either way, declare `backend: git` in {CONFIG_FILE} so the store is "
                      "chosen, not inferred")
        findings.append(_finding("sp-backend-deprecated", "warn",
                                 f"backend `{cfg['backend']}`{origin} is deprecated: the spec "
                                 "lives in the tracker item, which costs a request per edit; "
                                 "the `git` store keeps it on branch `quenching` behind a "
                                 "thin card",
                                 path=CONFIG_FILE, backend=cfg["backend"], remedy=remedy))
    # The config moved to `.claude/`, and a repo that upgrades without moving its file is the
    # one shape where every command keeps working while nothing it declared is read — the
    # silence the two findings above exist to prevent, reappearing one directory over. Named
    # here rather than merged in load_config: two configs with no stated winner is worse than
    # one that is plainly stranded.
    if cfg["legacyPath"]:
        findings.append(_finding("sp-config-legacy-location", "warn",
                                 f"`/.specs/{LEGACY_CONFIG_FILE}` is still on disk and is no "
                                 f"longer read — the plugin's config is {CONFIG_FILE}",
                                 path=f".specs/{LEGACY_CONFIG_FILE}",
                                 remedy=f"move its keys into {CONFIG_FILE} and delete it; "
                                        f"whatever it declares is doing nothing today"))

    # A slot is reused, so `acquire` runs the setup again over a tree that already holds what
    # the last run installed. A setup that assumes a fresh tree breaks on the second acquire;
    # the target says once that it does not, and the warning stops.
    if cfg.get("worktreeSetup"):
        from quenching.git.slots import setup_idempotent
        from quenching.specs.config import find_repo_root

        if not setup_idempotent(find_repo_root(root) or root):
            findings.append(_finding(
                "sp-config-setup-idempotence", "warn",
                f"{CONFIG_FILE} declares `worktreeSetup`, which `cq git worktree acquire` runs "
                "on every acquire of a reused slot — it must be idempotent",
                path=CONFIG_FILE, command=cfg["worktreeSetup"],
                remedy="make the setup safe to run over a slot that already holds its "
                       "dependencies, then declare `shared.worktreeSlots.setupIdempotent: true`"))

    # The provider checks below reach the network — every other finding above is local by
    # construction. Justified because it is the only place that can ever catch it: a card a
    # human untagged is invisible to every OTHER command, which all read through the
    # tag-scoped listing. Runs only when `azure-boards` is actually configured, so a
    # repository without that provider never pays for it.
    if cfg["backend"] == "azure-boards":
        retirement = azure_workitemtype_retirement(cfg, None)
        if retirement:
            findings.append(_finding(retirement["code"], retirement["severity"],
                                     retirement["message"], path=CONFIG_FILE,
                                     remedy="migrate the type into a `workItemTypes` entry"))
        az, az_err = open_azure_backend(root)
        if not az_err:
            for row in az.marker_without_discovery_tag():
                findings.append(_finding("sp-az-marker-untagged", "warn",
                                         f"work item {row['id']} "
                                         f"carries the quenching-spec marker but not the "
                                         f"discovery tag '{az.discovery_tag}' — invisible to "
                                         f"every command's tag-scoped listing",
                                         path=str(row["id"]),
                                         remedy=f"re-apply the '{az.discovery_tag}' tag on "
                                                f"the board; until then this spec exists "
                                                f"only there"))
            # The three per-spec findings §2.13 adds — cost is the tag-scoped listing (the
            # specs this repository actually has), not the whole area the sweep above pays.
            for row in az.board_findings():
                if row["kind"] == "column":
                    findings.append(_finding("sp-az-column-drift", "warn",
                                             f"work item {row['id']} "
                                             f"is in column '{row['actual']}', not "
                                             f"'{row['expected']}' — the next write brings "
                                             f"it back",
                                             path=str(row["id"]),
                                             remedy="the board is the projection; move the "
                                                    "spec through its stage/records instead "
                                                    "of the card, or declare a different "
                                                    "azureColumns mapping"))
                elif row["kind"] == "tag":
                    findings.append(_finding("sp-az-tag-uncatalogued", "warn",
                                             f"work item {row['id']} "
                                             f"carries tag '{row['tag']}', which is not in "
                                             f"the declared `tagCatalog`",
                                             path=str(row["id"]),
                                             tag=row["tag"],
                                             remedy="add the tag to `tagCatalog` in "
                                                    f"{CONFIG_FILE}, or remove it from the "
                                                    f"work item"))
                elif row["kind"] == "dates":
                    findings.append(_finding("sp-az-dates-missing", "warn",
                                             f"work item {row['id']} "
                                             f"is past the captured stage with no `start`/"
                                             f"`target` — the team's own rule expects both "
                                             f"from Entendimento Técnico on",
                                             path=str(row["id"]),
                                             remedy="record `start`/`target` on the spec"))
    # The `github` counterpart of the network check above, and the same justification: a
    # listing that comes back empty is invisible to every OTHER command, which all read
    # through it and report the resulting nothing as the front's real state. This is where a
    # human already goes to ask what is wrong with the workspace, so it is where the standing
    # half of the answer belongs — the `stderr` line at the moment of the read is the other.
    #
    # NEITHER BRANCH REFUSES, which is the point: `doctor` has to reach the end. The exit-2
    # the same evidence produces at the read choke point becomes a `warn` here, quoting the
    # refusal's own message and remedy rather than composing a second wording for them.
    if cfg["backend"] == "github":
        gh, gh_err = open_github_backend(root)
        if not gh_err:
            try:
                rows = gh.list_specs()
            except BackendRefusal as refusal:
                # Every way the listing can fail lands here, not only the empty one — a 503
                # mid-diagnostic must not abort the diagnostic either. Only some refusals
                # carry a `remedy` of their own; the rest get the one true thing that can be
                # said, which is that nothing was read.
                findings.append(_finding(
                    refusal.err["code"], "warn", refusal.err["message"],
                    remedy=refusal.err.get("remedy")
                    or "the specs front could not be read, so nothing about it was measured"))
            else:
                if listing_is_suspect(len(rows), gh.open_issues):
                    findings.append(_finding("sp-gh-listing-suspect", "warn",
                                             listing_suspect_message(gh.repo, gh.open_issues),
                                             openIssues=gh.open_issues,
                                             remedy=GH_LISTING_SUSPECT_REMEDY))
    return _emit_doctor(args, root, findings)


def _emit_doctor(args, root: str, findings: list[dict]) -> int:
    errors = [f for f in findings if f["severity"] == "error"]
    if args.json:
        print(json.dumps({"ok": not errors, **front_fields(root), "findings": findings},
                         indent=2, ensure_ascii=False))
    else:
        print(f"specs doctor — {root} ({len(errors)} error(s), "
              f"{len(findings) - len(errors)} warning(s))")
        for f in findings:
            print(f"  [{f['severity']:<5}] {f['message']}  ({f['code']})")
            print(f"          remedy: {f['remedy']}")
        if not findings:
            print("  OK — workspace conforms.")
    return 1 if errors else 0
