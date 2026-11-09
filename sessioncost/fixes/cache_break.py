"""Check 1 (1.1): the prompt cache expired or broke mid-chat and the earlier conversation was sent again at full price."""
from __future__ import annotations

from .. import prices as pricemod
from ..model import Call, Fix, Session
from .common import setup_tokens, turns_where

MIN_REWRITTEN_TOKENS = 20_000

_REASON_WORDS = {
    "tools_changed": "the tool definitions changed",
    "system_changed": "the system instructions changed",
    "model_changed": "the model changed mid-chat",
    "messages_changed": "earlier messages in the prompt changed",
    "ttl_expired": "the cache expired after a pause",
    "previous_message_not_found": "the earlier cache entry had expired",
    "unavailable": "the cache was temporarily unavailable",
}
_PAUSE_REASONS = ("", "ttl_expired", "previous_message_not_found", "unavailable")


def _break_reason(prev: Call, cur: Call) -> str:
    if cur.model and prev.model and cur.model != prev.model:
        return f"the model changed from {prev.model} to {cur.model}"
    t0 = prev.ts_last or prev.ts_first
    t1 = cur.ts_first
    if cur.cache_miss_reason in _PAUSE_REASONS and t0 and t1 and (t1 - t0).total_seconds() >= 300:
        mins = int(round((t1 - t0).total_seconds() / 60))
        ttl = "1-hour" if prev.usage.cache_write_1h > 0 and not prev.usage.cache_write_5m else "5-minute"
        return f"after a {mins}-minute pause ({ttl} cache)"
    if cur.cache_miss_reason:
        return _REASON_WORDS.get(cur.cache_miss_reason, cur.cache_miss_reason.replace("_", " "))
    return "the prompt prefix changed or the cache expired"


def cache_break(s: Session, pr) -> Fix | None:
    calls = s.calls
    if not s.tokens_exact or len(calls) < 2:
        return None
    if not any(c.usage.cache_read or c.usage.cache_write for c in calls):
        return None
    st_tok = setup_tokens(s)
    items: list[str] = []
    per_turn: dict[int, int] = {}
    hit_calls: list[tuple[str, int]] = []
    reasons: list[str] = []
    total_tok = 0
    total_usd = 0.0

    for i in range(1, len(calls)):
        prev, c = calls[i - 1], calls[i]
        u = c.usage
        carried_max = max(0, prev.usage.context + prev.usage.output - st_tok)
        setup = min(st_tok, u.context)
        carried = max(0, min(u.context - setup, carried_max))
        cr_left = max(0, u.cache_read - min(setup, u.cache_read))
        h_cr = min(carried, cr_left)
        compacted = carried_max > 20_000 and u.context < carried_max * 0.4
        if not (carried > 2_000 and h_cr < carried / 2 and not compacted):
            continue
        rewritten = carried - h_cr
        r = pr.rates(c)
        extra = rewritten * max(0.0, pricemod.new_input_rate(u, r) - r["cache_read"]) / 1e6
        reason = _break_reason(prev, c)
        reasons.append(reason)
        total_tok += rewritten
        total_usd += extra
        per_turn[c.turn] = per_turn.get(c.turn, 0) + rewritten
        hit_calls.append(("main", c.n))
        items.append(f"call #{c.n} (turn {c.turn}) — ~{rewritten:,} tokens of earlier conversation sent again "
                     f"at the full new-input rate ({reason})")

    if total_tok < MIN_REWRITTEN_TOKENS:
        return None
    turns = sorted(per_turn)
    n = len(hit_calls)
    top_reason = reasons[0] if len(set(reasons)) == 1 else "a pause or a mid-chat setting/tool change"
    r0 = pr.rates(calls[0])
    pct = int(round(100 * r0["cache_read"] / r0["input"])) if r0.get("input") and r0.get("cache_read") else 10
    return Fix(
        id="cache-break",
        title=f"The prompt cache broke {n} time{'s' if n != 1 else ''} (~{total_tok:,} tokens sent again at full price)",
        why=(f"Normally the earlier conversation is re-read from the prompt cache at ~{pct}% of the input price. "
             f"On {n} call{'s' if n != 1 else ''} ({top_reason}), the cache missed and ~{total_tok:,} tokens "
             f"had to be written again at full price."),
        saving_usd=total_usd, saving_tokens=total_tok,
        where=turns_where(turns), turns=turns, items=items[:6], per_turn=per_turn, calls=hit_calls,
        how=("1. Before a long pause, finish the current step or run /compact (or start a new chat) when you return.\n"
             "2. Avoid changing MCP servers, tool sets, or models in the middle of a long chat."),
        paste=False,
    )
