"""The card layer — the thin tracker item (a GitHub issue or an Azure Boards work item) that
sits next to a spec stored on the `git` branch.

The spec document on branch `quenching` is the source of truth; the card is a projection a
human can read, label, rank on a board and discuss under. Three rules keep it cheap and honest:

- the card's NUMBER is the spec's ID, so the card is created first and the ID comes from the
  tracker (`plan/<id>-<handle>` and `Closes #<id>` keep working);
- it is written ONLY on a lifecycle transition — a record, a stage, a title, a tag, the
  archive hop — and never on a plain section edit or a task tick, which is why `lifecycle_key`
  is the whole decision and nothing else is diffed;
- one transition is one request, and a failed card write never undoes the spec write.

GitHub goes through `gh api`; Azure Boards through `az boards work-item create|update`, which
accept a PAT, never `az rest`, which only accepts an Entra token.
"""
from __future__ import annotations

import html
import json
import re
import sys

from quenching.specs.backends import azure as az_mod
from quenching.specs.backends import github as gh_mod
from quenching.specs.backends.base import BackendRefusal
from quenching.specs.parse import board_state_of, declared_tags, derive_labels, reconcile_label_set
from quenching.specs.parse.epics import presentation_level
from quenching.specs.parse.tasks import task_progress
from quenching.specs.parse.text import strip_comments


CARD_MARKER = "<!-- quenching-card -->"
CARD_PROVIDERS = ("github", "azure-boards", "none")
CARD_AT = ("capture",)
STORE_BRANCH = "quenching"
SUMMARY_MAX = 400


def card_refusal(spec_id, verb: str, cause: dict) -> BackendRefusal:
    """A card write that failed AFTER the spec was written: loud, and never a rollback."""
    return BackendRefusal({
        "code": "sp-card-failed", "exit": 2, "id": spec_id, "cause": cause.get("code"),
        "message": f"spec {spec_id} is written, but its card could not be {verb}: "
                   f"{cause.get('message', 'the tracker refused')} — the spec on branch "
                   f"'{STORE_BRANCH}' is the source of truth and was not rolled back; fix the "
                   "tracker access and make any lifecycle change again to refresh the card"})


# -- what a card says ----------------------------------------------------------------- #
def _tags(info: dict) -> list[str]:
    return declared_tags(list((info.get("frontmatter") or {}).get("tags") or []))


def summary_of(info: dict) -> str:
    """The `summary:` field, else the first paragraph of `## Problem`, cut to a card's size."""
    fm = info.get("frontmatter") or {}
    text = str(fm.get("summary") or "").strip()
    if not text:
        body = strip_comments(((info.get("sections") or {}).get("Problem") or {}).get("body", ""))
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        text = " ".join(paras[0].split()) if paras else ""
    if len(text) > SUMMARY_MAX:
        text = text[:SUMMARY_MAX - 1].rstrip() + "…"
    return text


def lifecycle_key(info: dict, members: list[dict] | None = None) -> tuple:
    """The only facts that make a card write necessary. Task progress, the summary and every
    section body are deliberately absent: they ride along when a transition writes the card
    and never cause a write of their own.

    A composite adds its member states: when a member starts, merges or finishes, the
    composite's card moves with it. `members` is the card layer's derived list; None is the
    plain document answer, which is what every non-composite caller passes."""
    fm = info.get("frontmatter") or {}
    return (str(fm.get("title") or ""), board_state_of(info, members), info["phase"],
            tuple(sorted(derive_labels(info))), tuple(sorted(_tags(info))),
            presentation_level(info),
            tuple((str(m.get("id")), str(m.get("status"))) for m in (members or [])))


def card_state(info: dict, members: list[dict] | None = None) -> dict:
    checked, _, total = task_progress(info.get("tasks") or [])
    fm = info.get("frontmatter") or {}
    rows = list(members or [])
    return {"id": info.get("id"), "title": str(fm.get("title") or ""),
            "summary": summary_of(info), "checked": checked, "total": total,
            "phase": info["phase"], "stage": info.get("stage") or "",
            "board": board_state_of(info, rows), "closed": info["phase"] == "archive",
            "labels": reconcile_label_set(_tags(info), derive_labels(info)),
            "typeKey": fm.get("workItemType"),
            "level": presentation_level(info), "members": rows,
            "specs": {"done": sum(1 for m in rows if m.get("status") == "done"),
                      "total": len(rows)}}


def spec_path(phase: str, spec_id) -> str:
    return f"specs/{'archive/' if phase == 'archive' else ''}{spec_id}.md" \
        if spec_id else f"specs/{'archive/' if phase == 'archive' else ''}<id>.md"


def render_card_body(state: dict, link: str | None, link_text: str, fmt: str = "markdown") -> str:
    """The thin body: marker, title, summary, progress, phase/stage and where the spec lives.

    A composite adds its presentation level and one line per member: the board reads the
    level through the label, and the human reads the membership here."""
    progress = f"Tasks {state['checked']}/{state['total']}" if state["total"] else "Tasks 0/0"
    members = state.get("members") or []
    level = str(state.get("level") or "story")
    if members:
        specs = state.get("specs") or {"done": 0, "total": len(members)}
        progress += f" · Specs {specs['done']}/{specs['total']}"
    lines = [f"Spec: {state['title']}"]
    if state["summary"]:
        lines += ["", state["summary"]]
    lines += ["", f"{progress} · phase {state['phase']} · stage {state['stage']}"]
    if level in ("feature", "epic"):
        lines += ["", f"Level: {level.title()}"]
        lines += [f"- {m.get('label') or ''} spec {m.get('id')} — {m.get('status')}: "
                  f"{m.get('title') or ''}".rstrip() for m in members]
    if fmt == "html":
        paras = [html.escape(p) for p in "\n".join(lines).split("\n\n")]
        where = html.escape(link_text)
        paras.append(f"File: {where}")
        paras.append("This card is a thin projection written at lifecycle transitions; the "
                     "spec on the git branch is the source of truth.")
        return f"<div style=\"display:none;\">quenching-card </div>\n" + \
            "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paras)
    where = f"[{link_text}]({link})" if link else link_text
    return (CARD_MARKER + "\n" + "\n".join(lines) + f"\n\nFile: {where}\n\n"
            f"_A thin card written by quenching at lifecycle transitions. The spec on branch "
            f"`{STORE_BRANCH}` is the source of truth; discuss here, edit the spec there._\n")


# -- clients ----------------------------------------------------------------------------- #
class CardClient:
    provider = "none"

    def create(self, state: dict) -> int:                    # pragma: no cover - interface
        raise NotImplementedError

    def update(self, number: int, state: dict) -> None:      # pragma: no cover - interface
        raise NotImplementedError

    def read(self, number: int) -> dict:                     # pragma: no cover - interface
        """`{title, body}` of an existing card, refusing anything that is not a plain item."""
        raise NotImplementedError


class GitHubCard(CardClient):
    provider = "github"

    def __init__(self, repo: str, cwd: str) -> None:
        self.repo = repo
        self.cwd = cwd

    def _link(self, state: dict, number: int | None) -> tuple[str, str]:
        base = f"https://github.com/{self.repo}"
        if number:
            path = spec_path(state["phase"], number)
            return f"{base}/blob/{STORE_BRANCH}/{path}", path
        return f"{base}/tree/{STORE_BRANCH}/specs", "specs/<this card's number>.md"

    def _api(self, action: str, *argv: str, stdin: str | None = None):
        code, out, err, attempts = gh_mod._gh_result(gh_mod._gh_run(self.cwd, "api", *argv,
                                                                    stdin=stdin))
        if code != 0:
            raise BackendRefusal(gh_mod.gh_refusal(action, code, out, err, attempts))
        try:
            return json.loads(out or "null")
        except json.JSONDecodeError as exc:
            raise BackendRefusal({"code": "sp-gh-bad-response", "exit": 2, "action": action,
                                  "message": f"`gh api` exited 0 while {action} but its "
                                             f"output is not JSON: {exc}"}) from exc

    def create(self, state: dict) -> int:
        link, text = self._link(state, None)
        payload = {"title": state["title"], "body": render_card_body(state, link, text)}
        if state["labels"]:
            payload["labels"] = state["labels"]
        issue = self._api("creating the card", "-X", "POST", f"repos/{self.repo}/issues",
                          "--input", "-", stdin=json.dumps(payload))
        number = int((issue or {}).get("number") or 0)
        if not number:
            raise BackendRefusal({"code": "sp-gh-bad-response", "exit": 2,
                                  "message": "GitHub created the card but returned no number"})
        return number

    def update(self, number: int, state: dict) -> None:
        link, text = self._link(state, number)
        payload = {"title": state["title"], "body": render_card_body(state, link, text),
                   "labels": state["labels"], "state": "closed" if state["closed"] else "open"}
        self._api(f"updating card #{number}", "-X", "PATCH",
                  f"repos/{self.repo}/issues/{number}", "--input", "-", stdin=json.dumps(payload))

    def read(self, number: int) -> dict:
        issue = self._api(f"reading issue #{number}", f"repos/{self.repo}/issues/{number}")
        if not isinstance(issue, dict) or "pull_request" in issue:
            raise BackendRefusal({"code": "sp-card-not-an-issue", "exit": 2,
                                  "message": f"#{number} is a pull request, not an issue — a "
                                             "spec can only adopt an issue"})
        return {"title": str(issue.get("title") or ""), "body": str(issue.get("body") or ""),
                "closed": issue.get("state") == "closed"}


class AzureCard(CardClient):
    """Work items through `az boards work-item create|update` — PAT-compatible, one process
    per transition. Placement rides the create and the transitions, never anything else."""
    provider = "azure-boards"

    def __init__(self, org: str, project: str, cwd: str, placement: dict | None = None,
                 states: dict | None = None, columns: dict | None = None,
                 types: dict | None = None) -> None:
        self.org = org
        self.project = project
        self.cwd = cwd
        self.placement = placement or {}
        self.states = states or {}
        self.columns = columns or {}
        self.types = types or {}
        self._boards = None

    def _az(self, action: str, *argv: str):
        code, out, err = az_mod._az_run(self.cwd, "boards", *argv, "--org", self.org,
                                        "--output", "json")
        if code != 0:
            raise BackendRefusal(az_mod.az_refusal(action, code, out, err))
        try:
            return json.loads(out or "null")
        except json.JSONDecodeError as exc:
            raise BackendRefusal({"code": "sp-az-bad-response", "exit": 2, "action": action,
                                  "message": f"`az` exited 0 while {action} but its output is "
                                             f"not JSON: {exc}"}) from exc

    def _type_name(self, state: dict) -> str:
        from quenching.specs.config import resolve_work_item_type
        return resolve_work_item_type({"workItemTypes": self.types}, state.get("typeKey")) \
            if self.types else (self.placement.get("workItemType") or az_mod.AZ_SPEC_TYPE)

    def _column_field(self, state: dict) -> tuple[str, str] | None:
        column = self.columns.get(state["board"], self.placement.get("boardColumn"))
        if not column:
            return None
        if self._boards is None:
            self._boards = az_mod.AzureBoardsBackend(
                self.org, self.project, self.states, self.cwd, team=self.placement.get("team"),
                types=self.types)
        return self._boards._resolve_board_field(self._type_name(state)), column

    def _fields(self, state: dict) -> list[str]:
        fields = []
        if state["labels"]:
            fields.append("System.Tags=" + "; ".join(sorted(state["labels"])))
        col = self._column_field(state)
        if col:
            fields.append(f"{col[0]}={col[1]}")
        return fields

    def _placement(self) -> list[str]:
        argv = []
        if self.placement.get("areaPath"):
            argv += ["--area", self.placement["areaPath"]]
        if self.placement.get("iterationPath"):
            argv += ["--iteration", self.placement["iterationPath"]]
        return argv

    def _body(self, state: dict, number: int | None) -> str:
        path = spec_path(state["phase"], number) if number else "specs/<this item's id>.md"
        return render_card_body(state, None, f"branch {STORE_BRANCH}, {path}", fmt="html")

    def _relations(self, number: int) -> list[dict]:
        """The work item's relations, read only when the state carries members."""
        item = self._az(f"reading the relations of work item {number}", "work-item", "show",
                        "--id", str(number), "--expand", "relations")
        return list((item or {}).get("relations") or [])

    def _sync_member_links(self, number: int, members: list[dict]) -> None:
        """Add one predecessor link per member that has none.

        `System.LinkTypes.Dependency-Reverse` is the relation `dependsOn` already uses and the
        one Azure accepts between any two work-item types. A link is added once and never
        removed: a member taken out of the document leaves a harmless stale link, and the
        alternative — treating every predecessor as composition — would delete a dependency
        the store wrote. No parent-child relation is ever emitted."""
        wanted = [int(m["id"]) for m in members if str(m.get("id", "")).isdigit()]
        if not wanted:
            return
        linked = az_mod.azure_predecessor_links(self._relations(number))
        for mid in wanted:
            if mid in linked:
                continue
            self._az(f"linking work item {number} to {mid}", "work-item", "relation", "add",
                     "--id", str(number),
                     "--relation-type", az_mod.AZ_PREDECESSOR_REL,
                     "--target-id", str(mid))

    def create(self, state: dict) -> int:
        argv = ["work-item", "create", "--project", self.project,
                "--type", self._type_name(state), "--title", state["title"],
                "--description", self._body(state, None), *self._placement()]
        fields = self._fields(state)
        if fields:
            argv += ["--fields", *fields]
        item = self._az("creating the card", *argv)
        number = int((item or {}).get("id") or 0)
        if not number:
            raise BackendRefusal({"code": "sp-az-bad-response", "exit": 2,
                                  "message": "Azure created the work item but returned no id"})
        if state.get("members"):
            self._sync_member_links(number, state["members"])
        return number

    def update(self, number: int, state: dict) -> None:
        argv = ["work-item", "update", "--id", str(number), "--title", state["title"],
                "--description", self._body(state, number), *self._placement()]
        if not self._column_field(state) and self.states.get(state["phase"]):
            argv += ["--state", self.states[state["phase"]]]
        fields = self._fields(state)
        if fields:
            argv += ["--fields", *fields]
        self._az(f"updating work item {number}", *argv)
        if state.get("members"):
            self._sync_member_links(number, state["members"])

    def read(self, number: int) -> dict:
        item = self._az(f"reading work item {number}", "work-item", "show", "--id", str(number))
        fields = (item or {}).get("fields") or {}
        if not fields:
            raise BackendRefusal({"code": "sp-card-not-found", "exit": 2,
                                  "message": f"work item {number} was not found"})
        body = re.sub(r"<br\s*/?>|</p>|</div>", "\n", str(fields.get("System.Description") or ""))
        return {"title": str(fields.get("System.Title") or ""),
                "body": html.unescape(re.sub(r"<[^>]+>", "", body)).strip(), "closed": False}


# -- resolution --------------------------------------------------------------------------- #
def card_settings(cfg: dict, root: str) -> dict:
    """`{"provider", "at"}`: the declared card, else `github` for a GitHub remote, else none."""
    card = cfg.get("card") if isinstance(cfg.get("card"), dict) else {}
    provider = card.get("provider")
    if provider not in CARD_PROVIDERS:
        from quenching.specs.config import detect_provider
        provider = "github" if detect_provider(root)[0] == "github" else "none"
    return {"provider": provider, "at": card.get("at") or "capture"}


def resolve_card_client(root: str) -> CardClient | None:
    """The client for this repository's configured card provider, or None for `none`."""
    from quenching.common.config import find_repo_root
    from quenching.specs.config import load_config
    cfg = load_config(root)
    provider = card_settings(cfg, root)["provider"]
    cwd = find_repo_root(root)
    if provider == "github":
        repo, _, err = gh_mod.resolve_github_repo(cwd)
        if err:
            raise BackendRefusal(err)
        return GitHubCard(repo, cwd)
    if provider == "azure-boards":
        (org, project), err = az_mod.resolve_azure_project(cwd)
        if err:
            raise BackendRefusal(err)
        return AzureCard(org, project, cwd, placement=cfg.get("azurePlacement"),
                         states=cfg.get("azureStates"), columns=cfg.get("azureColumns"),
                         types=cfg.get("workItemTypes"))
    return None


def client_for_backend(source, source_namespace: dict | None = None) -> CardClient:
    """The client that thins the items a TRACKER backend currently holds as whole specs —
    built from the open backend itself, so `migrate` talks to the tracker it just read."""
    if source.name == "github":
        return GitHubCard(source.repo, source.cwd)
    if source.name == "azure-boards":
        ns = source_namespace or {}
        return AzureCard(source.org, source.project, source.cwd,
                         placement=ns.get("azurePlacement") or {
                             "areaPath": source.area_path, "iterationPath": source.iteration_path,
                             "team": source.team, "boardColumn": source.board_column},
                         states=source.states, columns=source.column_map, types=source.types)
    raise BackendRefusal({"code": "sp-card-unsupported", "exit": 2,
                          "message": f"backend '{source.name}' has no tracker items to thin"})
