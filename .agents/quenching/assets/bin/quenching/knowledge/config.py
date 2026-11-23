"""What the target repo declares about this checker, and the two reads that find it.

Moved verbatim out of the pre-refactor OKF validator script.

Config (target repo's `.agents/quenching.json` `shared.okfValidate`, over `.claude/hooks/hooks-config.json` + `hooks-config.local.json`, block
`okfValidate`): `warnAsError`, `ignoreGlobs`. An absent config uses the defaults below; an unreadable, invalid or non-object one is refused.
The file and directory keep the `hooks` name for the two keys that survive it — the plugin's
self-installed hook that once read the rest of this block (`enabled`, `blockOnFail`, `hardBlock`,
`deadlineMs`, `stopScan`) was retired, and `warnAsError`/`ignoreGlobs` stayed on the same config
surface rather than move, which would have been a breaking change of its own.

Trust note: this config is executable code with shell privileges. This pillar reads the touched
path/content and prints; it writes nothing. In **CLI mode** it additionally shells out to
`git log`/`git rev-parse` (read-only, timeout-bounded) for `stale-doc`. Version and review it like
infra.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

DEFAULTS: dict = {
    "warnAsError": False,       # CLI: treat warnings as failures (exit 1)
    "ignoreGlobs": [],          # bundle-relative dir-prefix/fnmatch globs pruned from every scan
}


class ConfigError(Exception):
    """A declared config file that cannot be honoured; the caller refuses, never defaults."""


def _load_config(project_dir: str) -> dict:
    cfg = dict(DEFAULTS)
    for name in ("hooks-config.json", "hooks-config.local.json"):
        path = os.path.join(project_dir, ".claude", "hooks", name)
        if not pathlib.Path(path).exists():
            continue
        try:
            with pathlib.Path(path).open(encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ConfigError(f"{path} is unreadable or not valid UTF-8 JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{path} is not a JSON object")
        section = data.get("okfValidate")
        if isinstance(section, dict):
            cfg.update(section)
    return _apply_shared(project_dir, cfg)


def _apply_shared(project_dir: str, cfg: dict) -> dict:
    """Lay `shared.okfValidate` of `.agents/quenching.json` over the hooks-config layers.

    Key by key: a leaf of the wrong shape is ignored and the lower layer keeps the key.
    """
    from quenching.common.config import setting
    checks = {"warnAsError": lambda v: isinstance(v, bool),
              "ignoreGlobs": lambda v: isinstance(v, list) and all(isinstance(g, str) for g in v)}
    for key, check in checks.items():
        value, warning = setting(project_dir, "shared", f"okfValidate.{key}", cfg[key], check)
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
        cfg[key] = value
    return cfg


def _project_dir() -> str:
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
