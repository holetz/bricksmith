"""cq_calls — the `cq`, `git` and `gh` calls a body tells its reader to run, and the `cq` ones checked
against the argparse parsers that actually answer them.

A command or an agent that cites `cq specs show <id>` keeps working in prose long after the verb
lost that shape; only a run inside an orchestration fails. This module closes that gap without
keeping a second copy of the CLI: the pillar table is read from `assets/bin/cq` and each pillar's
own `build_parser()` is the source of truth for its verbs, its required options and its flags.

WHAT COUNTS AS A CALL. A line inside a fence, or a span between backticks, whose first word (after an
optional `python3`, a `path/to/` prefix, or a `X=$(` opener) is the binary. A negation (`never`,
`do not`, `nunca`, `não`, `without`, `skips`, `no` right before the span) only counts when it precedes the span in the same
clause — no sentence-ending `.`, `;`, `:` or `,` between the two — and within four words of it
("never `cq …`", "no `cq …` grant"); a loose one elsewhere in the sentence hides nothing, `no` before another noun ("no reviewer answered `cq …`") negates nothing, and "never skip `cq …`" is a double negation that requires the call. `e.g.`,
`i.e.` and decimals do not end a clause; a negation closing the previous prose line still counts, and
so does one over a list of spans ("never `a`, `b` or `c`"). A placeholder (`<id>`, `"<title>"`, `…`, `$VAR`) counts as a value, and the number of positionals is
never checked, because prose abbreviates them.

THE GLOBAL OPTION BEFORE THE PILLAR IS READ. `cq --root "$TARGET_ROOT" proof doctor` is the form a
third of the delivery commands use (measured on the corpus when this module was written), so the
extractor skips the top-level parser's `--root <value>` before it reads the pillar.

AN UNKNOWN PILLAR IS AN UNKNOWN VERB OF `cq`. A first word that is neither a pillar nor one of the
commands the launcher answers before its parser (`deploy`, `doctor`, `modules`) is reported, never
skipped: `cq session context` fails at argparse just like `cq specs shw`.
"""
from __future__ import annotations

import argparse
import functools
import importlib
import importlib.machinery
import importlib.util
import os
import re
import shlex
from typing import NamedTuple

NEGATION_RE = re.compile(r"\b(?:never|do not|don't|does not|doesn't|cannot|can't|skips?|without|nunca|não|nao)\b", re.IGNORECASE)
_CLAUSE_END_RE = re.compile(r"[;:,]|\.(?=\s|$)")
_ABBREV_RE = re.compile(r",?\s*\b(?:e\.g\.|i\.e\.)(?=\s|,|$),?", re.IGNORECASE)
_LISTED_RE = re.compile(r"`[^`\n]+`(?:\s*,)?(?:\s+(?:or|and|nor|ou|e)\b)?")  # one item of a negated list
_DOUBLE_NEG_RE = re.compile(r"\b(?:never|do not|don't|does not|doesn't|cannot|can't|not|without|nunca|não|nao)\s+skip\w*\b", re.IGNORECASE)
_NO_RE = re.compile(r"\bno\s*$|\bno\s+(?:longer|more(?!\s+than\b))\b", re.IGNORECASE)  # bare `no` negates only right before the span; `no longer|more` negates the verb after it
_NEGATION_REACH = 4  # words between a negation and the span it negates
_SPLIT_RE = re.compile(r"\s*(?:&&|\|\||;|\s\|\s)\s*")
_INLINE_RE = re.compile(r"`([^`\n]+)`")
_BIN_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WRAPPERS = ("python3", "python", "sudo", "exec")
# Answered by `assets/bin/cq` `main()` before its pillar parser, so they are no pillar of `PILLARS`.
_LAUNCHER_COMMANDS = frozenset({"deploy", "doctor", "modules"})


class Call(NamedTuple):
    line: int
    binary: str
    args: list[str]
    text: str
    inline: bool


class CqIssue(NamedTuple):
    line: int
    kind: str  # "unknown-verb" | "flag"
    call: str
    detail: str


def _is_placeholder(token: str) -> bool:
    return (not token or token[0] in "<$[({@…" or "…" in token or "..." in token
            or "|" in token or token.endswith(">"))


def _clean(token: str) -> str:
    token = token.strip().strip("`")
    token = token.lstrip("[").rstrip("]").rstrip(",")
    if token.endswith(")") and "(" not in token:
        token = token.rstrip(")")
    return token


def _binary_of(token: str, binaries: tuple[str, ...]) -> str | None:
    word = token.rsplit("$(", 1)[-1].rsplit("=", 1)[-1].strip("\"'()`")
    word = word.rsplit("/", 1)[-1]
    return word if word in binaries else None


def _calls_in(text: str, lineno: int, binaries: tuple[str, ...], inline: bool) -> list[Call]:
    out: list[Call] = []
    for part in _SPLIT_RE.split(text.strip()):
        part = part.strip().lstrip("$ ").strip()
        if not part:
            continue
        try:
            tokens = shlex.split(part)
        except ValueError:
            tokens = part.split()
        index = 0
        while index < len(tokens) and tokens[index] in _WRAPPERS:
            index += 1
        if index >= len(tokens):
            continue
        binary = _binary_of(tokens[index], binaries)
        if not binary:
            continue
        args: list[str] = []
        for token in tokens[index + 1:]:
            if token.startswith((">", "2>", "&>")) or token == "<<<":
                break
            token = _clean(token)
            if token:
                args.append(token)
        if args:
            out.append(Call(lineno, binary, args, " ".join([binary, *args]), inline))
    return out


def _negated(line: str, start: int, carried: str = "") -> bool:
    """True when a negation word sits within `_NEGATION_REACH` words before the span at `start`,
    in its own clause; `carried` is the previous prose line, for a sentence the source wrapped."""
    before = _LISTED_RE.sub(" ", (carried + " " if carried else "") + line[:start])
    clause = _CLAUSE_END_RE.split(_ABBREV_RE.sub(" ", before))[-1]
    window = " ".join(_DOUBLE_NEG_RE.sub(" ", clause).split()[-_NEGATION_REACH:])  # "never skip" requires the call
    return bool(NEGATION_RE.search(window) or _NO_RE.search(window))


def extract_calls(body: str, binaries: tuple[str, ...] = ("cq",)) -> list[Call]:
    """Every call to `binaries` the body tells the reader to run, with its body line number."""
    out: list[Call] = []
    fenced = False
    pending: tuple[int, str] | None = None
    carried = ""
    for lineno, line in enumerate(body.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            pending = None
            carried = ""
            continue
        if fenced and pending:
            lineno, stripped = pending[0], pending[1] + " " + stripped
            line = stripped
            pending = None
        if not stripped:
            carried = ""
            continue
        if fenced:
            if stripped.endswith("\\"):
                pending = (lineno, stripped[:-1].rstrip())
            elif not stripped.startswith("#"):
                out.extend(_calls_in(stripped, lineno, binaries, False))
            continue
        for found in _INLINE_RE.finditer(line):
            if not _negated(line, found.start(), carried):
                out.extend(_calls_in(found.group(1), lineno, binaries, True))
        carried = stripped
    return out


@functools.lru_cache(maxsize=1)
def _pillar_modules() -> tuple[dict[str, str], argparse.ArgumentParser]:
    path = os.path.join(_BIN_DIR, "cq")
    loader = importlib.machinery.SourceFileLoader("cq_entry", path)
    spec = importlib.util.spec_from_loader("cq_entry", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return {name: pillar.module for name, pillar in module.PILLARS.items()}, module.build_parser()


@functools.lru_cache(maxsize=None)
def _pillar_parser(name: str) -> argparse.ArgumentParser:
    built = importlib.import_module(_pillar_modules()[0][name]).build_parser()
    return built[0] if isinstance(built, tuple) else built


def _subparsers(parser: argparse.ArgumentParser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _option(parser: argparse.ArgumentParser, flag: str):
    name = flag.split("=", 1)[0]
    exact = [a for a in parser._actions if name in a.option_strings]
    if exact:
        return exact[0]
    if parser.allow_abbrev and name.startswith("--"):
        near = {id(a): a for a in parser._actions
                if any(o.startswith(name) for o in a.option_strings)}
        if len(near) == 1:
            return next(iter(near.values()))
    return None


def _check(parser: argparse.ArgumentParser, tokens: list[str], call: Call,
           path: str) -> list[CqIssue]:
    sub = _subparsers(parser)
    seen: set[int] = set()
    valued = False
    positional = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-") and len(token) > 1 and not _is_placeholder(token):
            action = _option(parser, token)
            if action is None:
                return [CqIssue(call.line, "flag", call.text,
                                f"`{token.split('=', 1)[0]}` is not an option of `{path}`")]
            seen.add(id(action))
            takes_value = action.nargs != 0 and "=" not in token
            if takes_value and index + 1 < len(tokens) and not tokens[index + 1].startswith("-"):
                index += 1
                valued = True
        elif sub is not None:
            if _is_placeholder(token):
                return []
            if token not in sub.choices:
                known = ", ".join(sorted(sub.choices))
                return [CqIssue(call.line, "unknown-verb", call.text,
                                f"`{token}` is not a verb of `{path}` (known: {known})")]
            return _check(sub.choices[token], tokens[index + 1:], call, f"{path} {token}")
        else:
            valued = positional = True
        index += 1
    if sub is not None or (call.inline and not valued):
        return []  # a span naming the verb and its flags cites it; it hands over no values
    missing = [a.option_strings[0] for a in parser._actions
               if a.option_strings and a.required and id(a) not in seen]
    # The spec id is optional to argparse (positional or `--spec`) and enforced after parsing,
    # so a verb that needs it says so through the `id_required` default `add_spec` sets.
    if parser.get_default("id_required") and not positional and not any(
            "--spec" in a.option_strings and id(a) in seen for a in parser._actions):
        missing.append("--spec")
    return [CqIssue(call.line, "flag", call.text,
                    f"`{path}` requires {', '.join(f'`{m}`' for m in missing)}")] if missing else []


def check_cq_calls(body: str) -> list[CqIssue]:
    """Each `cq` call in `body` that no real parser would accept."""
    issues: list[CqIssue] = []
    pillars, top = _pillar_modules()
    for call in extract_calls(body, ("cq",)):
        tokens = list(call.args)
        rooted = False
        while tokens and tokens[0].startswith("-"):
            action = _option(top, tokens[0])
            if action is None or action.nargs == 0:
                tokens = []
                break
            tokens = tokens[1 if "=" in tokens[0] else 2:]
            rooted = True
        if rooted and tokens and tokens[0] == "modules":
            # The launcher answers `modules` only at index 0 (`assets/bin/cq` `main()`).
            issues.append(CqIssue(call.line, "unknown-verb", call.text,
                                  "`modules` is answered only as the first word of `cq`; "
                                  "the launcher refuses it after a global option"))
            continue
        if not tokens or tokens[0] in _LAUNCHER_COMMANDS or _is_placeholder(tokens[0]):
            continue
        if tokens[0] not in pillars:
            known = ", ".join(sorted({*pillars, *_LAUNCHER_COMMANDS}))
            issues.append(CqIssue(call.line, "unknown-verb", call.text,
                                  f"`{tokens[0]}` is not a verb of `cq` (known: {known})"))
            continue
        issues.extend(_check(_pillar_parser(tokens[0]), tokens[1:], call, f"cq {tokens[0]}"))
    return issues
