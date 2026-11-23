"""Provider pull-request payloads reduced to one safe, human-facing identity.

Azure calls the REST endpoint ``url`` while GitHub's CLI calls the browser address ``url``.
Keeping that ambiguity in each command is how an ``/_apis/`` endpoint escaped into a report.
These adapters make the distinction once: ``webUrl`` is the link a human may open, ``apiUrl``
is the endpoint a tool may call, and ``id`` is the provider's PR identity.
"""
from __future__ import annotations

from collections.abc import Mapping


def _first(payload: Mapping, *keys: str):
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _api_url(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _web_url(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    return None if "/_apis/" in value or "api.github.com/" in value else value


def normalize_azure_pull_request(payload: Mapping) -> dict:
    """Normalize one ``az repos pr`` response.

    Azure's ``url`` is an API endpoint. When the repository object is present, the browser
    address is deterministic and must be built from its ``webUrl`` plus ``pullrequest/<id>``;
    the API field is never reused as a human link. A pre-existing non-API browser URL is only a
    fallback for older/provider-shaped fixtures that omit the repository object.
    """
    repository = payload.get("repository")
    repository = repository if isinstance(repository, Mapping) else {}
    ident = _first(payload, "pullRequestId", "pullRequestID", "id")
    api = _api_url(_first(payload, "apiUrl", "api_url", "url"))
    repository_web = _web_url(_first(repository, "webUrl", "web_url"))
    web = (f"{repository_web.rstrip('/')}/pullrequest/{ident}"
           if repository_web and ident is not None else
           _web_url(_first(payload, "webUrl", "web_url")))
    return {"id": ident, "webUrl": web, "apiUrl": api}


def _github_repository(repository: Mapping | None) -> tuple[str | None, str | None]:
    if not isinstance(repository, Mapping):
        return None, None
    owner = repository.get("owner")
    if isinstance(owner, Mapping):
        owner = _first(owner, "login", "name")
    name = _first(repository, "name")
    return (str(owner).strip() if owner else None,
            str(name).strip() if name else None)


def normalize_github_pull_request(payload: Mapping,
                                  repository: Mapping | None = None) -> dict:
    """Normalize a GitHub PR object or ``gh pr view --json`` response.

    GitHub REST-shaped payloads expose ``html_url`` and ``url``; the CLI-shaped payload exposes
    ``url`` as the browser link. Accept both without treating an API-looking value as a human
    URL. If the repository identity is available, derive the REST endpoint only when the
    provider did not return one.
    """
    ident = _first(payload, "number", "pullRequestId", "id")
    candidate = _first(payload, "apiUrl", "api_url")
    raw_url = _first(payload, "url")
    api = _api_url(candidate) or (_api_url(raw_url) if isinstance(raw_url, str) and
                                  ("api.github.com/" in raw_url or "/_apis/" in raw_url)
                                  else None)
    web = _web_url(_first(payload, "webUrl", "web_url", "html_url"))
    if web is None and isinstance(raw_url, str) and "/_apis/" not in raw_url \
            and "api.github.com/" not in raw_url:
        web = _web_url(raw_url)
    owner, name = _github_repository(repository or payload.get("repository"))
    if api is None and owner and name and ident is not None:
        api = f"https://api.github.com/repos/{owner}/{name}/pulls/{ident}"
    return {"id": ident, "webUrl": web, "apiUrl": api}


def normalize_pull_request(provider: str, payload: Mapping,
                           repository: Mapping | None = None) -> dict:
    """Dispatch one provider response to the common PR identity model."""
    if provider == "azure-boards":
        return normalize_azure_pull_request(payload)
    if provider == "github":
        return normalize_github_pull_request(payload, repository)
    raise ValueError(f"unsupported pull-request provider: {provider}")


def review_link(snapshot: Mapping) -> str | None:
    """Return only a safe browser link for the report's ``Link para revisão`` label."""
    return _web_url(snapshot.get("webUrl"))


def labelled_links(snapshot: Mapping) -> dict:
    """The two explicit report labels, preserving a missing link as ``None``."""
    return {"Link para revisão": review_link(snapshot), "API URL": snapshot.get("apiUrl")}
