"""Prices: built-in table, merged with the user's ~/.sessioncost/prices.json, then with --prices <file>."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .model import Usage

HERE = Path(__file__).parent
USER_FILE = Path.home() / ".sessioncost" / "prices.json"
CLASSES = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        if k == "models" and isinstance(v, dict):
            models = {m: dict(r) for m, r in base.get("models", {}).items()}
            for m, r in v.items():
                models[m] = {**models.get(m, {}), **r}
            out["models"] = models
        else:
            out[k] = v
    return out


def load(extra_file: str | None = None, user_file: Path | None = USER_FILE) -> dict:
    prices = json.loads((HERE / "prices.json").read_text(encoding="utf-8"))
    for f in (user_file, Path(extra_file).expanduser() if extra_file else None):
        if f and Path(f).is_file():
            prices = _merge(prices, json.loads(Path(f).read_text(encoding="utf-8")))
    return prices


def normalise(model: str) -> str:
    """'claude-opus-5[1m]' -> 'claude-opus-5'; 'claude-haiku-4-5-20251001' -> 'claude-haiku-4-5'."""
    m = re.sub(r"\[.*?\]", "", (model or "").strip().lower())
    return re.sub(r"-\d{8}$", "", m)


def rates(model: str, prices: dict) -> tuple[dict, bool, str]:
    """(rates per million, checked?, the price-table key used)."""
    table = prices["models"]
    for key in (model, normalise(model)):
        if key in table:
            r = table[key]
            return r, bool(r.get("checked")), key
    m = normalise(model)
    key = "default-openai" if re.match(r"(gpt|codex|o\d)", m) else "default-gemini" if m.startswith("gemini") else ""
    key = key if key in table else "default"
    return table[key], False, key


def cost(u: Usage, r: dict) -> dict:
    """USD per token class for one call. Every token is counted in exactly one class."""
    return {
        "input": u.fresh_input * r["input"] / 1e6,
        "cache_write_5m": u.cache_write_5m * r["cache_write_5m"] / 1e6,
        "cache_write_1h": u.cache_write_1h * r["cache_write_1h"] / 1e6,
        "cache_read": u.cache_read * r["cache_read"] / 1e6,
        "output": u.output * r["output"] / 1e6,
    }


def new_input_rate(u: Usage, r: dict) -> float:
    """Average USD per million for the new (not re-read) input of this call."""
    n = u.new_input
    if not n:
        return r["cache_write_1h"] if u.cache_read else r["input"]
    return (u.fresh_input * r["input"] + u.cache_write_5m * r["cache_write_5m"]
            + u.cache_write_1h * r["cache_write_1h"]) / n
