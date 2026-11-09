"""Check 8 (1.1): report-only check when a top-tier model is used for many small mechanical steps."""
from __future__ import annotations

from .. import prices as pricemod
from ..model import Call, Fix, Session
from .common import turns_where

MIN_MECHANICAL_CALLS = 5
MIN_SAVING_USD = 0.20
MAX_MECHANICAL_OUTPUT = 400
MAX_MECHANICAL_THINKING = 200


def _cheaper_peer(model: str) -> str | None:
    m = (model or "").lower()
    if any(k in m for k in ("opus-5", "fable", "mythos")):
        return "claude-sonnet-5-5"
    if "opus-4" in m or "opus" in m:
        return "claude-sonnet-4-6"
    if "gpt-6-astra" in m:
        return "gpt-6-sol"
    return None


def _is_mechanical(c: Call) -> bool:
    if not c.actions:
        return False
    kinds = {a.kind for a in c.actions}
    if not kinds.issubset({"read", "search", "command"}):
        return False
    if c.usage.output > MAX_MECHANICAL_OUTPUT:
        return False
    if (c.usage.thinking or 0) > MAX_MECHANICAL_THINKING:
        return False
    return True


def model_choice(s: Session, pr) -> Fix | None:
    calls = s.calls
    if len(calls) < MIN_MECHANICAL_CALLS:
        return None
    hit_calls: list[tuple[str, int]] = []
    turns_set: set[int] = set()
    items: list[str] = []
    total_diff = 0.0
    src_model = ""
    dst_model = ""

    for c in calls:
        peer = _cheaper_peer(c.model)
        if not peer or not _is_mechanical(c):
            continue
        cur_usd = pr.total(c)
        peer_rates, _, _ = pricemod.rates(peer, pr.prices)
        peer_rates = pricemod.for_prompt(peer_rates, c.usage.context)
        peer_usd = sum(pricemod.cost(c.usage, peer_rates).values())
        diff = max(0.0, cur_usd - peer_usd)
        if diff <= 0:
            continue
        src_model = src_model or c.model
        dst_model = dst_model or peer
        total_diff += diff
        turns_set.add(c.turn)
        hit_calls.append(("main", c.n))
        acts = ", ".join(a.label for a in c.actions[:2])
        items.append(f"call #{c.n} (turn {c.turn}) — {acts} (saved ${diff:.2f} on {peer})")

    if len(hit_calls) < MIN_MECHANICAL_CALLS or total_diff < MIN_SAVING_USD:
        return None
    turns = sorted(turns_set)
    n = len(hit_calls)
    return Fix(
        id="model-choice",
        title=f"{n} routine read/search/command steps ran on {src_model} (${total_diff:.2f} cheaper on {dst_model})",
        why=(f"{n} calls in this chat only read files, searched, or ran a quick command with short output, "
             f"where {dst_model} costs much less per cached token than {src_model}."),
        saving_usd=total_diff, saving_tokens=0,
        where=turns_where(turns), turns=turns, items=items[:6],
        per_turn={t: 0 for t in turns}, calls=hit_calls,
        how=f"Use `/model {dst_model}` (or a subagent on {dst_model}) for routine file reading, searching, and test runs, and switch to {src_model} for hard design or debugging.",
        paste=False,
    )
