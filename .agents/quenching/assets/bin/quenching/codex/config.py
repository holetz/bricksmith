"""The `shared.codex` block of `.agents/quenching.json`, with its defaults.

The block is optional: absent, every default holds and Codex is off — `executor.enabled` is
`false`. A value of the wrong shape falls back to its default and is named in `warnings`, so a typo
never silently changes the threshold a caller relies on.

`executor` configures Codex writing one task's code inside `/quenching-specs execute`; a
repository opts in with `executor.enabled: true`. Codex's native sandbox is not configurable: the
executor always runs with the bypass, inside the worktree, under the git guard.

`crossReview` and `model` belonged to the Codex cross review, which was removed: a leftover value
is ignored and named in `warnings`.
"""
from __future__ import annotations

from quenching.common.config import load_config, namespace

DEFAULTS = {
    "maxUsedPercent": {"primary": 80, "secondary": 90},
    "timeoutSeconds": 900,
    "executor": {
        "enabled": False,
        "modelSmall": "gpt-6.1-sol",
        "modelVerified": "gpt-6.1-sol",
        "modelSmallTier": "gpt-6-luna",
        "reasoningEffort": "medium",
        "maxTaskTokens": 20000,
        "reservePercent": 10,
        "stopLossPercent": 15,
    },
}
RETIRED = ("crossReview", "model")
CHOICES = {
    "reasoningEffort": ("minimal", "low", "medium", "high"),
}


def _percent(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100


def load_settings(root: str) -> tuple[dict, list[str]]:
    """Return the effective settings and the warnings for every value that was ignored."""
    block = namespace(load_config(root, detect_provider_info=False), "shared").get("codex")
    settings = {**DEFAULTS, "maxUsedPercent": dict(DEFAULTS["maxUsedPercent"]),
                "executor": dict(DEFAULTS["executor"])}
    warnings: list[str] = []
    if block is None:
        return settings, warnings
    if not isinstance(block, dict):
        return settings, ["shared.codex is not an object; defaults apply"]

    warnings += [f"shared.codex.{key} is no longer read: the Codex cross review was removed"
                 for key in RETIRED if key in block]
    ceilings = block.get("maxUsedPercent", {})
    if isinstance(ceilings, dict):
        for window in ("primary", "secondary"):
            if window not in ceilings:
                continue
            if _percent(ceilings[window]):
                settings["maxUsedPercent"][window] = ceilings[window]
            else:
                warnings.append(f"shared.codex.maxUsedPercent.{window} must be 0-100; "
                                f"{DEFAULTS['maxUsedPercent'][window]} applies")
    else:
        warnings.append("shared.codex.maxUsedPercent is not an object; defaults apply")
    if "timeoutSeconds" in block:
        value = block["timeoutSeconds"]
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            settings["timeoutSeconds"] = value
        else:
            warnings.append("shared.codex.timeoutSeconds must be a positive integer; "
                            f"{DEFAULTS['timeoutSeconds']} applies")
    if "executor" in block:
        warnings += _executor(block["executor"], settings["executor"])
    return settings, warnings


def _executor(block, executor: dict) -> list[str]:
    """Apply the declared `shared.codex.executor` keys onto `executor`; return the warnings."""
    if not isinstance(block, dict):
        return ["shared.codex.executor is not an object; defaults apply"]
    warnings: list[str] = []
    for key, default in DEFAULTS["executor"].items():
        if key not in block:
            continue
        value = block[key]
        if key == "enabled":
            valid = isinstance(value, bool)
            rule = "true or false"
        elif key in CHOICES:
            valid = value in CHOICES[key]
            rule = "one of " + ", ".join(repr(c) for c in CHOICES[key])
        elif key == "maxTaskTokens":
            valid = isinstance(value, int) and not isinstance(value, bool) and value > 0
            rule = "a positive integer"
        elif key in ("reservePercent", "stopLossPercent"):
            valid = _percent(value)
            rule = "0-100"
        else:
            valid = isinstance(value, str) and bool(value.strip())
            value = value.strip() if valid else value
            rule = "a name"
        if valid:
            executor[key] = value
        else:
            warnings.append(f"shared.codex.executor.{key} must be {rule}; {default!r} applies")
    return warnings
