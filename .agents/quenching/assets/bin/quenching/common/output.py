"""The one exit-code contract, and the four doors a run leaves by.

    0  ok        the verb answered
    1  findings  the verb answered, and what it found fails the gate
    2  refusal   the verb did not answer, and says why
    3  usage     the invocation could not be parsed

Before this module the contract was declared in three docstrings and implemented in none: only
the pre-refactor session script named the three steps as constants, the pre-refactor OKF
validator script had no refusal step at all (not one `return 2` in 1,372 lines), and the three
scripts disagreed on the trivial case of being run with no subcommand — 1, 2 and 0 respectively,
from the same contract. Naming the steps here
is what turns the contract into something a caller can branch on, which is what every command
body already assumes it is doing.

A REFUSAL IS NOT A FINDING. The distinction is the whole reason the ladder has three steps rather
than two: `1` says the verb looked and did not like what it saw, `2` says the verb declined to
look. A conductor that cannot tell them apart retries the one case that will never succeed.

Finding codes are stable machine keys in the form `<prefix>-<name>`. The prefix identifies the
owning pillar or adapter (`sp`, `design`, `sk`, `ct`, `op`, `pf`, `delivery`, `tc`, `security`,
`git`, `site`, `nav`, or `glossary`); the name identifies the predicate. Human wording may evolve,
but a code does not, because callers filter on it.
"""

import argparse
import json
import sys

OK, FINDINGS, REFUSAL, USAGE = 0, 1, 2, 3


class NoAbbrevParser(argparse.ArgumentParser):
    """Every `cq` parser: a long option is matched whole, never by a prefix.

    With `allow_abbrev` on, `--w` reads as `--write`, so a grant or a block that matches the
    command text never sees the flag that runs. `add_subparsers` builds each subparser with
    `type(self)`, so the default reaches the whole tree from its root."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)


class CQArgumentParser(NoAbbrevParser):
    """The parser shared by entry points that own the CQ usage contract.

    ``argparse`` uses exit 2 for malformed syntax, but CQ reserves 2 for a deliberate refusal
    after a command has been selected. Keeping the normal usage/error rendering while changing
    only the exit door lets callers distinguish those cases without making a second parser.
    """

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(USAGE, f"{self.prog}: error: {message}\n")


def emit(as_json: bool, obj: dict, human: str, prefix: str = "") -> None:
    """One payload, two renderings, chosen by the caller and never by the payload.

    `prefix` is for a caller that has something to say ahead of the human rendering and nothing
    to add to the JSON — the specs pillar prints its resolution receipt there. It is a parameter
    rather than module state because the receipt belongs to the pillar that resolves slugs, and a
    module every pillar imports has no business holding one pillar's mutable answer."""
    if as_json:
        print(json.dumps(obj, indent=2, ensure_ascii=False))
    else:
        print(prefix + human)


def emit_err(as_json: bool, err: dict, prefix: str = "") -> int:
    """An error payload, and the step it exits by.

    `exit` is carried inside `err` and stripped from what is printed: the step is a property of
    the error, decided where the error is raised and known there. It defaults to FINDINGS, so a
    refusal is the case that has to say so — which is the right way round, because a verb that
    refuses knows it, and a verb that merely found something does not have to remember."""
    if err.get("exit", FINDINGS) == REFUSAL:
        return refuse({k: v for k, v in err.items() if k != "exit"}, as_json)
    emit(as_json, {"ok": False, **{k: v for k, v in err.items() if k != "exit"}},
         f"error: {err['message']}", prefix)
    return err.get("exit", FINDINGS)


def refuse(payload: dict, as_json: bool) -> int:
    """Every refusal leaves by this door: exit 2, a code, and a stated reason.

    The human rendering goes to stderr, because a refusal is not an answer and a caller piping
    stdout is collecting answers."""
    if as_json:
        print(json.dumps({"ok": False, **payload}, indent=2, ensure_ascii=False))
    else:
        print(f"refused: {payload['message']}", file=sys.stderr)
    return REFUSAL


def finding(code: str, severity: str, message: str, **extra) -> dict:
    f = {"code": code, "severity": severity, "message": message}
    f.update(extra)
    return f


def finding_code(prefix: str, name: str) -> str:
    """Build one stable `<prefix>-<name>` finding key at the point that declares it."""
    prefix = prefix.strip()
    name = name.strip()
    if not prefix or not name or "-" in prefix:
        raise ValueError("finding codes require a non-empty hyphen-free prefix and name")
    return f"{prefix}-{name}"


def exit_for(findings: list[dict]) -> int:
    """Errors are what the exit code reports; warnings are reported and do not fail the run.

    This is what lets a conductor gate on an exit code without carrying a severity table of its
    own — and it is why `stale-doc`, a warning by construction, can never turn a sweep red."""
    return FINDINGS if any(f["severity"] == "error" for f in findings) else OK


def report_findings(as_json: bool, header: str, payload: dict, findings: list[dict],
                    label_key: str = "subject") -> int:
    errors = sum(1 for f in findings if f["severity"] == "error")
    if as_json:
        print(json.dumps({"ok": errors == 0, **payload, "findings": findings},
                         indent=2, ensure_ascii=False))
    else:
        print(f"{header} ({errors} error(s), {len(findings) - errors} warning(s))")
        for f in findings:
            print(f"  [{f['severity']:<5}] {f.get(label_key, '-')}: {f['message']}  ({f['code']})")
        if not findings:
            print("  OK — no findings.")
    return exit_for(findings)
