"""The leak gate: what must never reach a commit, a push or the specs store.

Three pattern sets, one scanner:

* the GENERIC patterns below, safe to publish (Brazilian tax ids, Azure Databricks workspace
  hosts, Databricks personal access tokens, classic cluster ids, private keys, provider tokens with
  a fixed prefix, and hidden Unicode — zero-width, bidi and tag characters);
* the PATH patterns, built at run time from `$HOME` — the home directory itself and the slug
  Claude Code derives from it for project and scratchpad directories. No user name is ever written
  into this file, and a machine with another `$HOME` finds nothing that only exists on this one;
* a private DENYLIST of organization-specific terms, one case-insensitive regex per line, read from
  `$QUENCHING_DENYLIST_FILE` or `~/.config/quenching/denylist.txt`. It lives outside the repository
  on purpose: committing it would leak the very terms it protects.

The hooks fail closed without the denylist (`DenylistMissing`), unless the caller asks for the
generic patterns only (CI, which never sees the denylist). The specs store guard uses the denylist
when it exists and the generic patterns always: it runs in every repository the plugin is installed
in, and requiring a private file there would refuse every write of every other user.

A path has a known relative form, so the store guard rewrites it before scanning (`normalize_paths`);
a generic or denylist hit has none, so it is refused. A finding is fixed by removing the content,
never by weakening a pattern.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

DENYLIST_ENV = "QUENCHING_DENYLIST_FILE"
ZERO_SHA = "0" * 40
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
EXCERPT_CHARS = 160

GENERIC_PATTERNS: dict[str, re.Pattern[str]] = {
    "brazilian-cpf": re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    "brazilian-cnpj": re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b"),
    "azure-databricks-host": re.compile(r"adb-\d{6,}\.\d+\.azuredatabricks\.net", re.IGNORECASE),
    "databricks-pat": re.compile(r"\bdapi[0-9a-f]{32}\b"),
    "classic-cluster-id": re.compile(r"\b\d{4}-\d{6}-[a-z0-9]{8}\b"),
    "private-key": re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----"),
    # Provider tokens with a fixed prefix: the prefix makes a match near-certain, so these stay
    # precise where a generic `password=` or `token:` pattern would refuse ordinary prose.
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})"),
    "aws-access-key-id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "anthropic-api-key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "openai-project-key": re.compile(r"\bsk-proj-[A-Za-z0-9_-]{40,}"),
    "slack-token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    "gitlab-token": re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),
    # Characters a reader cannot see but a model reads: zero-width and bidi controls, the BOM
    # anywhere but where an editor puts it, and the Unicode tag block that smuggles ASCII. Every
    # command body, reference and imported doc is loaded straight into an agent's context, so a
    # hidden instruction there is an injection no review catches by eye. ZWJ (U+200D) is left
    # out: it joins emoji sequences in ordinary prose.
    "hidden-unicode": re.compile(
        "[\u200b\u200c\u200e\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069"
        "\U000e0000-\U000e007f]|(?!^)\ufeff"),
}

# A Claude Code session scratchpad: `/tmp/claude-<uid>/<project slug>/<session>/scratchpad`.
_SCRATCHPAD = re.compile(
    r"(?:/private)?/tmp/claude-\d+/[^/\s`'\")\]]+(?:/[0-9a-fA-F-]{36})?(?:/scratchpad)?")


# A slug starts a path segment, so it never follows a word character, a dot or a dash: for a root
# home it would otherwise match a `--root` flag or the tail of `temp-root`.
_SLUG_LEFT = r"(?<![\w.-])"


class DenylistMissing(Exception):
    """The denylist the caller requires is not on disk."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"denylist not found at {path}")
        self.path = path


@dataclass(frozen=True)
class Finding:
    """One pattern match, located well enough to fix it."""

    where: str
    line: int
    label: str
    excerpt: str

    def render(self) -> str:
        return f"{self.where}:{self.line}: [{self.label}] {self.excerpt}"

    def as_dict(self) -> dict:
        return {"where": self.where, "line": self.line, "label": self.label,
                "excerpt": self.excerpt}


def denylist_path() -> Path:
    raw = os.environ.get(DENYLIST_ENV, "")
    return Path(raw) if raw else Path.home() / ".config" / "quenching" / "denylist.txt"


def load_denylist(path: Path) -> dict[str, re.Pattern[str]]:
    """One regex per line; blank lines and `#` comments are ignored."""
    patterns: dict[str, re.Pattern[str]] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if line and not line.startswith("#"):
            patterns[f"denylist:{number}"] = re.compile(line, re.IGNORECASE)
    return patterns


def _home() -> str:
    home = os.environ.get("HOME", "").rstrip("/\\")
    return home if len(home) > 1 else ""


def _home_slug(home: str) -> str:
    """The prefix Claude Code gives a project directory under `home`: every `/`, `\\`, `:` and
    `.` becomes `-`, one for one (`session.transcript.encode_cwd`): `/a/b.c` → `-a-b-c`."""
    return "".join("-" if c in "\\/:." else c for c in home)


def path_patterns() -> dict[str, re.Pattern[str]]:
    """The home directory and its slug, as this process sees them; empty without a `$HOME`."""
    home = _home()
    if not home:
        return {}
    return {
        "home-path": re.compile(re.escape(home) + r"(?![\w.-])"),
        "home-slug": re.compile(_SLUG_LEFT + re.escape(_home_slug(home)) + r"(?![\w.])"),
    }


def patterns(*, generic_only: bool = False, require_denylist: bool = True) -> dict[str, re.Pattern[str]]:
    """The patterns a scan applies. Raises `DenylistMissing` when the denylist is required and
    absent; `generic_only` skips it entirely."""
    found = dict(GENERIC_PATTERNS) | path_patterns()
    if generic_only:
        return found
    path = denylist_path()
    if path.is_file():
        return found | load_denylist(path)
    if require_denylist:
        raise DenylistMissing(path)
    return found


def scan_text(where: str, text: str, pats: dict[str, re.Pattern[str]]) -> list[Finding]:
    """Every pattern match in `text`, one finding per line and label."""
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        matched = [label for label, pattern in pats.items() if pattern.search(line)]
        if matched:
            excerpt = _masked(line, pats).strip()[:EXCERPT_CHARS]
            findings.extend(Finding(where, number, label, excerpt) for label in matched)
    return findings


def _masked(line: str, pats: dict[str, re.Pattern[str]]) -> str:
    """`line` with every non-empty span any pattern matches replaced by a mask."""
    spans = sorted((m.start(), m.end()) for pattern in pats.values()
                   for m in pattern.finditer(line) if m.end() > m.start())
    out, cursor = [], 0
    for start, end in spans:
        if end <= cursor:
            continue
        out.append(line[cursor:max(start, cursor)])
        out.append("***")
        cursor = end
    out.append(line[cursor:])
    return "".join(out)


def normalize_paths(text: str, root: str | None = None) -> str:
    """Rewrite the absolute paths that have a known relative form: the scratchpad to
    `<scratchpad>`, the repository root to a relative path, the home directory to `~`, and the
    home slug of a Claude Code project directory to `<project>`."""
    text = _SCRATCHPAD.sub("<scratchpad>", text)
    if root:
        # The root as given and as `abspath` spells it (`/r` is `C:\r` on Windows), each with
        # either separator: a Windows path reaches a text as `C:\r\x` or as `C:/r/x`.
        tops = set()
        for top in (root, os.path.abspath(root)):
            top = top.rstrip("/\\")
            if len(top) > 1:
                tops |= {top, top.replace("\\", "/"), top.replace("/", "\\")}
        for top in sorted(tops, key=len, reverse=True):
            text = re.sub(re.escape(top) + r"[/\\]", "", text)
            text = re.sub(re.escape(top) + r"(?![\w./\\-])", ".", text)
    home = _home()
    if home:
        text = re.sub(re.escape(home) + r"(?![\w.-])", "~", text)
        text = re.sub(_SLUG_LEFT + re.escape(_home_slug(home)) + r"(?:-[^/\s`'\")\]]*)?(?![\w.])", "<project>", text)
    return text


def guard_text(text: str, root: str | None = None,
               previous: str | None = None) -> tuple[str, list[Finding]]:
    """The specs store guard: normalize the paths, then scan what is left with the generic
    patterns, the path patterns and — when it exists — the denylist.

    With the `previous` document, only the lines it does not already hold are judged, the
    model `pre-push` follows: a legacy spec that predates the gate stays writable, and what a
    write adds is still refused."""
    clean = normalize_paths(text, root)
    findings = scan_text("spec", clean, patterns(require_denylist=False))
    if previous is not None and findings:
        held = set(normalize_paths(previous, root).splitlines())
        lines = clean.splitlines()
        findings = [f for f in findings if lines[f.line - 1] not in held]
    return clean, findings


def store_refusal(findings: list[Finding]) -> dict:
    """The ready-to-emit refusal a store write gets when `guard_text` found anything."""
    first = findings[0]
    return {
        "code": "sp-leak", "exit": 2,
        "findings": [f.as_dict() for f in findings],
        "message": f"the text matches the leak gate ([{first.label}] at line {first.line}"
                   f"{f' and {len(findings) - 1} more' if len(findings) > 1 else ''}); "
                   "remove the content and write again — nothing was written",
    }


def guard_store_write(text: str, root: str | None, previous: str | None,
                      refusal: type[Exception]) -> str:
    """The text a store write may land, or `refusal(store_refusal(...))` raised before anything
    is written — `refusal` is the caller's own exception type, so this module needs no backend."""
    clean, findings = guard_text(text, root, previous)
    if findings:
        raise refusal(store_refusal(findings))
    return clean


def _git(root: str, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def _is_binary(blob: bytes) -> bool:
    return b"\0" in blob[:8192]


def scan_staged(root: str, pats: dict[str, re.Pattern[str]]) -> list[Finding]:
    """The index version of every staged file and the email that will sign the commit."""
    email = subprocess.run(["git", "config", "user.email"], cwd=root, capture_output=True,
                           text=True, encoding="utf-8", errors="replace").stdout
    findings = scan_text("git config user.email", email, pats)
    for path in _git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z").split("\0"):
        if not path:
            continue
        blob = subprocess.run(["git", "show", f":{path}"], cwd=root, check=True,
                              capture_output=True).stdout
        if not _is_binary(blob):
            findings.extend(scan_text(path, blob.decode("utf-8", errors="replace"), pats))
    return findings


def _known_commit(root: str, sha: str) -> bool:
    """Whether `sha` is a full object name this clone resolves to a commit."""
    if not _SHA.fullmatch(sha):
        return False
    return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=root,
                          capture_output=True).returncode == 0


class PushInputInvalid(subprocess.CalledProcessError):
    """A `pre-push` stdin field that is not a full object name. A `CalledProcessError`, so every
    verb that already refuses a failed git call refuses it too (exit 2), and the push is blocked
    rather than scanned over a set the field rewrote."""

    def __init__(self, field: str, value: str):
        super().__init__(2, ["pre-push"], stderr=f"pre-push stdin carries a {field} that is not "
                                                 f"a full object name: {value!r}")


def _is_zero(sha: str) -> bool:
    """git's null object name in either object format: 40 zeros (sha1) or 64 (sha256)."""
    return bool(_SHA.fullmatch(sha)) and not sha.strip("0")


def _commits_to_push(root: str, local_sha: str, remote_sha: str,
                     remote: str | None = None) -> list[str]:
    """The commits a push publishes: what the destination's remote-tracking refs and the ref's old
    tip do not have yet, so a merge of an already published base is not judged again. Without the
    destination's name only the old tip is excluded, and a new ref is judged whole: a commit
    already on another remote may still be new to this one. An old tip this clone does not have
    (the remote moved and nothing was fetched) is judged as a new ref, so the cut is never
    narrower than with no old tip at all. A local sha that is not a full object name refuses
    (`PushInputInvalid`): it never reaches `git rev-list` as an option or a revision."""
    if _is_zero(local_sha):
        return []
    if not _SHA.fullmatch(local_sha):
        raise PushInputInvalid("local sha", local_sha)
    if not _is_zero(remote_sha) and _known_commit(root, remote_sha):
        revs = [local_sha, "--not", remote_sha]
        if remote:
            revs.append(f"--remotes={remote}")
    elif remote:
        revs = [local_sha, "--not", f"--remotes={remote}"]
    else:
        revs = [local_sha]
    return _git(root, "rev-list", *revs).split()


def _added_lines(patch: str) -> list[str]:
    """The `+` rows of a patch, without its `+++ <path>` headers: a header follows `--- `, so an
    added line that itself starts with `++` is still judged."""
    added, previous = [], ""
    for row in patch.splitlines():
        if row.startswith("+") and not (row.startswith("+++ ") and previous.startswith("--- ")):
            added.append(row[1:])
        previous = row
    return added


def scan_push(root: str, hook_input: str, pats: dict[str, re.Pattern[str]],
              remote: str | None = None) -> list[Finding]:
    """Emails, messages and added lines of every commit named by `pre-push`'s stdin lines
    (`<local ref> <local sha> <remote ref> <remote sha>`); `remote` is the destination's name,
    the hook's first argument."""
    findings: list[Finding] = []
    seen: set[str] = set()
    for line in hook_input.splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        for sha in _commits_to_push(root, fields[1], fields[3], remote):
            if sha in seen:
                continue
            seen.add(sha)
            header = _git(root, "show", "-s", "--format=%ae%n%ce%n%B", sha)
            findings.extend(scan_text(f"commit {sha[:12]} (header)", header, pats))
            added = _added_lines(
                _git(root, "show", "--format=", "--unified=0", "--no-color", sha))
            findings.extend(scan_text(f"commit {sha[:12]} (diff)", "\n".join(added), pats))
    return findings


def scan_all(root: str, pats: dict[str, re.Pattern[str]]) -> list[Finding]:
    """Every tracked file as it is in the working tree."""
    findings: list[Finding] = []
    for path in _git(root, "ls-files", "-z").split("\0"):
        file = Path(root, path)
        if not path or not file.is_file():
            continue
        blob = file.read_bytes()
        if not _is_binary(blob):
            findings.extend(scan_text(path, blob.decode("utf-8", errors="replace"), pats))
    return findings
