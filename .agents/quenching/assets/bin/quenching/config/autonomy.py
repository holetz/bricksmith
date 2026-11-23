"""The `shared.autonomy` block: how much of the build runs without a human, and how fast.

Defaults reproduce the behaviour that used to be fixed in prose. A value of the wrong shape falls
back to its default and is named in `warnings`, so a typo never silently widens what runs alone.
"""
from __future__ import annotations

COMPLEXITY = ("none", "low", "medium", "high", "xhigh")
MERGE = ("policy", "never", "dependencies", "all")
MAX_WORKERS = 8

DEFAULTS = {
    "runAloneUpTo": "low",
    "merge": "policy",
    "maxWorkers": 3,
    "maxLaunches": 40,
    "livenessMinutes": 20,
}


def _integer(value, low: int, high: int | None = None) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool) and value >= low
            and (high is None or value <= high))


def coerce(block) -> tuple[dict, list[str]]:
    """Return the effective leaves of a `shared.autonomy` block and a warning per ignored value."""
    settings = dict(DEFAULTS)
    if block is None:
        return settings, []
    if not isinstance(block, dict):
        return settings, ["shared.autonomy is not an object; defaults apply"]
    warnings: list[str] = []
    for key, allowed in (("runAloneUpTo", COMPLEXITY), ("merge", MERGE)):
        if key in block:
            if block[key] in allowed:
                settings[key] = block[key]
            else:
                warnings.append(f"shared.autonomy.{key} must be one of {', '.join(allowed)}; "
                                f"{DEFAULTS[key]} applies")
    for key, low, high in (("maxWorkers", 1, MAX_WORKERS), ("maxLaunches", 1, None),
                           ("livenessMinutes", 1, None)):
        if key in block:
            if _integer(block[key], low, high):
                settings[key] = block[key]
            else:
                span = f"{low}-{high}" if high else f">= {low}"
                warnings.append(f"shared.autonomy.{key} must be an integer {span}; "
                                f"{DEFAULTS[key]} applies")
    for key in block:
        if key not in DEFAULTS:
            warnings.append(f"shared.autonomy.{key} is not a known key; ignored")
    return settings, warnings
