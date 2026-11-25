"""Read-only pre-publication readiness: what would leak, or lack consent, if this repository went public.

Nothing here writes to the target, fetches, changes a ref or runs a target script. Scanners are
existing installed tools invoked offline, with a disposable report directory outside the target;
their output is untrusted input read through an allowlist, and nothing they print reaches the
payload. A category that could not be measured stays ``not-measured`` — never a clean answer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

CLASSES = ("secrets", "internal-identifiers", "owner-terms", "dependency-license", "attribution",
           "large-generated", "history-consent", "provenance")
MEASURED, PARTIAL, NOT_MEASURED, REFUSED = "measured", "partial", "not-measured", "refused"
DEFAULT_TIMEOUT_S = 300
GIT_TIMEOUT_S = 60
LARGE_BYTES = 5 * 1024 * 1024
FINDINGS_PER_CODE = 50

SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
TOKENISH = re.compile(r"[A-Za-z0-9]{20,}")
COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
NOREPLY = ("users.noreply.github.com", "noreply.gitlab.com", "noreply.github.com")
GENERATED_DIRS = {"node_modules", "dist", "build", "__pycache__", ".venv", "venv", "target", ".tox"}
GENERATED_SUFFIXES = (".min.js", ".pyc", ".pyo", ".class", ".o", ".so", ".dll", ".exe", ".jar", ".whl")
INTERNAL_PATTERNS = (
    r"(^|[^A-Za-z0-9.-])[A-Za-z0-9-]+\.(corp|internal|intranet|lan)([^A-Za-z0-9]|$)",
    r"(^|[^0-9.])10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}([^0-9]|$)",
    r"(^|[^0-9.])192\.168\.[0-9]{1,3}\.[0-9]{1,3}([^0-9]|$)",
    r"(^|[^0-9.])172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}([^0-9]|$)",
)
# A tenant inside a public SaaS host: the host is public, the organization in it is not.
TENANT_PATTERNS = (
    r"dev\.azure\.com/[A-Za-z0-9][A-Za-z0-9_.-]*",
    r"[A-Za-z0-9][A-Za-z0-9-]*\.(visualstudio|sharepoint|snowflakecomputing)\.com",
    r"[A-Za-z0-9][A-Za-z0-9-]*\.atlassian\.net",
    r"adb-[0-9]+\.[0-9]+\.azuredatabricks\.net",
)
TENANT = re.compile(r"dev\.azure\.com/([^/\s]+)|^([^.]+)\.", re.IGNORECASE)
# The vendor's own public subdomains and the documentation placeholders, which name no tenant.
PUBLIC_TENANTS = {"app", "aka", "docs", "learn", "login", "marketplace", "status", "support", "vssps", "www",
                  "o", "org", "myorg", "myorganization", "your-org", "yourorg", "example", "example-org",
                  "acme", "contoso", "fabrikam", "tenant"}
# The minimum version whose safe flags this adapter was written against.
SUPPORTED = {"gitleaks": (8, 19), "trufflehog": (3, 0), "reuse": (2, 0)}
REMEDIATION = {
    "pr-secret": "rotate the credential, then the owner decides whether to rewrite history",
    "pr-internal-identifier": "the owner reviews the reference and removes or generalizes it",
    "pr-owner-term": "the owner removes the term from the tree and decides whether to rewrite history",
    "pr-identity-in-metadata": "the owner confirms every author agrees to public commit metadata",
    "pr-large-file": "the owner decides whether the artifact belongs in the published history",
    "pr-generated-artifact": "the owner decides whether the generated artifact belongs in the repository",
    "pr-attribution-missing": "add copyright and license information to the files reuse names",
    "pr-license-text-missing": "add the referenced license texts under LICENSES/",
    "pr-license-incompatible": "the owner resolves the incompatible dependency license before publishing",
    "pr-consent-withheld": "the owner obtains consent or removes the contribution before publishing",
    "pr-provenance-derived": "the owner obtains a license or authorization from the origin project, or removes the derived work",
}


def _env() -> dict[str, str]:
    """Every git the engine or a scanner runs ignores the target's fsmonitor and hooks."""
    return dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_COUNT="2",
                GIT_CONFIG_KEY_0="core.fsmonitor", GIT_CONFIG_VALUE_0="false",
                GIT_CONFIG_KEY_1="core.hooksPath", GIT_CONFIG_VALUE_1=os.devnull)


def _run(argv: list[str], cwd: str, timeout: int) -> tuple[int, str, str] | None:
    """Exit, stdout, stderr — or ``None`` for a missing binary or a timeout. Never raises.

    The program runs from the path `which` resolves, the same lookup `_scanner` detected it with:
    on Windows a bare name only reaches a `.exe`, so a `.cmd` shim would be found and then fail."""
    argv = [shutil.which(argv[0]) or argv[0], *argv[1:]]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd,
                             timeout=timeout, env=_env(), stdin=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return out.returncode, out.stdout, out.stderr


def _git(root: str, *argv: str) -> tuple[int, str]:
    result = _run(["git", *argv], root, GIT_TIMEOUT_S)
    return (127, "") if result is None else (result[0], result[1])


def location(path: Any) -> str:
    """A relative path only when no segment could carry a value; an opaque id otherwise."""
    text = str(path)
    segments = text.split("/")
    if (isinstance(path, str) and text and not text.startswith("/") and ".." not in segments
            and all(SAFE_SEGMENT.match(s) for s in segments) and not TOKENISH.search(text)):
        return text
    return "opaque:" + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


def _finding(code: str, cls: str, severity: str, loc: str | None = None,
             commit: Any = None, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"code": code, "class": cls, "severity": severity,
                           "remediation": REMEDIATION[code], "owner": "repository owner"}
    if loc is not None:
        row["location"] = loc
    if isinstance(commit, str) and COMMIT.match(commit):
        row["commit"] = commit
    row.update(extra)
    return row


def _cap(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """At most FINDINGS_PER_CODE rows per code; the rest is a count."""
    kept, seen, dropped = [], {}, 0
    for row in rows:
        seen[row["code"]] = seen.get(row["code"], 0) + 1
        if seen[row["code"]] <= FINDINGS_PER_CODE:
            kept.append(row)
        else:
            dropped += 1
    return kept, dropped


def inventory(root: str) -> dict[str, Any]:
    """The exact assessed scope: local refs, history completeness, tracked/untracked/ignored."""
    code, inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if code != 0 or inside.strip() != "true":
        return {"git": False, "code": "pr-not-a-repository"}
    _, top = _git(root, "rev-parse", "--show-toplevel")
    if os.path.realpath(top.strip()) != os.path.realpath(root):
        return {"git": False, "code": "pr-not-repository-root"}
    head_code, _ = _git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    _, shallow = _git(root, "rev-parse", "--is-shallow-repository")
    _, refs_out = _git(root, "for-each-ref", "--format=%(refname)%00%(objectname)", "refs/heads", "refs/tags")
    refs = []
    for line in refs_out.splitlines():
        name, _, sha = line.partition("\0")
        refs.append({"ref": location(name), "commit": sha if COMMIT.match(sha) else None})
    connected = _git(root, "rev-list", "--objects", "--branches", "--tags", "--quiet")[0] == 0 if refs else True
    _, tracked = _git(root, "ls-files", "-z")
    _, untracked = _git(root, "ls-files", "-z", "--others", "--exclude-standard")
    _, ignored = _git(root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory")
    is_shallow = shallow.strip() == "true"
    return {
        "git": True,
        "hasCommits": head_code == 0,
        "refs": refs,
        "shallow": is_shallow,
        "historyComplete": not is_shallow and connected,
        "tracked": len([p for p in tracked.split("\0") if p]),
        "untracked": len([p for p in untracked.split("\0") if p]),
        "ignored": len([p for p in ignored.split("\0") if p]),
        "untrackedScanned": False,
        "trackedPaths": {p for p in tracked.split("\0") if p},
    }


def _version(name: str, argv: list[str], root: str) -> tuple[str | None, str | None]:
    """The scanner's version, or the fixed code that refuses it."""
    result = _run(argv, root, GIT_TIMEOUT_S)
    if result is None or result[0] != 0:
        return None, "pr-scanner-failed"
    match = VERSION.search(result[1] + "\n" + result[2])
    if not match:
        return None, "pr-scanner-version-unknown"
    found = tuple(int(part) for part in match.groups())
    want = SUPPORTED[name]
    # reuse's lint JSON is stable across majors; the secret scanners' flags are not.
    supported = found[0] >= want[0] if name == "reuse" else found[0] == want[0] and found[1] >= want[1]
    return ".".join(map(str, found)), None if supported else "pr-scanner-unsupported"


def _scratch(root: str) -> tempfile.TemporaryDirectory | None:
    scratch = tempfile.TemporaryDirectory(prefix="cq-readiness-")
    real = os.path.realpath(scratch.name)
    if os.path.commonpath([real, os.path.realpath(root)]) == os.path.realpath(root):
        scratch.cleanup()
        return None
    return scratch


def _gitleaks(root: str, timeout: int) -> tuple[list[dict[str, Any]] | None, str | None]:
    scratch = _scratch(root)
    if scratch is None:
        return None, "pr-scratch-inside-target"
    with scratch:
        report = os.path.join(scratch.name, "report.json")
        result = _run(["gitleaks", "git", root, "--log-opts=--branches --tags", "--report-format", "json",
                       "--report-path", report, "--redact", "--no-banner", "--exit-code", "0",
                       "--log-level", "error"], scratch.name, timeout)
        if result is None:
            return None, "pr-scanner-timeout"
        if result[0] != 0:
            return None, "pr-scanner-failed"
        try:
            rows = json.loads(Path(report).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, "pr-scanner-malformed"
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return None, "pr-scanner-malformed"
    return [_finding("pr-secret", "secrets", "blocker", location(r.get("File")), r.get("Commit"))
            for r in rows], None


def _trufflehog(root: str, timeout: int) -> tuple[list[dict[str, Any]] | None, str | None]:
    scratch = _scratch(root)
    if scratch is None:
        return None, "pr-scratch-inside-target"
    with scratch:
        result = _run(["trufflehog", "git", Path(root).as_uri(), "--json", "--no-verification", "--no-update"],
                      scratch.name, timeout)
    if result is None:
        return None, "pr-scanner-timeout"
    if result[0] != 0:
        return None, "pr-scanner-failed"
    rows = []
    for line in result[1].splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            git = row["SourceMetadata"]["Data"]["Git"]
        except (ValueError, KeyError, TypeError):
            return None, "pr-scanner-malformed"
        if not isinstance(git, dict):
            return None, "pr-scanner-malformed"
        rows.append(_finding("pr-secret", "secrets", "blocker", location(git.get("file")), git.get("commit")))
    return rows, None


def _reuse(root: str, inv: dict[str, Any], timeout: int) -> tuple[list[dict[str, Any]] | None, str | None]:
    scratch = _scratch(root)
    if scratch is None:
        return None, "pr-scratch-inside-target"
    with scratch:
        result = _run(["reuse", "--root", root, "lint", "--json"], scratch.name, timeout)
    if result is None:
        return None, "pr-scanner-timeout"
    if result[0] not in (0, 1):
        return None, "pr-scanner-failed"
    try:
        payload = json.loads(result[1])
        bad = payload["non_compliant"]
        missing = set(bad.get("missing_licensing_info") or []) | set(bad.get("missing_copyright_info") or [])
        licenses = bool(bad.get("missing_licenses")) or bool(bad.get("bad_licenses"))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None, "pr-scanner-malformed"
    rows = [_finding("pr-attribution-missing", "attribution", "blocker", location(path))
            for path in sorted(str(p) for p in missing) if path in inv["trackedPaths"]]
    if licenses:
        rows.append(_finding("pr-license-text-missing", "attribution", "blocker"))
    return rows, None


def _scanner(name: str, version_argv: list[str], root: str) -> dict[str, Any]:
    if shutil.which(name) is None:
        return {"name": name, "state": "absent", "version": None, "code": "pr-scanner-absent"}
    version, code = _version(name, version_argv, root)
    return {"name": name, "state": "refused" if code else "available", "version": version, "code": code}


def _secrets(root: str, inv: dict[str, Any], timeout: int, scanners: list[dict[str, Any]]):
    adapters = (("gitleaks", ["gitleaks", "version"], _gitleaks),
                ("trufflehog", ["trufflehog", "--version"], _trufflehog))
    for name, version_argv, run in adapters:
        row = _scanner(name, version_argv, root)
        scanners.append(row)
        if row["state"] != "available":
            continue
        found, code = run(root, timeout)
        if code:
            row.update(state="failed", code=code)
            continue
        row["state"] = "used"
        state = MEASURED if inv["historyComplete"] else PARTIAL
        return found, {"class": "secrets", "state": state,
                       "code": None if state == MEASURED else "pr-history-incomplete", "scanner": name}
    return [], {"class": "secrets", "state": NOT_MEASURED, "code": "pr-no-safe-scanner", "scanner": None}


def _attribution(root: str, inv: dict[str, Any], timeout: int, scanners: list[dict[str, Any]]):
    row = _scanner("reuse", ["reuse", "--version"], root)
    scanners.append(row)
    if row["state"] != "available":
        return [], {"class": "attribution", "state": NOT_MEASURED, "code": row["code"], "scanner": None}
    found, code = _reuse(root, inv, timeout)
    if code:
        row.update(state="failed", code=code)
        return [], {"class": "attribution", "state": NOT_MEASURED, "code": code, "scanner": None}
    row["state"] = "used"
    return found, {"class": "attribution", "state": MEASURED, "code": None, "scanner": "reuse"}


def _internal(root: str, inv: dict[str, Any]):
    argv = ["grep", "-I", "-l", "-E"]
    for pattern in INTERNAL_PATTERNS:
        argv += ["-e", pattern]
    code, out = _git(root, *argv, "HEAD")
    if code not in (0, 1):
        return [], {"class": "internal-identifiers", "state": NOT_MEASURED, "code": "pr-tree-unreadable"}
    paths = [line.split(":", 1)[1] if ":" in line else line for line in out.splitlines() if line]
    argv = ["grep", "-I", "-o", "-z", "-E"]
    for pattern in TENANT_PATTERNS:
        argv += ["-e", pattern]
    code, out = _git(root, *argv, "HEAD")
    if code not in (0, 1):
        return [], {"class": "internal-identifiers", "state": NOT_MEASURED, "code": "pr-tree-unreadable"}
    for line in out.splitlines():
        path, _, match = line.partition("\0")
        tenant = TENANT.match(match)
        if tenant and (tenant.group(1) or tenant.group(2)).rstrip(".").lower() not in PUBLIC_TENANTS:
            paths.append(path.split(":", 1)[1] if ":" in path else path)
    rows = [_finding("pr-internal-identifier", "internal-identifiers", "warning", location(path))
            for path in dict.fromkeys(paths)]
    code, log = _git(root, "log", "--branches", "--tags", "--format=%ae%n%ce")
    if code != 0:
        return rows, {"class": "internal-identifiers", "state": PARTIAL, "code": "pr-history-unreadable"}
    addresses = {a.strip().lower() for a in log.splitlines() if a.strip()}
    personal = [a for a in addresses if not a.endswith(NOREPLY) and a != "noreply@github.com"]
    if personal:
        rows.append(_finding("pr-identity-in-metadata", "internal-identifiers", "warning",
                             addresses=len(personal)))
    state = MEASURED if inv["historyComplete"] else PARTIAL
    return rows, {"class": "internal-identifiers", "state": state,
                  "code": None if state == MEASURED else "pr-history-incomplete"}


def load_denylist(root: str, path: str | None) -> tuple[list[str], str | None, str | None]:
    """The owner's terms, or the state and code that say why the class cannot be measured."""
    if path is None:
        return [], NOT_MEASURED, "pr-denylist-absent"
    real = os.path.realpath(path)
    if os.path.commonpath([real, root]) == root:
        return [], REFUSED, "pr-denylist-inside-target"
    try:
        lines = Path(real).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return [], NOT_MEASURED, "pr-denylist-unreadable"
    terms = list(dict.fromkeys(t.strip() for t in lines if t.strip() and not t.strip().startswith("#")))
    return (terms, None, None) if terms else ([], NOT_MEASURED, "pr-denylist-empty")


def _owner_terms(root: str, inv: dict[str, Any], denylist: str | None, timeout: int):
    """Owner-declared terms by whole word, any case, in HEAD and in the history's diffs and messages."""
    terms, state, code = load_denylist(root, denylist)
    if state:
        return [], {"class": "owner-terms", "state": state, "code": code}
    argv = ["grep", "-I", "-l", "-i", "-w", "-F"]
    for term in terms:
        argv += ["-e", term]
    code, out = _git(root, *argv, "HEAD")
    if code not in (0, 1):
        return [], {"class": "owner-terms", "state": NOT_MEASURED, "code": "pr-tree-unreadable"}
    rows = [_finding("pr-owner-term", "owner-terms", "blocker",
                     location(line.split(":", 1)[1] if ":" in line else line))
            for line in out.splitlines() if line]
    words = "|".join(re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", term) for term in terms)
    regex = f"(^|[^A-Za-z0-9_])({words})([^A-Za-z0-9_]|$)"
    commits: list[str] = []
    for pick in ("-G" + regex, "--grep=" + regex):
        result = _run(["git", "log", "--branches", "--tags", "--format=%H", "-i", "-E", pick], root, timeout)
        if result is None or result[0] != 0:
            return rows, {"class": "owner-terms", "state": PARTIAL, "code": "pr-history-unreadable"}
        commits += [sha for sha in result[1].split() if COMMIT.match(sha)]
    rows += [_finding("pr-owner-term", "owner-terms", "blocker", commit=sha) for sha in dict.fromkeys(commits)]
    state = MEASURED if inv["historyComplete"] else PARTIAL
    return rows, {"class": "owner-terms", "state": state,
                  "code": None if state == MEASURED else "pr-history-incomplete"}


def _large(root: str):
    code, out = _git(root, "ls-tree", "-r", "-l", "-z", "HEAD")
    if code != 0:
        return [], {"class": "large-generated", "state": NOT_MEASURED, "code": "pr-tree-unreadable"}
    rows = []
    for entry in out.split("\0"):
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) != 4 or parts[1] != "blob":
            continue
        segments = path.split("/")
        if parts[3].isdigit() and int(parts[3]) > LARGE_BYTES:
            rows.append(_finding("pr-large-file", "large-generated", "warning", location(path)))
        elif GENERATED_DIRS & set(segments[:-1]) or path.endswith(GENERATED_SUFFIXES):
            rows.append(_finding("pr-generated-artifact", "large-generated", "warning", location(path)))
    return rows, {"class": "large-generated", "state": MEASURED, "code": None}


def load_evidence(path: str | None) -> tuple[dict[str, Any], str | None]:
    """The owner's structured legal evidence, or the fixed code that says why it is unusable."""
    if path is None:
        return {}, None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, "pr-evidence-unreadable"
    return (data, None) if isinstance(data, dict) else ({}, "pr-evidence-unreadable")


def _filled(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _legal(evidence: dict[str, Any], evidence_code: str | None, key: str, cls: str,
           clean: tuple[str, ...], failing: str, code: str, cleared_by: str | None = None):
    """A reviewed owner statement; ``cleared_by`` names the field that lifts the failing state."""
    if evidence_code:
        return [], {"class": cls, "state": NOT_MEASURED, "code": evidence_code}
    entry = evidence.get(key)
    if entry is None:
        return [], {"class": cls, "state": NOT_MEASURED, "code": "pr-evidence-absent"}
    if not isinstance(entry, dict) or entry.get("state") not in (*clean, failing):
        return [], {"class": cls, "state": NOT_MEASURED, "code": "pr-evidence-ambiguous"}
    if not _filled(entry.get("reviewedBy")):
        return [], {"class": cls, "state": NOT_MEASURED, "code": "pr-evidence-unreviewed"}
    blocked = entry["state"] == failing and not (cleared_by and _filled(entry.get(cleared_by)))
    rows = [_finding(code, cls, "blocker")] if blocked else []
    return rows, {"class": cls, "state": MEASURED, "code": None}


def assess(root: str, evidence: str | None = None, timeout: int = DEFAULT_TIMEOUT_S,
           denylist: str | None = None) -> tuple[dict[str, Any], int]:
    """The readiness payload and its typed exit: 0 measured clean · 1 findings · 2 incomplete."""
    root = os.path.realpath(root)
    inv = inventory(root)
    scanners: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    evidence_data, evidence_code = load_evidence(evidence)
    if not inv["git"] or not inv["hasCommits"]:
        reason = inv["code"] if not inv["git"] else "pr-no-commits"
        coverage = [{"class": cls, "state": REFUSED, "code": reason} for cls in CLASSES]
    else:
        for found, cover in (_secrets(root, inv, timeout, scanners), _internal(root, inv),
                             _owner_terms(root, inv, denylist, timeout),
                             _legal(evidence_data, evidence_code, "dependencyLicenses", "dependency-license",
                                    ("compatible",), "incompatible", "pr-license-incompatible"),
                             _attribution(root, inv, timeout, scanners), _large(root),
                             _legal(evidence_data, evidence_code, "historyConsent", "history-consent",
                                    ("granted",), "withheld", "pr-consent-withheld"),
                             _legal(evidence_data, evidence_code, "provenance", "provenance",
                                    ("original", "clean-room"), "derived", "pr-provenance-derived",
                                    cleared_by="authorization")):
            rows += found
            coverage.append(cover)
    rows, truncated = _cap(rows)
    blockers = [r for r in rows if r["severity"] == "blocker"]
    warnings = [r for r in rows if r["severity"] == "warning"]
    incomplete = [c for c in coverage if c["state"] != MEASURED]
    scope = {k: v for k, v in inv.items() if k != "trackedPaths"}
    payload = {
        "ok": not incomplete and not rows,
        "readOnly": True,
        "grantsPublication": False,
        "scope": scope,
        "scanners": scanners,
        "coverage": coverage,
        "blockers": blockers,
        "warnings": warnings,
        "truncated": truncated,
    }
    return payload, 2 if incomplete else (1 if rows else 0)
