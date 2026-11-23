"""The `shared.cache` block: what the `quenching-cache` bar above the prompt reads, through
`cq config show --effective`, to judge a chat's prompt cache.

The defaults live here once, as data: the registry (`config/schema.py`) and the effective view
(`config/effective.py`) read them from this module, and the plugin keeps the same numbers as its
fallback for a session without `cq`. A long chat is not a key of its own: the bar reads
`shared.contextBudgets.main`, the size the main session already plans around."""
from __future__ import annotations

TTL_CHOICES = ("auto", "5", "60")
COST_STYLES = ("dollars", "times")

DEFAULTS = {
    "rebuildLimitUsd": 0.5,
    "warnMinutes": 15,
    "ttlMinutes": "auto",
    "costStyle": "dollars",
}
