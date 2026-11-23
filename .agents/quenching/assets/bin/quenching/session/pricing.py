"""The model price table quenching owns, read by `cq components session cost|cache --usd` and
copied, generated, into the `quenching-cache` plugin (`integrations/claude-cache/hooks/prices.ts`).

`pricing.json` is the one source. It holds Anthropic list prices per million tokens for input and
cache reads, keyed by exact `family-version` (`opus-5-5`), plus the multipliers every model shares:
a cache write costs `cacheWrite` × input, output costs `output` × input, a `-fast` deployment costs
`fast` ×, and a model under `longPrompt` costs its multiplier × above that many prompt tokens.

An unknown model prices as `None`, never as a neighbour's rate: `claude-opus-5-6` is not Opus 5.
The figures are list prices, an estimate for comparison: a gateway or a subscription bills
otherwise, which is why no command prices anything unless asked with `--usd`."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

TABLE = Path(__file__).with_name("pricing.json")
MODEL = re.compile(r"claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?=$|-\d{8}|\[|@|-v\d|-fast|:)")


@lru_cache(maxsize=1)
def table() -> dict:
    return json.loads(TABLE.read_text(encoding="utf-8"))


def model_key(model: str) -> str | None:
    """`claude-opus-5-5-20260401` → `opus-5-5`; an old-style id (`claude-3-7-sonnet`) → None."""
    m = MODEL.search((model or "").lower())
    if not m:
        return None
    return f"{m[1]}-{m[2]}-{m[3]}" if m[3] else f"{m[1]}-{m[2]}"


def rates(model: str, prompt_tokens: int = 0) -> dict | None:
    """USD per million tokens for this model and prompt size, or None for an unpriced model."""
    data = table()
    key = model_key(model)
    row = data["models"].get(key) if key else None
    if row is None:
        return None
    factor = data["fast"] if "-fast" in model.lower() else 1
    long = data["longPrompt"].get(key)
    if long and prompt_tokens > long["above"]:
        factor *= long["multiplier"]
    base = row["input"] * factor
    return {"input": base, "cacheRead": row["cacheRead"] * factor,
            "write5m": base * data["cacheWrite"]["5m"], "write1h": base * data["cacheWrite"]["1h"],
            "output": base * data["output"]}


def usd(model: str, *, input: int = 0, cache_read: int = 0, write5m: int = 0, write1h: int = 0,
        write_other: int = 0, output: int = 0, prompt_tokens: int = 0) -> float | None:
    """List-price USD for these token counts. `write_other` is a cache write whose lifetime the
    record did not say; it prices at the 5-minute rate, the floor."""
    r = rates(model, prompt_tokens)
    if r is None:
        return None
    total = (input * r["input"] + cache_read * r["cacheRead"] + (write5m + write_other) * r["write5m"]
             + write1h * r["write1h"] + output * r["output"])
    return round(total / 1e6, 6)


def problems(data: dict | None = None) -> list[str]:
    """What makes the table unusable: the gate refuses a table with any."""
    data = table() if data is None else data
    out = []
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(data.get("sourceDate", ""))):
        out.append("sourceDate is not YYYY-MM-DD")
    models = data.get("models")
    if not isinstance(models, dict) or not models:
        return out + ["models is empty"]
    for key, row in models.items():
        if not re.fullmatch(r"[a-z]+-\d+(?:-\d{1,2})?", key):
            out.append(f"{key}: not a family-version key")
        inp, read = (row or {}).get("input"), (row or {}).get("cacheRead")
        if not all(isinstance(v, (int, float)) and v > 0 for v in (inp, read)):
            out.append(f"{key}: input and cacheRead must be positive numbers")
        elif read >= inp or inp > 100:
            out.append(f"{key}: implausible (cacheRead {read} vs input {inp})")
    for name in ("output", "fast"):
        if not isinstance(data.get(name), (int, float)) or data[name] < 1:
            out.append(f"{name} must be a multiplier of at least 1")
    writes = data.get("cacheWrite") or {}
    if not all(isinstance(writes.get(k), (int, float)) and writes[k] >= 1 for k in ("5m", "1h")):
        out.append("cacheWrite needs 5m and 1h multipliers of at least 1")
    return out
