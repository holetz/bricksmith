"""Block identifiers that must never reach the repository.

Two pattern sets are checked:

* generic patterns, built in and safe to publish (Brazilian tax ids, Azure Databricks workspace
  hosts, personal access tokens, classic cluster ids, private keys);
* a private denylist of organization-specific terms, one case-insensitive regex per line, read
  from ``$BRICKSMITH_DENYLIST_FILE`` or ``~/.config/bricksmith/denylist.txt``. The denylist lives
  outside the repository on purpose: committing it would leak the very terms it protects.

Without the denylist the check fails closed, unless ``--generic-only`` is passed (CI runs that
way, since the denylist never leaves the developer machine).

Modes:

* ``--staged``: the index content of staged files and the configured author email (pre-commit);
* ``--push LOCAL_SHA REMOTE_SHA``: added lines, messages and author/committer emails of the
  commits being pushed (pre-push);
* ``--all``: every tracked file in the working tree.

Exit codes: 0 clean, 1 findings, 2 configuration error.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ZERO_SHA = "0" * 40
DEFAULT_DENYLIST = Path.home() / ".config" / "bricksmith" / "denylist.txt"

GENERIC_PATTERNS: dict[str, re.Pattern[str]] = {
    "brazilian-cpf": re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    "brazilian-cnpj": re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b"),
    "azure-databricks-host": re.compile(r"adb-\d{6,}\.\d+\.azuredatabricks\.net", re.IGNORECASE),
    "databricks-pat": re.compile(r"\bdapi[0-9a-f]{32}\b"),
    "classic-cluster-id": re.compile(r"\b\d{4}-\d{6}-[a-z0-9]{8}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


@dataclass(frozen=True)
class Finding:
    """One pattern match, located well enough to fix it."""

    where: str
    label: str
    excerpt: str

    def render(self) -> str:
        """Format the finding as ``where: [label] excerpt``."""
        return f"{self.where}: [{self.label}] {self.excerpt}"


def git(*args: str) -> str:
    """Run git and return stdout; raise on failure."""
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def load_denylist(path: Path) -> dict[str, re.Pattern[str]]:
    """Read one regex per line; blank lines and ``#`` comments are ignored."""
    patterns: dict[str, re.Pattern[str]] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if line and not line.startswith("#"):
            patterns[f"denylist:{number}"] = re.compile(line, re.IGNORECASE)
    return patterns


def scan_text(where: str, text: str, patterns: dict[str, re.Pattern[str]]) -> list[Finding]:
    """Return every pattern match in ``text``, one finding per line and label."""
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        findings.extend(
            Finding(f"{where}:{number}", label, line.strip()[:160])
            for label, pattern in patterns.items()
            if pattern.search(line)
        )
    return findings


def is_binary(blob: bytes) -> bool:
    """Treat content with a NUL byte as binary and skip it."""
    return b"\0" in blob[:8192]


def scan_staged(patterns: dict[str, re.Pattern[str]]) -> list[Finding]:
    """Scan the index version of staged files and the author email that will sign the commit."""
    findings = scan_text("git config user.email", git("config", "user.email"), patterns)
    for path in git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split("\0"):
        if not path:
            continue
        blob = subprocess.run(["git", "show", f":{path}"], check=True, capture_output=True).stdout
        if not is_binary(blob):
            findings.extend(scan_text(path, blob.decode("utf-8", errors="replace"), patterns))
    return findings


def commits_to_push(local_sha: str, remote_sha: str) -> list[str]:
    """List the commits a push would publish; a new branch publishes what no remote has yet."""
    if local_sha == ZERO_SHA:
        return []
    revs = [local_sha, "--not", "--remotes"] if remote_sha == ZERO_SHA else [f"{remote_sha}..{local_sha}"]
    return git("rev-list", *revs).split()


def scan_push(local_sha: str, remote_sha: str, patterns: dict[str, re.Pattern[str]]) -> list[Finding]:
    """Scan the emails, messages and added lines of every commit about to be pushed."""
    findings: list[Finding] = []
    for sha in commits_to_push(local_sha, remote_sha):
        header = git("show", "-s", "--format=%ae%n%ce%n%B", sha)
        findings.extend(scan_text(f"commit {sha[:12]} (header)", header, patterns))
        added = [
            line[1:]
            for line in git("show", "--format=", "--unified=0", "--no-color", sha).splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        findings.extend(scan_text(f"commit {sha[:12]} (diff)", "\n".join(added), patterns))
    return findings


def scan_all(patterns: dict[str, re.Pattern[str]]) -> list[Finding]:
    """Scan every tracked file as it is in the working tree."""
    findings: list[Finding] = []
    for path in git("ls-files", "-z").split("\0"):
        file = Path(path)
        if not path or not file.is_file():
            continue
        blob = file.read_bytes()
        if not is_binary(blob):
            findings.extend(scan_text(path, blob.decode("utf-8", errors="replace"), patterns))
    return findings


def main(argv: list[str] | None = None) -> int:
    """Run the requested scan and report findings."""
    parser = argparse.ArgumentParser(description="Block identifiers that must never reach the repository.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="scan staged content (pre-commit)")
    mode.add_argument("--push", nargs=2, metavar=("LOCAL_SHA", "REMOTE_SHA"), help="scan commits being pushed")
    mode.add_argument("--all", action="store_true", help="scan every tracked file")
    parser.add_argument("--generic-only", action="store_true", help="skip the private denylist (CI)")
    args = parser.parse_args(argv)

    patterns = dict(GENERIC_PATTERNS)
    if not args.generic_only:
        denylist = Path(os.environ.get("BRICKSMITH_DENYLIST_FILE", DEFAULT_DENYLIST))
        if not denylist.is_file():
            print(f"leak-check: denylist not found at {denylist}; refusing to continue.", file=sys.stderr)
            print("Create it (one regex per line) or set BRICKSMITH_DENYLIST_FILE.", file=sys.stderr)
            return 2
        patterns |= load_denylist(denylist)

    if args.staged:
        findings = scan_staged(patterns)
    elif args.push:
        findings = scan_push(args.push[0], args.push[1], patterns)
    else:
        findings = scan_all(patterns)

    for finding in findings:
        print(finding.render(), file=sys.stderr)
    if findings:
        print(f"leak-check: {len(findings)} finding(s).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
